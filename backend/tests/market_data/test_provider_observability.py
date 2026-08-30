"""
Tests for per-call provider observability (v2.2): per-call structured logging
and per-provider Prometheus metrics.

Covers:
  - _correlation_id_placeholder() returns the correlation ID or '-' when unavailable
  - _call_provider() emits DEBUG log lines on success and failure
  - _provider_metrics() emits circuit_breaker_state, consecutive_failures, and
    rate_limit_hits_total metrics for every registered provider
  - _provider_metrics() handles gracefully when no breakers are registered
  - Prometheus text format is valid (HELP, TYPE, labelled metrics render)
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.market_data.circuit_breaker import CircuitState
from backend.market_data.services.manager import (
    _correlation_id_placeholder,
    _call_provider,
    _circuit_breakers,
    _cb_lock,
    _get_breaker,
    _rate_limiter,
)


class TestCorrelationIdPlaceholder(unittest.TestCase):
    """_correlation_id_placeholder() returns the ID or '-' gracefully."""

    def test_returns_dash_when_unavailable(self):
        """When no correlation ID is active, the placeholder returns '-'."""
        # When get_correlation_id returns None, placeholder returns '-'
        def fake_get_corr_id():
            return None

        with patch(
            "backend.market_data.services.manager._corr_id_fn", fake_get_corr_id
        ):
            self.assertEqual(_correlation_id_placeholder(), "-")

    def test_returns_id_when_available(self):
        """When a correlation ID is in the context, it is returned."""
        fake_id = "test-correlation-123"

        def fake_get_corr_id():
            return fake_id

        with patch(
            "backend.market_data.services.manager._corr_id_fn", fake_get_corr_id
        ):
            self.assertEqual(_correlation_id_placeholder(), fake_id)


class TestProviderLogging(unittest.TestCase):
    """_call_provider() emits structured DEBUG log lines on start, success, and failure."""

    def setUp(self):
        # Clear circuit breakers so tests are independent
        with _cb_lock:
            _circuit_breakers.clear()

    def tearDown(self):
        with _cb_lock:
            _circuit_breakers.clear()

    def test_success_logs_ok(self):
        """A successful call emits a provider_call_ok debug line."""
        mock_provider = MagicMock()
        mock_provider.name = "yahoo_finance"
        mock_provider.get_quote.return_value = MagicMock(
            symbol="AAPL",
            price=150.0,
            timestamp=datetime.now(),
            provider="yahoo_finance",
            data_status=MagicMock(value="live"),
        )

        log_lines = []

        def capture_debug(msg, *args, **kwargs):
            log_lines.append(msg % args)

        with patch(
            "backend.market_data.services.manager.logger.debug", capture_debug
        ), patch(
            "backend.market_data.services.manager._correlation_id_placeholder",
            return_value="-",
        ):
            result = _call_provider(mock_provider, "get_quote", "AAPL")

        # Should have logged: start, then ok
        self.assertTrue(
            any("provider_call_ok" in line for line in log_lines),
            f"Expected provider_call_ok in log lines: {log_lines}",
        )
        self.assertTrue(
            any("yahoo_finance" in line for line in log_lines),
            f"Expected provider name in log lines: {log_lines}",
        )
        self.assertTrue(
            any("AAPL" in line for line in log_lines),
            f"Expected symbol in log lines: {log_lines}",
        )
        self.assertTrue(
            any("latency_ms" in line for line in log_lines),
            f"Expected latency_ms in log lines: {log_lines}",
        )
        self.assertIsNotNone(result)

    def test_failure_logs_fail(self):
        """A failing call emits a provider_call_fail debug line with error type."""
        mock_provider = MagicMock()
        mock_provider.name = "yahoo_finance"
        mock_provider.get_quote.side_effect = RuntimeError("provider error")

        log_lines = []

        def capture_debug(msg, *args, **kwargs):
            log_lines.append(msg % args)

        with patch(
            "backend.market_data.services.manager.logger.debug", capture_debug
        ), patch(
            "backend.market_data.services.manager._correlation_id_placeholder",
            return_value="-",
        ):
            with self.assertRaises(RuntimeError):
                _call_provider(mock_provider, "get_quote", "AAPL")

        self.assertTrue(
            any("provider_call_fail" in line for line in log_lines),
            f"Expected provider_call_fail in log lines: {log_lines}",
        )
        self.assertTrue(
            any("RuntimeError" in line for line in log_lines),
            f"Expected RuntimeError in log lines: {log_lines}",
        )
        self.assertTrue(
            any("latency_ms" in line for line in log_lines),
            f"Expected latency_ms in log lines: {log_lines}",
        )

    def test_start_logged_before_call(self):
        """A call always starts with provider_call_start before completing."""
        mock_provider = MagicMock()
        mock_provider.name = "yahoo_finance"
        mock_provider.get_quote.return_value = MagicMock(
            symbol="AAPL", price=150.0, timestamp=datetime.now(),
            provider="yahoo_finance", data_status=MagicMock(value="live"),
        )

        log_lines = []

        def capture_debug(msg, *args, **kwargs):
            log_lines.append(msg % args)

        with patch(
            "backend.market_data.services.manager.logger.debug", capture_debug
        ), patch(
            "backend.market_data.services.manager._correlation_id_placeholder",
            return_value="req-abc-123",
        ):
            _call_provider(mock_provider, "get_quote", "MSFT")

        self.assertTrue(
            any("provider_call_start" in line for line in log_lines),
            f"Expected provider_call_start in log lines: {log_lines}",
        )
        self.assertTrue(
            any("req-abc-123" in line for line in log_lines),
            f"Expected correlation ID in log lines: {log_lines}",
        )
        self.assertTrue(
            any("MSFT" in line for line in log_lines),
            f"Expected symbol MSFT in log lines: {log_lines}",
        )


class TestProviderMetricsPrometheus(unittest.TestCase):
    """Per-provider Prometheus metrics: circuit breaker state, failures, rate-limit hits."""

    def setUp(self):
        # Clear circuit breakers and rate limiter for independent tests
        with _cb_lock:
            _circuit_breakers.clear()
        _rate_limiter._calls.clear()
        _rate_limiter._throttled_count.clear()

    def tearDown(self):
        with _cb_lock:
            _circuit_breakers.clear()
        _rate_limiter._calls.clear()
        _rate_limiter._throttled_count.clear()

    def test_circuit_breaker_state_metric_rendered(self):
        """Circuit breaker state is emitted as a labelled gauge metric."""
        from backend.observability.prometheus import _provider_metrics

        # Register a breaker and set its state to OPEN
        breaker = _get_breaker("test_provider")
        breaker._state = CircuitState.OPEN
        breaker._consecutive_failures = 3

        metrics_text = "\n".join(_provider_metrics())

        self.assertIn("marketlens_provider_circuit_breaker_state", metrics_text)
        self.assertIn('provider="test_provider"', metrics_text)
        # State 2 = OPEN
        self.assertIn("test_provider", metrics_text)

        breaker._state = CircuitState.CLOSED

    def test_consecutive_failures_metric_rendered(self):
        """Consecutive failures count is emitted as a labelled gauge metric."""
        from backend.observability.prometheus import _provider_metrics

        breaker = _get_breaker("fail_provider")
        breaker._consecutive_failures = 5

        metrics_text = "\n".join(_provider_metrics())

        self.assertIn("marketlens_provider_consecutive_failures", metrics_text)
        self.assertIn('provider="fail_provider"', metrics_text)

        breaker._consecutive_failures = 0

    def test_rate_limit_hits_metric_rendered(self):
        """Rate-limit throttle counts are emitted as labelled counter metrics."""
        from backend.observability.prometheus import _provider_metrics

        # Pre-populate some throttle counts
        _rate_limiter._throttled_count["yahoo_finance"] = 7
        _rate_limiter._throttled_count["webull"] = 0

        metrics_text = "\n".join(_provider_metrics())

        self.assertIn("marketlens_provider_rate_limit_hits_total", metrics_text)
        self.assertIn('provider="yahoo_finance"', metrics_text)
        self.assertIn('provider="webull"', metrics_text)
        # yahoo_finance should show 7
        self.assertIn('provider="yahoo_finance"} 7', metrics_text)

    def test_metric_type_and_help_present(self):
        """Each new metric has HELP and TYPE comment lines in the output."""
        from backend.observability.prometheus import _provider_metrics

        _get_breaker("another_provider")

        metrics_text = "\n".join(_provider_metrics())

        for metric_name in (
            "marketlens_provider_circuit_breaker_state",
            "marketlens_provider_consecutive_failures",
            "marketlens_provider_rate_limit_hits_total",
        ):
            self.assertIn(f"# HELP {metric_name}", metrics_text)
            self.assertIn(f"# TYPE {metric_name}", metrics_text)

    def test_no_bisers_registered_emits_nothing(self):
        """When no circuit breakers are registered, _provider_metrics returns []."""
        # Ensure no breakers are registered
        with _cb_lock:
            _circuit_breakers.clear()

        # Also stub the manager's providers dict and rate-limiter stats so
        # _provider_metrics() can't emit any per-provider metrics.
        # _provider_metrics() imports MarketDataManager and _rate_limiter from
        # the manager module lazily, so we only need to patch the source.
        fake_manager = MagicMock()
        fake_manager.providers = MagicMock()
        fake_manager.providers.keys = MagicMock(return_value=iter([]))

        fake_limiter = MagicMock()
        fake_limiter.stats = MagicMock(return_value={})

        with patch(
            "backend.market_data.services.manager.MarketDataManager",
            return_value=fake_manager,
        ), patch(
            "backend.market_data.services.manager._rate_limiter", fake_limiter
        ):
            from backend.observability.prometheus import _provider_metrics

            result = _provider_metrics()
        # Should be empty (no breakers, no known providers)
        self.assertEqual(result, [])

    def test_unknown_provider_in_limiter_shows_zero(self):
        """Providers known to the limiter but not to the manager still get a zero line."""
        from backend.observability.prometheus import _provider_metrics

        # A provider that was rate-limited but has no circuit breaker
        _rate_limiter._throttled_count["random_provider"] = 12

        metrics_text = "\n".join(_provider_metrics())

        self.assertIn('provider="random_provider"} 12', metrics_text)

    def test_render_prometheus_text_includes_provider_metrics(self):
        """render_prometheus_text() includes the provider metrics block."""
        from backend.observability.prometheus import render_prometheus_text

        _get_breaker("yahoo_finance")

        text = render_prometheus_text()
        self.assertIn("marketlens_provider_circuit_breaker_state", text)
        self.assertIn("marketlens_provider_rate_limit_hits_total", text)


if __name__ == "__main__":
    unittest.main()
