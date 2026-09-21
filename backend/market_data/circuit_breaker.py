"""
Circuit breaker for market data providers.

A per-provider state machine that fail-fast when a provider is unhealthy:

  CLOSED  → normal operation, calls pass through
  OPEN     → after failure_threshold consecutive failures; calls raise CircuitBreakerOpen immediately
  HALF_OPEN → after recovery_timeout; one test call passes through; success → CLOSED, failure → OPEN

State transitions are logged at INFO. Thread-safe via a lock per breaker instance.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F")  # callable return type


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpen(Exception):
    """Raised immediately when a circuit breaker is OPEN."""

    def __init__(self, provider_name: str, state: CircuitState):
        self.provider_name = provider_name
        self.state = state
        super().__init__(f"Circuit breaker is OPEN for {provider_name}")


@dataclass
class CircuitBreakerStats:
    """Snapshot of circuit breaker state for health reporting."""

    provider_name: str
    state: CircuitState
    consecutive_failures: int
    last_failure_time: float | None  # monotonic seconds
    total_successes: int
    total_failures: int


class CircuitBreaker:
    """Thread-safe per-provider circuit breaker.

    Parameters
    ----------
    name:
        Provider name (used in logs and exceptions).
    failure_threshold:
        Number of consecutive failures that triggers OPEN.  Default 5.
    recovery_timeout:
        Seconds before transitioning OPEN → HALF_OPEN.  Default 60.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time: float | None = None
        self._total_successes = 0
        self._total_failures = 0
        self._half_open_call_made = False

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def get_state(self) -> CircuitState:
        """Alias for the state property — exposed on the instance for health checks."""
        return self.state

    def stats(self) -> CircuitBreakerStats:
        """Thread-safe snapshot of current stats."""
        with self._lock:
            return CircuitBreakerStats(
                provider_name=self.name,
                state=self._state,
                consecutive_failures=self._consecutive_failures,
                last_failure_time=self._last_failure_time,
                total_successes=self._total_successes,
                total_failures=self._total_failures,
            )

    # ------------------------------------------------------------------ call
    def call(self, fn: Callable[..., F], *args: Any, **kwargs: Any) -> F:
        """Execute ``fn(*args, **kwargs)`` through the circuit breaker.

        Raises
        ------
        CircuitBreakerOpen
            When the breaker is OPEN and the recovery timeout has not elapsed.
        """
        self._check_and_transition()

        with self._lock:
            if self._state == CircuitState.OPEN:
                # Still within the recovery window — fail fast.
                raise CircuitBreakerOpen(self.name, self._state)
            if self._state == CircuitState.HALF_OPEN and self._half_open_call_made:
                # The single HALF_OPEN test call is already in flight; subsequent
                # calls fail-fast until the test result lands.
                raise CircuitBreakerOpen(self.name, self._state)

        try:
            result = fn(*args, **kwargs)
            self._record_success()
            return result
        except Exception:
            self._record_failure()
            raise

    # ---------------------------------------------------------------- internal
    def _check_and_transition(self) -> None:
        """OPEN → HALF_OPEN when recovery timeout has elapsed."""
        if self.state != CircuitState.OPEN:
            return
        with self._lock:
            if self._state != CircuitState.OPEN:
                return
            if (
                self._last_failure_time is not None
                and time.monotonic() - self._last_failure_time >= self.recovery_timeout
            ):
                self._transition_to(CircuitState.HALF_OPEN)
                self._half_open_call_made = False

    def _record_success(self) -> None:
        with self._lock:
            self._total_successes += 1
            if self._state == CircuitState.HALF_OPEN:
                logger.info(
                    "Circuit breaker '%s' HALF_OPEN → CLOSED (test call succeeded)",
                    self.name,
                )
                self._state = CircuitState.CLOSED
                self._consecutive_failures = 0
            elif self._consecutive_failures > 0:
                # Partial recovery — decay the failure counter on a success
                self._consecutive_failures = max(0, self._consecutive_failures - 1)

    def _record_failure(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._total_failures += 1
            self._consecutive_failures += 1
            self._last_failure_time = now

            if self._state == CircuitState.HALF_OPEN:
                logger.info(
                    "Circuit breaker '%s' HALF_OPEN → OPEN (test call failed)",
                    self.name,
                )
                self._state = CircuitState.OPEN
            elif (
                self._state == CircuitState.CLOSED
                and self._consecutive_failures >= self.failure_threshold
            ):
                logger.info(
                    "Circuit breaker '%s' CLOSED → OPEN (%d consecutive failures, threshold %d)",
                    self.name,
                    self._consecutive_failures,
                    self.failure_threshold,
                )
                self._state = CircuitState.OPEN
            elif self._state == CircuitState.HALF_OPEN:
                self._half_open_call_made = True

    def _transition_to(self, new_state: CircuitState) -> None:
        logger.info(
            "Circuit breaker '%s' %s → %s",
            self.name,
            self._state.value,
            new_state.value,
        )
        self._state = new_state
        if new_state == CircuitState.HALF_OPEN:
            self._half_open_call_made = False
        elif new_state == CircuitState.CLOSED:
            self._consecutive_failures = 0


def circuit_breaker(
    name: str,
    breaker: CircuitBreaker,
) -> Callable[[Callable[..., F]], Callable[..., F]]:
    """Decorator that wraps a callable through a named circuit breaker.

    Usage::

        @circuit_breaker("yahoo_finance", yahoo_breaker)
        def fetch_yahoo(...) -> Quote:
            ...

    The wrapped function is called via ``breaker.call(fn, *args, **kwargs)``.
    Any exception raised by the function is re-raised after the breaker
    records the failure.
    """

    def decorator(fn: Callable[..., F]) -> Callable[..., F]:
        def wrapper(*args: Any, **kwargs: Any) -> F:
            return breaker.call(fn, *args, **kwargs)

        # Preserve identity for introspection.
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator
