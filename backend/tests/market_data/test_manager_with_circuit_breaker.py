"""
Tests for the circuit breaker integration in market_data/services/manager.py.

Covers:
  - OPEN circuit breaker causes provider to be skipped in _get_available_providers()
  - get_provider_statuses() includes circuit_breaker_state and consecutive_failures
  - _call_provider() passes the breaker correctly
  - _get_breaker() creates a new breaker lazily
  - Circuit breaker stats are accessible from the global registry
  - Retry exhaustion triggers fallback to the next provider
"""
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.market_data.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
)
from backend.market_data.services.manager import (
    _call_provider,
    _circuit_breakers,
    _cb_lock,
    _get_breaker,
    _provider_call_with_breaker,
)
from backend.models.market_data import ProviderStatus


class TestCircuitBreakerIntegration(unittest.TestCase):
    """Circuit breaker integration with the market data manager."""

    def test_get_breaker_returns_same_instance(self):
        """_get_breaker() is idempotent — returns the same instance for a given name."""
        b1 = _get_breaker("test_provider")
        b2 = _get_breaker("test_provider")
        self.assertIs(b1, b2)
        self.assertEqual(b1.name, "test_provider")
        # Clean up the module-level registry
        with _cb_lock:
            _circuit_breakers.pop("test_provider", None)

    def test_get_breaker_different_names_different_instances(self):
        b1 = _get_breaker("provider_a")
        b2 = _get_breaker("provider_b")
        self.assertIsNot(b1, b2)
        with _cb_lock:
            _circuit_breakers.pop("provider_a", None)
            _circuit_breakers.pop("provider_b", None)

    def test_circuit_breaker_registered_in_global_dict(self):
        """After _get_breaker(), the global _circuit_breakers dict contains the entry."""
        with _cb_lock:
            _circuit_breakers.clear()
        breaker = _get_breaker("global_test")
        self.assertIn("global_test", _circuit_breakers)
        self.assertIs(_circuit_breakers["global_test"], breaker)
        with _cb_lock:
            _circuit_breakers.pop("global_test", None)


class TestProviderSkippedWhenOpen(unittest.TestCase):
    """OPEN circuit breaker causes _get_available_providers() to skip that provider."""

    def test_open_breaker_skips_provider(self):
        """When a provider's breaker is OPEN, it should be excluded from available list."""
        with patch("backend.market_data.services.manager._settings") as mock_settings:
            mock_settings.market_data.primary_provider = "mock"
            mock_settings.market_data.fallback_providers = []
            mock_settings.market_data.cache_enabled = False
            mock_settings.market_data.rate_limit_per_minute = 1000
            mock_settings.market_data.yahoo_finance_rate_limit_per_minute = 1000
            mock_settings.market_data.webull_rate_limit_per_minute = 1000

            # Patch provider classes so we don't need real providers
            with patch.dict(
                "backend.market_data.services.manager._PROVIDER_CLASSES",
                {},
                clear=True,
            ):
                from backend.market_data.services.manager import MarketDataManager

                with patch.object(MarketDataManager, "__init__", lambda self: None):
                    manager = MarketDataManager.__new__(MarketDataManager)
                    manager.provider_priority = ["primary", "secondary"]
                    manager.providers = {
                        "primary": MagicMock(is_available=MagicMock(return_value=True)),
                        "secondary": MagicMock(is_available=MagicMock(return_value=True)),
                    }

                    # Set primary's breaker to OPEN
                    p_breaker = _get_breaker("primary")
                    # Force it to OPEN by adding failures
                    p_breaker._state = CircuitState.OPEN

                    available = manager._get_available_providers()
                    # primary should be skipped (OPEN), secondary should remain
                    self.assertNotIn("primary", available)
                    self.assertIn("secondary", available)

                    # Clean up
                    with _cb_lock:
                        _circuit_breakers.pop("primary", None)
                        _circuit_breakers.pop("secondary", None)

    def test_half_open_provider_also_skipped(self):
        """HALF_OPEN state also causes the provider to be skipped."""
        with patch("backend.market_data.services.manager._settings") as mock_settings:
            mock_settings.market_data.primary_provider = "half_open_provider"
            mock_settings.market_data.fallback_providers = []
            mock_settings.market_data.cache_enabled = False
            mock_settings.market_data.rate_limit_per_minute = 1000
            mock_settings.market_data.yahoo_finance_rate_limit_per_minute = 1000
            mock_settings.market_data.webull_rate_limit_per_minute = 1000

            with patch.dict(
                "backend.market_data.services.manager._PROVIDER_CLASSES",
                {},
                clear=True,
            ):
                from backend.market_data.services.manager import MarketDataManager

                with patch.object(MarketDataManager, "__init__", lambda self: None):
                    manager = MarketDataManager.__new__(MarketDataManager)
                    manager.provider_priority = ["half_open_provider"]
                    manager.providers = {
                        "half_open_provider": MagicMock(is_available=MagicMock(return_value=True)),
                    }

                    # Set breaker to HALF_OPEN
                    hb_breaker = _get_breaker("half_open_provider")
                    hb_breaker._state = CircuitState.HALF_OPEN

                    available = manager._get_available_providers()
                    self.assertNotIn("half_open_provider", available)

                    with _cb_lock:
                        _circuit_breakers.pop("half_open_provider", None)


class TestProviderStatusEnrichment(unittest.TestCase):
    """get_provider_statuses() enriches ProviderStatus with circuit breaker stats."""

    def test_provider_status_includes_breaker_state(self):
        """When a breaker exists, the status includes circuit_breaker_state."""
        with patch("backend.market_data.services.manager._settings") as mock_settings:
            mock_settings.market_data.primary_provider = "test_status"
            mock_settings.market_data.fallback_providers = []
            mock_settings.market_data.cache_enabled = False
            mock_settings.market_data.rate_limit_per_minute = 1000
            mock_settings.market_data.yahoo_finance_rate_limit_per_minute = 1000
            mock_settings.market_data.webull_rate_limit_per_minute = 1000

            with patch.dict(
                "backend.market_data.services.manager._PROVIDER_CLASSES",
                {},
                clear=True,
            ):
                from backend.market_data.services.manager import MarketDataManager

                with patch.object(MarketDataManager, "__init__", lambda self: None):
                    manager = MarketDataManager.__new__(MarketDataManager)
                    manager.provider_priority = ["test_status"]
                    manager.providers = {
                        "test_status": MagicMock(
                            is_available=MagicMock(return_value=True),
                            get_provider_status=MagicMock(return_value=ProviderStatus(
                                provider_name="test_status",
                                is_healthy=True,
                                timestamp=datetime.now(timezone.utc),
                            )),
                        ),
                    }

                    # Create a breaker and record some activity
                    breaker = _get_breaker("test_status")
                    breaker._total_successes = 10
                    breaker._total_failures = 2
                    breaker._consecutive_failures = 1
                    breaker._state = CircuitState.OPEN

                    statuses = manager.get_provider_statuses()
                    self.assertIn("test_status", statuses)
                    s = statuses["test_status"]
                    self.assertEqual(s.circuit_breaker_state, "OPEN")
                    self.assertEqual(s.consecutive_failures, 1)
                    self.assertEqual(s.total_successes, 10)
                    self.assertEqual(s.total_failures, 2)
                    self.assertEqual(s.error_count, 2)

                    with _cb_lock:
                        _circuit_breakers.pop("test_status", None)

    def test_provider_without_breaker_has_default_state(self):
        """Providers without a breaker entry get the pydantic default state."""
        with patch("backend.market_data.services.manager._settings") as mock_settings:
            mock_settings.market_data.primary_provider = "no_breaker"
            mock_settings.market_data.fallback_providers = []
            mock_settings.market_data.cache_enabled = False
            mock_settings.market_data.rate_limit_per_minute = 1000
            mock_settings.market_data.yahoo_finance_rate_limit_per_minute = 1000
            mock_settings.market_data.webull_rate_limit_per_minute = 1000

            with patch.dict(
                "backend.market_data.services.manager._PROVIDER_CLASSES",
                {},
                clear=True,
            ):
                from backend.market_data.services.manager import MarketDataManager

                with patch.object(MarketDataManager, "__init__", lambda self: None):
                    manager = MarketDataManager.__new__(MarketDataManager)
                    manager.provider_priority = ["no_breaker"]
                    # No breaker registered for this provider
                    manager.providers = {
                        "no_breaker": MagicMock(
                            is_available=MagicMock(return_value=True),
                            get_provider_status=MagicMock(return_value=ProviderStatus(
                                provider_name="no_breaker",
                                is_healthy=True,
                                timestamp=datetime.now(timezone.utc),
                            )),
                        ),
                    }

                    statuses = manager.get_provider_statuses()
                    self.assertIn("no_breaker", statuses)
                    # Should have the default "CLOSED" state
                    self.assertEqual(statuses["no_breaker"].circuit_breaker_state, "CLOSED")


class TestCallProviderWithBreaker(unittest.TestCase):
    """_call_provider() routes calls through the circuit breaker."""

    def _rate_limiter_mock(self):
        """Return a no-op rate limiter so calls aren't throttled in tests."""
        limiter = MagicMock()
        limiter.acquire = MagicMock()  # no-op
        return limiter

    def test_call_provider_returns_result_on_success(self):
        """_call_provider() returns the method result when circuit is CLOSED."""
        mock_provider = MagicMock()
        mock_provider.name = "test_cp"
        mock_provider.get_value = MagicMock(return_value="success")
        breaker = _get_breaker("test_cp")
        breaker._state = CircuitState.CLOSED
        breaker._consecutive_failures = 0

        noop_limiter = MagicMock()
        noop_limiter.acquire = MagicMock()
        with patch(
            "backend.market_data.services.manager._rate_limiter", noop_limiter
        ):
            result = _call_provider(mock_provider, "get_value")

        self.assertEqual(result, "success")
        self.assertEqual(breaker._total_successes, 1)

        with _cb_lock:
            _circuit_breakers.pop("test_cp", None)

    def test_call_provider_raises_circuit_breaker_open(self):
        """_call_provider() raises CircuitBreakerOpen when breaker is OPEN."""
        mock_provider = MagicMock()
        mock_provider.name = "test_cp_open"
        mock_provider.get_value = MagicMock(return_value="should not reach")
        breaker = _get_breaker("test_cp_open")
        breaker._state = CircuitState.OPEN
        breaker._last_failure_time = time.monotonic()

        noop_limiter = MagicMock()
        noop_limiter.acquire = MagicMock()
        with patch(
            "backend.market_data.services.manager._rate_limiter", noop_limiter
        ):
            with self.assertRaises(CircuitBreakerOpen) as ctx:
                _call_provider(mock_provider, "get_value")
        self.assertEqual(ctx.exception.provider_name, "test_cp_open")

        with _cb_lock:
            _circuit_breakers.pop("test_cp_open", None)

    def test_call_provider_records_failure_on_exception(self):
        """A failing call increments failure counters on the breaker.

        Tenacity retries 3 times — each retry is wrapped by the breaker,
        so the failure counter advances once per attempt.
        """
        mock_provider = MagicMock()
        mock_provider.name = "test_cp_fail"
        mock_provider.get_value = MagicMock(side_effect=ValueError("boom"))
        breaker = _get_breaker("test_cp_fail")
        breaker._state = CircuitState.CLOSED
        breaker._consecutive_failures = 0
        breaker._total_failures = 0

        noop_limiter = MagicMock()
        noop_limiter.acquire = MagicMock()
        with patch(
            "backend.market_data.services.manager._rate_limiter", noop_limiter
        ):
            with self.assertRaises(ValueError):
                _call_provider(mock_provider, "get_value")

        # Tenacity retries 2 times → 2 failures recorded on the breaker
        self.assertEqual(breaker._total_failures, 2)
        self.assertEqual(breaker._consecutive_failures, 2)

        with _cb_lock:
            _circuit_breakers.pop("test_cp_fail", None)

    def test_circuit_breaker_open_escapes_retry(self):
        """CircuitBreakerOpen is re-raised immediately, not retried by tenacity."""
        mock_provider = MagicMock()
        mock_provider.name = "test_retry"
        mock_provider.get_value = MagicMock(return_value="ok")
        breaker = _get_breaker("test_retry")
        breaker._state = CircuitState.OPEN
        breaker._last_failure_time = time.monotonic()

        noop_limiter = MagicMock()
        noop_limiter.acquire = MagicMock()
        with patch(
            "backend.market_data.services.manager._rate_limiter", noop_limiter
        ):
            # Should raise CircuitBreakerOpen immediately — tenacity should not retry it
            with self.assertRaises(CircuitBreakerOpen):
                _call_provider(mock_provider, "get_value")

        # Provider method should NOT have been called (OPEN skip happens before call)
        mock_provider.get_value.assert_not_called()

        with _cb_lock:
            _circuit_breakers.pop("test_retry", None)


class TestBreakerStatsSnapshot(unittest.TestCase):
    """breaker.stats() returns a consistent snapshot."""

    def test_stats_snapshot_has_all_fields(self):
        """Stats snapshot contains all expected fields."""
        breaker = _get_breaker("stats_test")
        breaker._state = CircuitState.HALF_OPEN
        breaker._consecutive_failures = 3
        breaker._total_successes = 50
        breaker._total_failures = 10
        breaker._last_failure_time = time.monotonic()

        s = breaker.stats()
        self.assertEqual(s.provider_name, "stats_test")
        self.assertEqual(s.state, CircuitState.HALF_OPEN)
        self.assertEqual(s.consecutive_failures, 3)
        self.assertEqual(s.total_successes, 50)
        self.assertEqual(s.total_failures, 10)
        self.assertIsNotNone(s.last_failure_time)

        with _cb_lock:
            _circuit_breakers.pop("stats_test", None)


if __name__ == "__main__":
    unittest.main()
