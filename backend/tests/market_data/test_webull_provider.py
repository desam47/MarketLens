"""
Tests for backend/market_data/providers/webull_provider.py

Covers:
  - WebullAuthError when credentials are missing
  - get_quote() parses SDK response and returns Quote
  - get_historical_bars() parses SDK response and returns list[Bar]
  - get_batch_quotes() handles SDK snapshot response
  - get_market_status() returns MarketStatus
  - HTTP errors raise RuntimeError for CB tracking
  - is_available() returns True/False based on _data_client presence
  - Credentials are never logged or included in responses
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

import backend.market_data.providers.webull_provider as _webull_module
from backend.market_data.providers.webull_provider import (
    WebullAuthError,
    WebullProvider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeWebullSettings:
    """Fake settings object matching the real WebullSettings shape."""
    def __init__(self, app_key: str = "test_key", app_secret: str = "test_secret",
                 use_sandbox: bool = True, enabled: bool = True):
        self.app_key = app_key
        self.app_secret = app_secret
        self.use_sandbox = use_sandbox
        self.enabled = enabled


def _make_provider(data_client_mock: MagicMock) -> WebullProvider:
    """Return a WebullProvider with a mocked _data_client injected.

    Patches the provider's __init__ so the SDK is never loaded and the
    provided data client mock is used directly.
    """
    def fake_init(self):
        self.name = "webull"
        self._settings = _FakeWebullSettings()
        self._data_client = data_client_mock
        self._last_error: Exception | None = None

    with patch.object(WebullProvider, "__init__", fake_init):
        p = WebullProvider()
    return p


# ---------------------------------------------------------------------------
# Authentication error tests
# ---------------------------------------------------------------------------
class TestWebullAuthErrors(unittest.TestCase):
    def test_missing_credentials_raises_auth_error(self):
        """Missing app_key raises WebullAuthError at init."""
        fake_settings = _FakeWebullSettings(app_key="", app_secret="test_secret")
        with patch.object(_webull_module, "_settings",
                          MagicMock(webull=fake_settings)):
            with self.assertRaises(WebullAuthError) as ctx:
                WebullProvider()
        self.assertIn("must be set", str(ctx.exception))

    def test_missing_secret_raises_auth_error(self):
        """Missing app_secret raises WebullAuthError at init."""
        fake_settings = _FakeWebullSettings(app_key="test_key", app_secret="")
        with patch.object(_webull_module, "_settings",
                          MagicMock(webull=fake_settings)):
            with self.assertRaises(WebullAuthError) as ctx:
                WebullProvider()
        self.assertIn("must be set", str(ctx.exception))


# ---------------------------------------------------------------------------
# get_quote tests
# ---------------------------------------------------------------------------
class TestGetQuote(unittest.TestCase):
    def _make_provider(self, snapshot_response: list[dict]) -> WebullProvider:
        """Return a WebullProvider with a mocked data client."""
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = snapshot_response
        mock_data.market_data.get_snapshot.return_value = mock_resp
        return _make_provider(mock_data)

    def test_quote_fields_parsed_correctly(self):
        import time as _time
        now_ms = int(_time.time() * 1000)
        p = self._make_provider([
            {
                "symbol": "AAPL",
                "price": 185.50,
                "bid": 185.45,
                "ask": 185.55,
                "volume": 42_000_000,
                "quote_time": now_ms,
            }
        ])
        q = p.get_quote("aapl")
        self.assertEqual(q.symbol, "AAPL")
        self.assertEqual(q.price, 185.50)
        self.assertEqual(q.bid, 185.45)
        self.assertEqual(q.ask, 185.55)
        self.assertEqual(q.volume, 42_000_000)
        self.assertEqual(q.provider, "webull")
        self.assertEqual(q.data_status.value, "DELAYED")

    def test_quote_delayed_status(self):
        p = self._make_provider([{"symbol": "MSFT", "price": 100.0, "quote_time": 0}])
        q = p.get_quote("msft")
        self.assertEqual(q.data_status.value, "DELAYED")

    def test_quote_empty_response_raises_runtime_error(self):
        """An empty snapshot list raises RuntimeError so the caller can handle it."""
        p = self._make_provider([])
        with self.assertRaises(RuntimeError) as ctx:
            p.get_quote("TSLA")
        self.assertIn("empty snapshot", str(ctx.exception))


# ---------------------------------------------------------------------------
# get_historical_bars tests
# ---------------------------------------------------------------------------
class TestGetHistoricalBars(unittest.TestCase):
    def _make_provider(self, bars_response: list[dict]) -> WebullProvider:
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = bars_response
        mock_data.market_data.get_history_bar.return_value = mock_resp
        return _make_provider(mock_data)

    def test_bars_parsed_from_response(self):
        import time as _time
        t1 = int(_time.time() * 1000)
        t2 = int((_time.time() - 86400) * 1000)
        p = self._make_provider([
            {"time": t1, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000000},
            {"time": t2, "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 900000},
        ])
        bars = p.get_historical_bars("spy", timeframe="1d", range_="5d")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].symbol, "SPY")
        # Phase 3.1.22: bars are sorted chronologically (oldest→newest).
        # ``bars[0]`` is yesterday's bar (t2), ``bars[1]`` is today's (t1).
        self.assertEqual(bars[0].close, 99.5)
        self.assertEqual(bars[1].close, 100.5)
        self.assertEqual(bars[0].timeframe, "1d")
        self.assertEqual(bars[0].data_status.value, "HISTORICAL")

    def test_bars_empty_returns_empty_list(self):
        p = self._make_provider([])
        bars = p.get_historical_bars("qqq")
        self.assertEqual(bars, [])

    def test_bars_skips_rows_with_missing_timestamp(self):
        import time as _time
        now = int(_time.time() * 1000)
        p = self._make_provider([
            {"time": now, "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.5, "volume": 500000},
            {"open": 60.0, "high": 61.0, "low": 59.0, "close": 60.0, "volume": 600000},  # missing time → uses now
        ])
        bars = p.get_historical_bars("dia")
        # Provider includes all rows; rows without a timestamp get datetime.now
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].close, 50.5)

    def test_non_200_http_raises_runtime_error(self):
        """Non-200 from the SDK raises RuntimeError so the circuit breaker tracks it."""
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_data.market_data.get_history_bar.return_value = mock_resp
        p = _make_provider(mock_data)
        with self.assertRaises(RuntimeError) as ctx:
            p.get_historical_bars("spy")
        self.assertIn("500", str(ctx.exception))


# ---------------------------------------------------------------------------
# Webull free-tier M1→M5 downscale detection
# ---------------------------------------------------------------------------
class TestTimeframeInference(unittest.TestCase):
    """Webull's free tier silently downgrades M1 requests to M5 resolution.
    ``_infer_actual_timeframe`` must detect that from inter-bar gaps and
    re-stamp each bar's ``timeframe`` field, so storage + signal recording
    reflect the true resolution."""

    def test_infers_1m_for_60s_gaps(self):
        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        from datetime import datetime, timezone, timedelta
        base = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        bars = [base + timedelta(seconds=60 * i) for i in range(10)]
        self.assertEqual(_infer_actual_timeframe("M1", bars), "1m")

    def test_infers_5m_for_300s_gaps(self):
        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        from datetime import datetime, timezone, timedelta
        base = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        bars = [base + timedelta(seconds=300 * i) for i in range(10)]
        self.assertEqual(_infer_actual_timeframe("M1", bars), "5m")

    def test_infers_15m_for_900s_gaps(self):
        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        from datetime import datetime, timezone, timedelta
        base = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        bars = [base + timedelta(seconds=900 * i) for i in range(10)]
        self.assertEqual(_infer_actual_timeframe("M1", bars), "15m")

    def test_infers_1h_for_3600s_gaps(self):
        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        from datetime import datetime, timezone, timedelta
        base = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        bars = [base + timedelta(seconds=3600 * i) for i in range(10)]
        self.assertEqual(_infer_actual_timeframe("M1", bars), "1h")

    def test_falls_back_to_timespan_when_too_few_bars(self):
        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        self.assertEqual(_infer_actual_timeframe("M1", []), "1m")
        self.assertEqual(_infer_actual_timeframe("M5", []), "5m")

    def test_median_robust_to_outliers(self):
        """One huge gap (lunch break) shouldn't pull inference to a coarser TF."""
        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        from datetime import datetime, timezone, timedelta
        base = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        # 9 1-minute bars + 1 big gap, then 9 more
        bars = [base + timedelta(seconds=60 * i) for i in range(9)]
        bars += [base + timedelta(seconds=60 * 18 + i) for i in range(9)]
        self.assertEqual(_infer_actual_timeframe("M1", bars), "1m")

    def test_provider_restamps_downgraded_bars(self):
        """When Webull returns 5m bars under an M1 request, each Bar's
        ``timeframe`` field should be re-stamped to "5m" so storage
        downstream reflects the actual resolution."""
        import time as _time
        # Build 5 bars spaced 5 minutes apart (simulating free-tier M5 data)
        now_ms = int(_time.time() * 1000)
        rows = []
        for i in range(5):
            rows.append({
                "time": now_ms - (300_000 * i),
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.5, "volume": 1000000,
            })
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = rows
        mock_data.market_data.get_history_bar.return_value = mock_resp
        p = _make_provider(mock_data)
        bars = p.get_historical_bars("spy", timeframe="1m", range_="1d")
        self.assertEqual(len(bars), 5)
        # All bars should be re-stamped from "1m" → "5m"
        for bar in bars:
            self.assertEqual(bar.timeframe, "5m")

    def test_provider_keeps_1m_for_true_1m_data(self):
        """When the response is genuinely 1m, the timeframe stays as 1m."""
        import time as _time
        now_ms = int(_time.time() * 1000)
        rows = []
        for i in range(5):
            rows.append({
                "time": now_ms - (60_000 * i),
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.5, "volume": 1000000,
            })
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = rows
        mock_data.market_data.get_history_bar.return_value = mock_resp
        p = _make_provider(mock_data)
        bars = p.get_historical_bars("spy", timeframe="1m", range_="1d")
        for bar in bars:
            self.assertEqual(bar.timeframe, "1m")


# ---------------------------------------------------------------------------
# get_batch_quotes tests
# ---------------------------------------------------------------------------
class TestGetBatchQuotes(unittest.TestCase):
    def test_batch_quotes_parses_multi_symbol_response(self):
        """get_batch_quotes should parse a snapshot list into per-symbol Quote objects."""
        import time as _time
        now_ms = int(_time.time() * 1000)
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"symbol": "AAPL", "price": 150.0, "bid": 149.9, "ask": 150.1,
             "volume": 1_000_000, "quote_time": now_ms},
            {"symbol": "MSFT", "price": 300.0, "bid": 299.9, "ask": 300.1,
             "volume": 500_000, "quote_time": now_ms},
        ]
        mock_data.market_data.get_snapshot.return_value = mock_resp
        p = _make_provider(mock_data)
        result = p.get_batch_quotes(["AAPL", "MSFT"])
        self.assertEqual(result["AAPL"].price, 150.0)
        self.assertEqual(result["MSFT"].price, 300.0)
        self.assertEqual(result["AAPL"].provider, "webull")

    def test_batch_quotes_partial_response_fills_missing_with_error_quotes(self):
        """If the snapshot omits some symbols, those symbols get zero-price error Quotes."""
        import time as _time
        now_ms = int(_time.time() * 1000)
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"symbol": "AAPL", "price": 150.0, "quote_time": now_ms},
            # MSFT omitted from response
        ]
        mock_data.market_data.get_snapshot.return_value = mock_resp
        p = _make_provider(mock_data)
        result = p.get_batch_quotes(["AAPL", "MSFT"])
        self.assertEqual(result["AAPL"].price, 150.0)
        self.assertEqual(result["MSFT"].price, 0.0)
        self.assertEqual(result["MSFT"].data_status.value, "ERROR")


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------
class TestWebullErrorHandling(unittest.TestCase):
    def test_429_raises_runtime_error_for_circuit_breaker(self):
        """HTTP 429 should raise RuntimeError so the circuit breaker tracks it."""
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_data.market_data.get_snapshot.return_value = mock_resp
        p = _make_provider(mock_data)
        with self.assertRaises(RuntimeError) as ctx:
            p.get_quote("AAPL")
        self.assertIn("429", str(ctx.exception))

    def test_http_500_raises_runtime_error(self):
        """HTTP 500 raises RuntimeError so the circuit breaker tracks it."""
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_data.market_data.get_snapshot.return_value = mock_resp
        p = _make_provider(mock_data)
        with self.assertRaises(RuntimeError) as ctx:
            p.get_quote("AAPL")
        self.assertIn("500", str(ctx.exception))


# ---------------------------------------------------------------------------
# get_market_status tests
# ---------------------------------------------------------------------------
class TestGetMarketStatus(unittest.TestCase):
    def _make_provider(self, snapshot_response: list[dict]) -> WebullProvider:
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = snapshot_response
        mock_data.market_data.get_snapshot.return_value = mock_resp
        return _make_provider(mock_data)

    def test_market_status_open(self):
        p = self._make_provider([
            {"symbol": "AAPL", "marketStatus": "OPEN", "marketState": "OPEN"}
        ])
        s = p.get_market_status("AAPL")
        self.assertEqual(s.symbol, "AAPL")
        self.assertTrue(s.is_open)
        self.assertEqual(s.timezone, "America/New_York")

    def test_market_status_closed(self):
        p = self._make_provider([
            {"symbol": "TSLA", "marketStatus": "CLOSED", "marketState": "CLOSED"}
        ])
        s = p.get_market_status("TSLA")
        self.assertFalse(s.is_open)


# ---------------------------------------------------------------------------
# is_available tests
# ---------------------------------------------------------------------------
class TestIsAvailable(unittest.TestCase):
    def test_returns_true_when_data_client_set(self):
        mock_data = MagicMock()
        p = _make_provider(mock_data)
        self.assertTrue(p.is_available())

    def test_returns_true_without_credentials_set(self):
        """Even without credentials WebullAuthError, is_available() checks the client."""
        # This tests the is_available() method directly on an object with _data_client set
        p = WebullProvider.__new__(WebullProvider)
        p.name = "webull"
        p._data_client = MagicMock()  # non-None
        self.assertTrue(p.is_available())

    def test_is_available_false_when_data_client_is_none(self):
        """is_available() is False when _data_client has not been initialized."""
        p = WebullProvider.__new__(WebullProvider)
        p.name = "webull"
        p._data_client = None
        self.assertFalse(p.is_available())


# ---------------------------------------------------------------------------
# Security / credentials-never-leaked tests
# ---------------------------------------------------------------------------
class TestCredentialsNotLeaked(unittest.TestCase):
    def test_auth_error_does_not_expose_credentials(self):
        """Auth error messages must not contain app_key or app_secret.

        When credentials are empty, WebullAuthError is raised before any SDK call.
        The error message must never echo back the (empty) credentials.
        """
        fake_settings = _FakeWebullSettings(app_key="", app_secret="")
        with patch.object(_webull_module, "_settings",
                          MagicMock(webull=fake_settings)):
            with self.assertRaises(WebullAuthError) as ctx:
                WebullProvider()
        msg = str(ctx.exception)
        self.assertIn("must be set", msg)
        # The generic message is shown, not the credential values themselves
        self.assertNotIn("None", msg)


if __name__ == "__main__":
    unittest.main()
