"""
Provider lifecycle, rate limiting, circuit breaking, and the call wrapper.

This module is kept separate from the manager so that the provider class
registry and per-call infrastructure (rate limiter, circuit breaker,
tenacity retry) can be tested and imported independently of the manager
singleton.

``_settings`` and ``redis`` come from the shared ``_providers`` module so
test patches at ``backend.market_data.services.manager._settings`` and
``backend.market_data.services.manager.redis`` propagate here.
"""
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.models.market_data import Bar

from ..circuit_breaker import CircuitBreaker, CircuitBreakerOpen, CircuitState
from ..provider import MarketDataProvider
from ._providers import _settings, get_settings, redis

logger = logging.getLogger(__name__)

# Timeframes whose bars go stale within seconds rather than minutes/hours.
# Daily and weekly bars are inherently "old" at close of market — stale
# detection on them is deferred to a future enhancement.
_INTRADAY_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h"}


# Provider class registry: maps the symbolic name used in settings
# (MARKET_DATA_PRIMARY_PROVIDER / FALLBACK_PROVIDERS) to a concrete class.
# Storing names instead of class references lets tests patch
# ``backend.market_data.services.manager.YFinanceProvider`` and have
# those changes visible at lookup time (the actual class is fetched
# from the shim module).
_PROVIDER_CLASS_NAMES: dict[str, str] = {
    "yahoo_finance": "YFinanceProvider",
}


def _resolve_provider_classes() -> dict[str, type[MarketDataProvider]]:
    """Return a mapping of name → class.

    Real classes (cached at import time, before any test patches are applied)
    are preferred so that ``@patch('backend.market_data.services.manager.YFinanceProvider')``
    decorators do not corrupt the registry keys with MagicMock objects.

    The shim is only consulted for provider names not already in the
    real-class cache (e.g. dynamically registered providers added via
    ``_register_finnhub`` at import time).
    """
    import sys

    resolved: dict[str, type[MarketDataProvider]] = {}
    # Prefer real classes (populated before any patches are active).
    for name, cls in _REAL_PROVIDER_CLASSES.items():
        resolved[name] = cls
    # Consult the shim for any names not in the real cache.
    manager = sys.modules.get("backend.market_data.services.manager")
    if manager is not None:
        for name, attr in _PROVIDER_CLASS_NAMES.items():
            if name in resolved:
                continue
            cls = getattr(manager, attr, None)
            if cls is not None:
                resolved[name] = cls
    return resolved


# The legacy ``_PROVIDER_CLASSES`` symbol is provided as a property so
# callers that read it (e.g. ``_PROVIDER_CLASSES.get(name)``) still work.
# Each access re-resolves through the shim so test patches are honoured.
class _ProviderClassRegistry:
    """Dict-like proxy that re-resolves provider classes on every read access.

    Backwards-compatible with the old ``_PROVIDER_CLASSES: dict[...]``
    API used by ``MarketDataManager._initialize_providers``.

    Writes (item assignment) are forwarded to a local dict since
    ``_register_finnhub`` adds providers dynamically at import time.
    """

    def __init__(self) -> None:
        self._local: dict[str, type[MarketDataProvider]] = {}

    def get(self, key: str, default: Any = None) -> Any:
        # Check local overrides first (e.g. finnhub added at import time).
        if key in self._local:
            return self._local[key]
        resolved = _resolve_provider_classes()
        return resolved.get(key, default)

    def __setitem__(self, key: str, value: type[MarketDataProvider]) -> None:
        self._local[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._local or key in _resolve_provider_classes()

    def __iter__(self):
        seen = set(self._local.keys())
        for k in _resolve_provider_classes():
            if k not in seen:
                yield k
                seen.add(k)
        yield from self._local

    def keys(self):
        return list(self)

    def values(self):
        return [self[k] for k in self]

    def items(self):
        return [(k, self[k]) for k in self]

    def __getitem__(self, key: str):
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __delitem__(self, key: str) -> None:
        """Remove a key from the local overrides."""
        del self._local[key]

    def clear(self) -> None:
        """Clear all local overrides."""
        self._local.clear()


# Cached real provider classes — populated at import time, BEFORE any test
# patches can be applied.  Used as the preferred source in _resolve_provider_classes
# so that test @patch decorators (which replace manager.YFinanceProvider with a
# MagicMock) do not corrupt the registry keys.
_REAL_PROVIDER_CLASSES: dict[str, type[MarketDataProvider]] = {}


def _populate_real_provider_classes() -> None:
    """Populate ``_REAL_PROVIDER_CLASSES`` with real classes at import time."""
    from ..providers.yfinance_provider import YFinanceProvider

    _REAL_PROVIDER_CLASSES["yahoo_finance"] = YFinanceProvider


_populate_real_provider_classes()


_PROVIDER_CLASSES = _ProviderClassRegistry()


def _get_webull_class() -> type[MarketDataProvider] | None:
    """Lazy resolver for WebullProvider.

    Deferred so a missing ``requests`` install only breaks the Webull
    path, not Yahoo Finance. The class is skipped entirely when
    ``WEBULL_ENABLED`` is false or credentials are not set.
    """
    try:
        from ..providers.webull_provider import WebullProvider
    except Exception as e:
        logger.warning(f"WebullProvider is not importable: {e}")
        return None

    webull_settings = _settings.webull
    if not webull_settings.enabled:
        return None
    if not webull_settings.app_key or not webull_settings.app_secret:
        return None
    return WebullProvider


def _get_finnhub_class() -> type[MarketDataProvider] | None:
    """Lazy resolver for FinnhubProvider.

    Deferred so a missing ``requests`` install only breaks the Finnhub
    path, not Yahoo Finance. The class is skipped entirely when
    ``FINNHUB_ENABLED`` is false.
    """
    try:
        from ..providers.finnhub_provider import FinnhubProvider
    except Exception as e:
        logger.warning(f"FinnhubProvider is not importable: {e}")
        return None

    finnhub_settings = _settings.finnhub
    if not finnhub_settings.enabled:
        return None
    return FinnhubProvider


# Register the actual Finnhub class at module import time (if eligible).
def _register_finnhub() -> None:
    finnhub_cls = _get_finnhub_class()
    if finnhub_cls is not None:
        _PROVIDER_CLASSES["finnhub"] = finnhub_cls


_register_finnhub()


class _PerProviderRateLimiter:
    """Sliding-window rate limiter keyed by provider name.

    Tracks call timestamps in a per-provider deque. Each call to
    ``acquire(provider_name)`` blocks until the provider is allowed
    another call under ``settings.market_data.rate_limit_per_minute``.

    Thread-safe via a single lock; rate limiting is on the slow path
    so contention is irrelevant.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._throttled_count: dict[str, int] = defaultdict(int)

    def acquire(self, provider_name: str, max_per_minute: int) -> None:
        """Block until one more call to ``provider_name`` is allowed."""
        if max_per_minute <= 0:
            return  # disabled

        with self._lock:
            now = time.monotonic()
            window_start = now - 60.0
            calls = self._calls[provider_name]
            while calls and calls[0] < window_start:
                calls.popleft()
            if len(calls) < max_per_minute:
                calls.append(now)
                return
            wait_seconds = (calls[0] + 60.0) - now
            self._throttled_count[provider_name] += 1

        if wait_seconds > 0:
            logger.debug(
                f"Rate-limited on {provider_name}: sleeping {wait_seconds:.2f}s "
                f"(limit {max_per_minute}/min)"
            )
            time.sleep(wait_seconds)
        self.acquire(provider_name, max_per_minute)

    def stats(self) -> dict[str, int]:
        """Snapshot of throttled-call counts per provider (for health endpoint)."""
        with self._lock:
            return dict(self._throttled_count)


# Singleton limiter shared across manager calls.
_rate_limiter = _PerProviderRateLimiter()


def _provider_retry():
    """Tenacity retry policy for a single provider call.

    Two attempts (original + one retry) with exponential backoff (0.5s → 1s).
    HTTP 429 rate-limit errors are NOT retried — the circuit breaker handles
    backoff.  Other transient errors (503, network timeouts) are retried once.
    """
    def _should_retry(exc: BaseException) -> bool:
        if isinstance(exc, CircuitBreakerOpen):
            return False
        if isinstance(exc, RuntimeError) and "rate limited" in str(exc):
            return False
        return True

    return retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
        reraise=True,
        retry=retry_if_exception(_should_retry),
    )


def _get_per_provider_rate_limit(provider_name: str) -> int:
    """Return the per-provider rate limit for ``provider_name``."""
    md = get_settings().market_data
    return getattr(md, f"{provider_name}_rate_limit_per_minute", md.rate_limit_per_minute)


# Module-level circuit breaker registry (one per provider name).
_circuit_breakers: dict[str, CircuitBreaker] = {}
_cb_lock = threading.Lock()


def _get_breaker(provider_name: str) -> CircuitBreaker:
    """Return the circuit breaker for ``provider_name``, creating it if needed."""
    with _cb_lock:
        if provider_name not in _circuit_breakers:
            _circuit_breakers[provider_name] = CircuitBreaker(
                name=provider_name,
                failure_threshold=5,
                recovery_timeout=60.0,
            )
        return _circuit_breakers[provider_name]


# Lazy-loaded at first use to avoid circular imports.
_corr_id_fn: Callable[[], str] | None = None


def _correlation_id_placeholder() -> str:
    """Return the current correlation ID, or '-' if none is active.

    Checks whether ``_correlation_id_placeholder`` itself was patched in the shim
    (tests do this); if so, call the patched function.  Otherwise falls back to
    the shim's ``_corr_id_fn`` (which tests also patch) or the local default.
    """
    import sys

    shim = sys.modules.get(__package__ + ".manager")
    # If _correlation_id_placeholder was patched in the shim, call it.
    shim_fn = getattr(shim, "_correlation_id_placeholder", None)
    if shim_fn is not None and shim_fn is not _correlation_id_placeholder:
        return shim_fn()
    # Resolve _corr_id_fn from the shim so test patches are honoured.
    fn = getattr(shim, "_corr_id_fn", None) if shim is not None else None
    if fn is None:
        # Fall back to the local default (lazy-loaded once).
        global _corr_id_fn
        if _corr_id_fn is None:
            try:
                from backend.observability.logging_enhanced import (
                    get_correlation_id,
                )
                _corr_id_fn = get_correlation_id
            except Exception:
                _corr_id_fn = lambda: "-"  # type: ignore[assignment]
        fn = _corr_id_fn
    return fn() or "-"


def _call_provider(
    provider: MarketDataProvider,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Invoke a provider method with per-provider rate limiting, circuit
    breaking, and tenacity retry.

    Rate limiting is checked before every call so bursts are throttled.
    The circuit breaker fail-fast path is evaluated before the call so
    OPEN providers skip the rate limiter entirely.
    """
    provider_name = provider.name
    breaker = _get_breaker(provider_name)

    limit = _get_per_provider_rate_limit(provider_name)
    _rate_limiter.acquire(provider_name, limit)

    method: Callable = getattr(provider, method_name)

    symbol = args[0] if args else "-"
    call_started = time.monotonic()
    logger.debug(
        "provider_call_start provider=%s method=%s symbol=%s correlation_id=%s",
        provider_name, method_name, symbol, _correlation_id_placeholder(),
    )
    try:
        result = _provider_call_with_breaker(
            provider_name, breaker, method, *args, **kwargs
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - call_started) * 1000.0
        logger.debug(
            "provider_call_fail provider=%s method=%s symbol=%s "
            "latency_ms=%.2f correlation_id=%s error_type=%s",
            provider_name, method_name, symbol, latency_ms,
            _correlation_id_placeholder(), type(exc).__name__,
        )
        raise
    latency_ms = (time.monotonic() - call_started) * 1000.0
    logger.debug(
        "provider_call_ok provider=%s method=%s symbol=%s "
        "latency_ms=%.2f correlation_id=%s",
        provider_name, method_name, symbol, latency_ms,
        _correlation_id_placeholder(),
    )
    return result


@_provider_retry()
def _provider_call_with_breaker(
    provider_name: str,
    breaker: CircuitBreaker,
    method: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute ``method(*args, **kwargs)`` through the circuit breaker."""
    return breaker.call(method, *args, **kwargs)


# Approximate bar counts to expect for a given (timeframe, range) tuple.
# Used to decide whether the cache is "good enough" to return without
# hitting a provider. Values are deliberately generous; if a cache returns
# at least 80% of the expected count, treat it as sufficient.
_EXPECTED_BAR_COUNTS: dict[tuple[str, str], int] = {
    ("1d", "1mo"): 22,
    ("1d", "3mo"): 65,
    ("1d", "6mo"): 130,
    ("1d", "1y"): 252,
    ("1d", "2y"): 504,
    ("1d", "5y"): 1260,
    ("1h", "1mo"): 200,
    ("1h", "3mo"): 600,
    ("1h", "1y"): 1900,
    ("5m", "1d"): 80,
    ("5m", "5d"): 400,
    ("15m", "5d"): 130,
    ("30m", "5d"): 65,
    ("1m", "1d"): 390,
    ("1m", "5d"): 1950,
    ("1wk", "2y"): 104,
    ("1wk", "5y"): 260,
    ("1mo", "5y"): 60,
}


def _newest_bar_age_seconds(bars: list[Bar]) -> float | None:
    """Age of the newest bar in seconds, or None if no bars."""
    if not bars:
        return None
    newest = max(bar.timestamp for bar in bars)
    return (datetime.now() - newest).total_seconds()
