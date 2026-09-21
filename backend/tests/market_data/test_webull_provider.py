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
import json
import os
import sys
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

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
        mock_resp.text = json.dumps(snapshot_response)
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

    def test_quote_parses_decimal_string_volume(self):
        """Regression: Webull can return volume as a decimal-formatted
        string ('57259716.57259716'), which int() rejects outright."""
        p = self._make_provider([
            {"symbol": "TSLA", "price": 430.0, "volume": "57259716.57259716", "quote_time": 0}
        ])
        q = p.get_quote("tsla")
        self.assertEqual(q.volume, 57259716)


# ---------------------------------------------------------------------------
# get_recent_ticks tests
# ---------------------------------------------------------------------------
class TestGetRecentTicks(unittest.TestCase):
    """get_recent_ticks exists as a real provider method (rather than tape
    seeding reaching into provider._data_client directly) so it can be
    routed through _call_provider and share Webull's rate limiter/circuit
    breaker. See backend/api/tape/registry.py's _seed_from_webull_ticks."""

    def test_calls_sdk_get_tick_with_upper_symbol_and_stringified_count(self):
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_data.market_data.get_tick.return_value = mock_resp
        p = _make_provider(mock_data)

        result = p.get_recent_ticks("aapl", count=200)

        mock_data.market_data.get_tick.assert_called_once_with(
            "AAPL", "US_STOCK", count="200"
        )
        self.assertIs(result, mock_resp)

    def test_default_count_is_200(self):
        mock_data = MagicMock()
        p = _make_provider(mock_data)

        p.get_recent_ticks("TSLA")

        mock_data.market_data.get_tick.assert_called_once_with(
            "TSLA", "US_STOCK", count="200"
        )


# ---------------------------------------------------------------------------
# get_financial_indicators / get_fund_brief tests
# ---------------------------------------------------------------------------
class TestGetFundamentalsMethods(unittest.TestCase):
    """Same rationale as get_recent_ticks: real provider methods (rather
    than webull_fundamentals.py reaching into provider._data_client
    directly) so fundamentals calls can be routed through _call_provider
    and share Webull's rate limiter/circuit breaker."""

    def test_get_financial_indicators_calls_sdk_with_upper_symbol(self):
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_data.fundamentals.get_financials_indicators.return_value = mock_resp
        p = _make_provider(mock_data)

        result = p.get_financial_indicators("aapl")

        mock_data.fundamentals.get_financials_indicators.assert_called_once_with(
            "AAPL", "US_STOCK"
        )
        self.assertIs(result, mock_resp)

    def test_get_fund_brief_calls_sdk_with_upper_symbol(self):
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_data.fundamentals.get_fund_brief.return_value = mock_resp
        p = _make_provider(mock_data)

        result = p.get_fund_brief("tsla")

        mock_data.fundamentals.get_fund_brief.assert_called_once_with("TSLA", "US_STOCK")
        self.assertIs(result, mock_resp)


# ---------------------------------------------------------------------------
# get_historical_bars tests
# ---------------------------------------------------------------------------
class TestGetHistoricalBars(unittest.TestCase):
    def _make_provider(self, bars_response: list[dict]) -> WebullProvider:
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = bars_response
        mock_resp.text = json.dumps(bars_response)
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

    def test_bars_parses_decimal_string_volume(self):
        """Regression: live 2026-09-16, TSLA 1d bars — Webull returned
        volume as a decimal-formatted string ('57259716.57259716'), which
        int() rejects outright (ValueError), crashing the whole bar fetch.
        Must parse via float() first, matching the pattern already used
        for extend_hour_volume."""
        import time as _time
        t1 = int(_time.time() * 1000)
        p = self._make_provider([
            {"time": t1, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
             "volume": "57259716.57259716"},
        ])
        bars = p.get_historical_bars("tsla", timeframe="1d", range_="5d")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].volume, 57259716)

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
        from datetime import timedelta

        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        base = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        bars = [base + timedelta(seconds=60 * i) for i in range(10)]
        self.assertEqual(_infer_actual_timeframe("M1", bars), "1m")

    def test_infers_5m_for_300s_gaps(self):
        from datetime import timedelta

        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        base = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        bars = [base + timedelta(seconds=300 * i) for i in range(10)]
        self.assertEqual(_infer_actual_timeframe("M1", bars), "5m")

    def test_infers_15m_for_900s_gaps(self):
        from datetime import timedelta

        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        base = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        bars = [base + timedelta(seconds=900 * i) for i in range(10)]
        self.assertEqual(_infer_actual_timeframe("M1", bars), "15m")

    def test_infers_1h_for_3600s_gaps(self):
        from datetime import timedelta

        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        base = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        bars = [base + timedelta(seconds=3600 * i) for i in range(10)]
        self.assertEqual(_infer_actual_timeframe("M1", bars), "1h")

    def test_falls_back_to_timespan_when_too_few_bars(self):
        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        self.assertEqual(_infer_actual_timeframe("M1", []), "1m")
        self.assertEqual(_infer_actual_timeframe("M5", []), "5m")

    def test_median_robust_to_outliers(self):
        """One huge gap (lunch break) shouldn't pull inference to a coarser TF."""
        from datetime import timedelta

        from backend.market_data.providers.webull_provider import _infer_actual_timeframe
        base = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
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
        mock_resp.text = json.dumps(rows)
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
        mock_resp.text = json.dumps(rows)
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
        mock_resp.text = json.dumps(mock_resp.json.return_value)
        mock_data.market_data.get_snapshot.return_value = mock_resp
        p = _make_provider(mock_data)
        result = p.get_batch_quotes(["AAPL", "MSFT"])
        self.assertEqual(result["AAPL"].price, 150.0)
        self.assertEqual(result["MSFT"].price, 300.0)
        self.assertEqual(result["AAPL"].provider, "webull")

    def test_batch_quotes_parses_decimal_string_volume(self):
        """Same regression as TestGetQuote's decimal-string-volume case,
        for the batch snapshot path."""
        import time as _time
        now_ms = int(_time.time() * 1000)
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"symbol": "TSLA", "price": 430.0, "volume": "57259716.57259716", "quote_time": now_ms},
        ]
        mock_resp.text = json.dumps(mock_resp.json.return_value)
        mock_data.market_data.get_snapshot.return_value = mock_resp
        p = _make_provider(mock_data)
        result = p.get_batch_quotes(["TSLA"])
        self.assertEqual(result["TSLA"].volume, 57259716)

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
        mock_resp.text = json.dumps(mock_resp.json.return_value)
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
        mock_resp.text = json.dumps(snapshot_response)
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


# ---------------------------------------------------------------------------
# Extended-hours (pre-market/after-hours) bars — confirmed live 2026-09-09:
# Webull's ``trading_sessions`` param supports PRE/RTH/ATH at 1m resolution.
# ---------------------------------------------------------------------------
class TestExtendedHoursBars(unittest.TestCase):
    def _make_provider(self, bars_response: list[dict]) -> WebullProvider:
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = bars_response
        mock_resp.text = json.dumps(bars_response)
        mock_data.market_data.get_history_bar.return_value = mock_resp
        return _make_provider(mock_data)

    def test_default_does_not_request_extended_hours(self):
        """Backward compat: without the flag, trading_sessions stays unset
        (None) — identical request shape to every call site written before
        this feature existed."""
        p = self._make_provider([])
        p.get_historical_bars("aapl", timeframe="1d", range_="5d")
        _, kwargs = p._data_client.market_data.get_history_bar.call_args
        self.assertIsNone(kwargs.get("trading_sessions"))

    def test_include_extended_hours_requests_pre_rth_ath(self):
        p = self._make_provider([])
        p.get_historical_bars(
            "aapl", timeframe="1m", range_="1d", include_extended_hours=True,
        )
        _, kwargs = p._data_client.market_data.get_history_bar.call_args
        self.assertEqual(kwargs.get("trading_sessions"), ["PRE", "RTH", "ATH"])

    def test_include_extended_hours_ignored_for_non_1m_timeframe(self):
        """Extended hours only means anything at 1m resolution in this
        pipeline — a 1d request with the flag set must not send
        trading_sessions (matches the docstring: 'Ignored for any other
        timeframe')."""
        p = self._make_provider([])
        p.get_historical_bars(
            "aapl", timeframe="1d", range_="5d", include_extended_hours=True,
        )
        _, kwargs = p._data_client.market_data.get_history_bar.call_args
        self.assertIsNone(kwargs.get("trading_sessions"))

    def test_paginated_path_also_requests_extended_hours(self):
        """A multi-day 1m range routes through _fetch_1m_paginated — confirm
        trading_sessions survives that path too, not just the single-page one."""
        p = self._make_provider([])
        p.get_historical_bars(
            "aapl", timeframe="1m", range_="15d", include_extended_hours=True,
        )
        _, kwargs = p._data_client.market_data.get_history_bar.call_args
        self.assertEqual(kwargs.get("trading_sessions"), ["PRE", "RTH", "ATH"])

    def test_session_classification_premarket(self):
        """A bar timestamped 08:45 ET (naive NY, this module's convention)
        must be classified 'premarket' (04:00-09:30 ET)."""
        from datetime import datetime as _dt
        ts_ms = int(_dt(2026, 9, 9, 8, 45, tzinfo=_webull_module._NY_TZ).timestamp() * 1000)
        p = self._make_provider([
            {"time": ts_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
        ])
        bars = p.get_historical_bars(
            "aapl", timeframe="1m", range_="1d", include_extended_hours=True,
        )
        self.assertEqual(bars[0].session, "premarket")

    def test_session_classification_regular(self):
        from datetime import datetime as _dt
        ts_ms = int(_dt(2026, 9, 9, 10, 0, tzinfo=_webull_module._NY_TZ).timestamp() * 1000)
        p = self._make_provider([
            {"time": ts_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
        ])
        bars = p.get_historical_bars("aapl", timeframe="1m", range_="1d")
        self.assertEqual(bars[0].session, "regular")

    def test_session_classification_after_hours(self):
        from datetime import datetime as _dt
        ts_ms = int(_dt(2026, 9, 9, 17, 30, tzinfo=_webull_module._NY_TZ).timestamp() * 1000)
        p = self._make_provider([
            {"time": ts_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
        ])
        bars = p.get_historical_bars(
            "aapl", timeframe="1m", range_="1d", include_extended_hours=True,
        )
        self.assertEqual(bars[0].session, "after_hours")

    def test_default_1m_fetch_still_classifies_regular_session_correctly(self):
        """Even without include_extended_hours, a bar that happens to be
        classified must default correctly — this documents that session
        tagging is independent of whether extended hours were requested
        (Webull just wouldn't return a PRE/ATH row in that case)."""
        from datetime import datetime as _dt
        ts_ms = int(_dt(2026, 9, 9, 11, 0, tzinfo=_webull_module._NY_TZ).timestamp() * 1000)
        p = self._make_provider([
            {"time": ts_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
        ])
        bars = p.get_historical_bars("aapl", timeframe="1m", range_="1d")
        self.assertEqual(bars[0].session, "regular")


class TestRecentWindowBarCount(unittest.TestCase):
    """A "15m" range is a 15-MINUTE lookback (30 bars), not a trading day.

    ``_RANGE_DAYS["15m"] == 1`` made every 60s 1m-ingest tick request a whole
    extended-hours day: 891 bars per symbol, ~22,000 rows upserted per cycle
    (measured in the logs), ~99% of them unchanged.
    """

    def _provider(self, response) -> WebullProvider:
        mock_data = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = response
        resp.text = json.dumps(response)
        mock_data.market_data.get_history_bar.return_value = resp
        mock_data.market_data.get_batch_history_bar.return_value = resp
        return _make_provider(mock_data)

    def test_target_bars_helper(self):
        from backend.market_data.providers.webull_provider import _m1_target_bars

        # Recent-window ranges are an explicit bar count, session-independent.
        self.assertEqual(_m1_target_bars("15m", True), 30)
        self.assertEqual(_m1_target_bars("15m", False), 30)
        # Day-based ranges are unchanged.
        self.assertEqual(_m1_target_bars("1d", False), 390)
        self.assertEqual(_m1_target_bars("1d", True), 891)
        self.assertEqual(_m1_target_bars("5d", True), 5 * 390 * 16 // 7)
        self.assertEqual(_m1_target_bars("1mo", False), 22 * 390)
        self.assertEqual(_m1_target_bars("no-such-range", False), 65 * 390)  # legacy default

    def test_batch_15m_requests_30_bars_not_a_full_day(self):
        p = self._provider({"result": []})
        p.get_historical_bars_batch(["AAPL", "MSFT"], "1m", range_="15m",
                                    include_extended_hours=True)
        _, kwargs = p._data_client.market_data.get_batch_history_bar.call_args
        self.assertEqual(kwargs["count"], "30")
        self.assertEqual(kwargs["trading_sessions"], ["PRE", "RTH", "ATH"])

    def test_batch_day_range_still_requests_a_full_day(self):
        p = self._provider({"result": []})
        p.get_historical_bars_batch(["AAPL"], "1m", range_="1d", include_extended_hours=True)
        _, kwargs = p._data_client.market_data.get_batch_history_bar.call_args
        self.assertEqual(kwargs["count"], "891")

    def test_single_symbol_paginated_path_honours_15m(self):
        """_fetch_1m_paginated used to ignore its ``count`` and recompute the
        target from _RANGE_DAYS, so fixing only the batch path was not enough."""
        p = self._provider([])
        p.get_historical_bars("aapl", timeframe="1m", range_="15m",
                              include_extended_hours=True)
        self.assertEqual(p._data_client.market_data.get_history_bar.call_count, 1)
        _, kwargs = p._data_client.market_data.get_history_bar.call_args
        self.assertEqual(kwargs["count"], "30")

    def test_single_symbol_rth_only_15m(self):
        p = self._provider([])
        p.get_historical_bars("aapl", timeframe="1m", range_="15m")
        _, kwargs = p._data_client.market_data.get_history_bar.call_args
        self.assertEqual(kwargs["count"], "30")  # was 390

    def test_multi_day_pagination_is_unchanged(self):
        p = self._provider([])
        p.get_historical_bars("aapl", timeframe="1m", range_="5d",
                              include_extended_hours=True)
        _, kwargs = p._data_client.market_data.get_history_bar.call_args
        self.assertEqual(kwargs["count"], "1200")  # page size cap, as before


class TestExtendedHoursQuotes(unittest.TestCase):
    def _make_snapshot_provider(self, field: dict) -> WebullProvider:
        mock_data = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [field]
        mock_resp.text = json.dumps([field])
        mock_data.market_data.get_snapshot.return_value = mock_resp
        return _make_provider(mock_data)

    def test_get_quote_always_requests_extend_hour_required(self):
        """extend_hour_required=True is safe to always send — confirmed live
        it needs no special subscription (unlike overnight_required)."""
        p = self._make_snapshot_provider({"symbol": "AAPL", "price": "100.0"})
        p.get_quote("aapl")
        _, kwargs = p._data_client.market_data.get_snapshot.call_args
        self.assertTrue(kwargs.get("extend_hour_required"))

    def test_get_quote_parses_extended_hours_fields(self):
        p = self._make_snapshot_provider({
            "symbol": "AAPL",
            "price": "150.00",
            "extend_hour_last_price": "151.25",
            "extend_hour_change": "1.25",
            "extend_hour_change_ratio": "0.0084",
            "extend_hour_high": "151.50",
            "extend_hour_low": "150.90",
            "extend_hour_volume": "12345",
        })
        quote = p.get_quote("aapl")
        self.assertEqual(quote.extended_hours_price, 151.25)
        self.assertEqual(quote.extended_hours_change, 1.25)
        self.assertEqual(quote.extended_hours_high, 151.50)
        self.assertEqual(quote.extended_hours_low, 150.90)
        self.assertEqual(quote.extended_hours_volume, 12345)

    def test_get_quote_extended_hours_fields_none_when_absent(self):
        """No premarket/after-hours activity → Webull omits the extend_hour_*
        keys entirely; every extended_hours_* field on Quote must be None,
        not 0 or an error."""
        p = self._make_snapshot_provider({"symbol": "AAPL", "price": "150.00"})
        quote = p.get_quote("aapl")
        self.assertIsNone(quote.extended_hours_price)
        self.assertIsNone(quote.extended_hours_volume)

    def test_get_batch_quotes_also_requests_extend_hour_required(self):
        p = self._make_snapshot_provider({"symbol": "AAPL", "price": "100.0"})
        p.get_batch_quotes(["aapl"])
        _, kwargs = p._data_client.market_data.get_snapshot.call_args
        self.assertTrue(kwargs.get("extend_hour_required"))


# ---------------------------------------------------------------------------
# set_file_logger dedup guard (2026-09-09 — see module docstring on
# _file_logger_paths_registered for why this exists)
# ---------------------------------------------------------------------------
class TestSetFileLoggerDedup(unittest.TestCase):
    """Every WebullProvider() construction gets its own fresh ApiClient, so
    the SDK's own per-instance guard (``ApiClient._file_logger_set``) never
    stops a second construction from adding a second handler to the
    process-global 'webull.core' logger. Our patch must dedup across
    constructions by (logger_name, path) instead."""

    def setUp(self):
        # Isolate from real construction activity (this session's own
        # process may have already registered the real log path).
        self._saved = set(_webull_module._file_logger_paths_registered)
        _webull_module._file_logger_paths_registered.clear()

    def tearDown(self):
        _webull_module._file_logger_paths_registered.clear()
        _webull_module._file_logger_paths_registered.update(self._saved)

    def test_first_call_invokes_the_real_setup(self):
        fake_self = MagicMock()
        with patch.object(_webull_module, "_orig_set_file_logger") as orig:
            _webull_module._patched_set_file_logger(fake_self, "/tmp/some.log")
        orig.assert_called_once()

    def test_second_call_same_path_skips_the_real_setup(self):
        """This is the actual leak: a second WebullProvider() construction
        must not add a second handler for the same logger/path."""
        fake_self_1 = MagicMock()
        fake_self_2 = MagicMock()
        with patch.object(_webull_module, "_orig_set_file_logger") as orig:
            _webull_module._patched_set_file_logger(fake_self_1, "/tmp/some.log")
            _webull_module._patched_set_file_logger(fake_self_2, "/tmp/some.log")
        orig.assert_called_once()

    def test_second_call_still_marks_flag_on_its_own_instance(self):
        """The SDK checks ``api_client._file_logger_set`` before calling at
        all — a skipped call must still leave that flag True so a later,
        unrelated instance-scoped check on THIS instance doesn't loop back
        and call in a third time."""
        fake_self_1 = MagicMock()
        fake_self_2 = MagicMock()
        with patch.object(_webull_module, "_orig_set_file_logger"):
            _webull_module._patched_set_file_logger(fake_self_1, "/tmp/some.log")
            _webull_module._patched_set_file_logger(fake_self_2, "/tmp/some.log")
        self.assertTrue(fake_self_2._file_logger_set)

    def test_different_path_is_not_deduped(self):
        fake_self_1 = MagicMock()
        fake_self_2 = MagicMock()
        with patch.object(_webull_module, "_orig_set_file_logger") as orig:
            _webull_module._patched_set_file_logger(fake_self_1, "/tmp/some.log")
            _webull_module._patched_set_file_logger(fake_self_2, "/tmp/other.log")
        self.assertEqual(orig.call_count, 2)

    def test_different_logger_name_is_not_deduped(self):
        fake_self_1 = MagicMock()
        fake_self_2 = MagicMock()
        with patch.object(_webull_module, "_orig_set_file_logger") as orig:
            _webull_module._patched_set_file_logger(fake_self_1, "/tmp/some.log", logger_name="webull.core")
            _webull_module._patched_set_file_logger(fake_self_2, "/tmp/some.log", logger_name="webull.data")
        self.assertEqual(orig.call_count, 2)


class TestGetHistoricalBarsBatchChunking(unittest.TestCase):
    def test_chunks_when_more_than_20_symbols(self):
        symbols = [f"SYM{i:02d}" for i in range(25)]
        mock_data = MagicMock()

        call_counts: list[int] = []

        def _fake_resp(*args: object, **kwargs: object) -> MagicMock:
            call_counts.append(len(args[0]))  # type: ignore[arg-type]
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "result": [{"symbol": s, "result": []} for s in args[0]]  # type: ignore[union-attr]
            }
            return resp

        mock_data.market_data.get_batch_history_bar.side_effect = _fake_resp
        provider = _make_provider(mock_data)

        result = provider.get_historical_bars_batch(symbols, "1d")

        self.assertEqual(call_counts, [20, 5])
        self.assertEqual(set(result.keys()), {s.upper() for s in symbols})


if __name__ == "__main__":
    unittest.main()
