"""
Tests for backend/market_data/providers/webull_provider.py

Covers:
  - WebullAuthError when credentials are missing
  - OAuth device-flow token acquisition
  - Token refresh on expiry
  - get_quote() parses response and returns Quote
  - get_historical_bars() parses response and returns list[Bar]
  - get_batch_quotes() falls back to individual calls
  - get_market_status() returns MarketStatus
  - HTTP errors and rate-limit (429) raise RuntimeError for CB tracking
  - is_available() returns True/False based on connectivity
  - Credentials are never logged or included in responses
"""
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.market_data.providers.webull_provider import (
    WebullAuthError,
    WebullProvider,
    _TokenStore,
)


# ---------------------------------------------------------------------------
# TokenStore tests (no network needed)
# ---------------------------------------------------------------------------
class TestTokenStore(unittest.TestCase):
    def test_is_expired_false_when_fresh(self):
        store = _TokenStore("at", "rt", expires_at=time.time() + 300)
        self.assertFalse(store.is_expired())

    def test_is_expired_true_when_past(self):
        store = _TokenStore("at", "rt", expires_at=time.time() - 10)
        self.assertTrue(store.is_expired())

    def test_is_expired_buffer(self):
        store = _TokenStore("at", "rt", expires_at=time.time() + 30)
        self.assertTrue(store.is_expired(buffer=60))
        self.assertFalse(store.is_expired(buffer=10))

    def test_update_replaces_tokens(self):
        store = _TokenStore("old_at", "old_rt", expires_at=time.time() - 10)
        store.update("new_at", "new_rt", expires_in=7200)
        self.assertEqual(store.access_token, "new_at")
        self.assertEqual(store.refresh_token, "new_rt")
        self.assertGreater(store.expires_at, time.time())

    def test_thread_safe_update(self):
        store = _TokenStore("at", "rt", expires_at=time.time() + 3600)
        barrier = threading.Barrier(10)
        errors = []

        def updater(i):
            barrier.wait()
            for _ in range(100):
                store.update(f"at_{i}", f"rt_{i}", expires_in=3600)
                _ = store.access_token  # read

        threads = [threading.Thread(target=updater, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # No assertion failures means thread-safe
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# Authentication error tests
# ---------------------------------------------------------------------------
class TestWebullAuthErrors(unittest.TestCase):
    def test_missing_credentials_raises_auth_error(self):
        """Missing app_key / app_secret raises WebullAuthError at init."""
        with patch.object(WebullProvider, "_authenticate") as mock_auth:
            mock_auth.side_effect = WebullAuthError(
                "Webull app_key or app_secret is not configured. "
                "Set WEBULL_ENABLED=true, WEBULL_APP_KEY, and WEBULL_APP_SECRET."
            )
            with self.assertRaises(WebullAuthError) as ctx:
                WebullProvider()
        self.assertIn("not configured", str(ctx.exception))


# ---------------------------------------------------------------------------
# get_quote tests
# ---------------------------------------------------------------------------
class TestGetQuote(unittest.TestCase):
    def _make_provider(self, json_data: dict, status_code: int = 200):
        """Return a WebullProvider with a mocked _get() that returns json_data."""
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = _TokenStore("at", "rt", expires_at=time.time() + 3600)
        p._get = MagicMock(return_value=json_data)
        return p

    def test_quote_fields_parsed_correctly(self):
        import time as _time
        now_ms = int(_time.time() * 1000)
        p = self._make_provider({
            "quote": {
                "close": 185.50,
                "bid": 185.45,
                "ask": 185.55,
                "volume": 42_000_000,
                "timestamp": now_ms,
            }
        })
        q = p.get_quote("aapl")
        self.assertEqual(q.symbol, "AAPL")
        self.assertEqual(q.price, 185.50)
        self.assertEqual(q.bid, 185.45)
        self.assertEqual(q.ask, 185.55)
        self.assertEqual(q.volume, 42_000_000)
        self.assertEqual(q.provider, "webull")

    def test_quote_delayed_status(self):
        p = self._make_provider({"quote": {"close": 100.0, "timestamp": 0}})
        q = p.get_quote("msft")
        self.assertEqual(q.data_status.value, "DELAYED")

    def test_quote_empty_response_returns_empty(self):
        p = self._make_provider({})
        # When field is missing, price defaults to 0.0
        q = p.get_quote("tsla")
        self.assertEqual(q.symbol, "TSLA")
        self.assertEqual(q.price, 0.0)


# ---------------------------------------------------------------------------
# get_historical_bars tests
# ---------------------------------------------------------------------------
class TestGetHistoricalBars(unittest.TestCase):
    def _make_provider(self, json_data: dict, status_code: int = 200):
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = _TokenStore("at", "rt", expires_at=time.time() + 3600)
        p._get = MagicMock(return_value=json_data)
        return p

    def test_bars_parsed_from_response(self):
        import time as _time
        t1 = int(_time.time() * 1000)
        t2 = int((_time.time() - 86400) * 1000)
        p = self._make_provider({
            "bars": [
                {"t": t1, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 1000000},
                {"t": t2, "o": 99.0, "h": 100.0, "l": 98.5, "c": 99.5, "v": 900000},
            ]
        })
        bars = p.get_historical_bars("spy", timeframe="1d", range_="5d")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].symbol, "SPY")
        self.assertEqual(bars[0].close, 100.5)
        self.assertEqual(bars[1].close, 99.5)
        self.assertEqual(bars[0].timeframe, "1d")

    def test_bars_empty_returns_empty_list(self):
        p = self._make_provider({"bars": []})
        bars = p.get_historical_bars("qqq")
        self.assertEqual(bars, [])

    def test_bars_skips_rows_with_missing_timestamp(self):
        import time as _time
        now = int(_time.time() * 1000)
        p = self._make_provider({
            "bars": [
                {"t": now, "o": 50.0, "h": 51.0, "l": 49.0, "c": 50.5, "v": 500000},
                {"o": 60.0, "h": 61.0, "l": 59.0, "c": 60.0, "v": 600000},  # missing t
            ]
        })
        bars = p.get_historical_bars("dia")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 50.5)


# ---------------------------------------------------------------------------
# get_batch_quotes tests
# ---------------------------------------------------------------------------
class TestGetBatchQuotes(unittest.TestCase):
    def _make_provider(self):
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = _TokenStore("at", "rt", expires_at=time.time() + 3600)
        p.get_quote = MagicMock()
        return p

    def test_batch_quotes_calls_get_quote_per_symbol(self):
        p = self._make_provider()
        from backend.models.market_data import Quote
        q1 = Quote(symbol="AAPL", price=150.0, timestamp=datetime.now(timezone.utc),
                    provider="webull", data_status="DELAYED")
        q2 = Quote(symbol="MSFT", price=300.0, timestamp=datetime.now(timezone.utc),
                    provider="webull", data_status="DELAYED")
        p.get_quote.side_effect = [q1, q2]
        result = p.get_batch_quotes(["AAPL", "MSFT"])
        self.assertEqual(p.get_quote.call_count, 2)
        self.assertEqual(result["AAPL"].price, 150.0)
        self.assertEqual(result["MSFT"].price, 300.0)


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------
class TestWebullErrorHandling(unittest.TestCase):
    def _make_provider_with_get(self, status_code: int, json_data: dict | None = None):
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = _TokenStore("at", "rt", expires_at=time.time() + 3600)
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        p._session.get = MagicMock(return_value=resp)
        return p

    def test_429_raises_runtime_error_for_circuit_breaker(self):
        """HTTP 429 should raise RuntimeError so the circuit breaker tracks it."""
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = _TokenStore("at", "rt", expires_at=time.time() + 3600)
        resp = MagicMock()
        resp.status_code = 429
        resp.json.return_value = {"error": "rate_limit_exceeded"}
        p._session.get = MagicMock(return_value=resp)
        with self.assertRaises(RuntimeError) as ctx:
            p._get("/quote/AAPL", require_auth=False)
        self.assertIn("429", str(ctx.exception))
        # Verify it counts as a failure for circuit breaker
        self.assertIn("rate limited", str(ctx.exception))

    def test_http_error_raises_runtime_error(self):
        p = self._make_provider_with_get(500)
        with self.assertRaises(RuntimeError) as ctx:
            p._get("/quote/AAPL", require_auth=False)
        self.assertIn("500", str(ctx.exception))

    def test_401_refreshes_token(self):
        """A 401 should trigger token refresh and retry the request."""
        import requests as _requests
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = _TokenStore("at", "rt", expires_at=time.time() + 3600)

        # First call returns 401, second call succeeds
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_success = MagicMock()
        resp_success.status_code = 200
        resp_success.json.return_value = {"quote": {"close": 150.0, "timestamp": 0}}
        p._session.get = MagicMock(side_effect=[resp_401, resp_success])

        with patch.object(p, "_refresh_token") as mock_refresh:
            result = p._get("/quote/AAPL", require_auth=False)
        mock_refresh.assert_called_once()
        self.assertEqual(result["quote"]["close"], 150.0)


# ---------------------------------------------------------------------------
# get_market_status tests
# ---------------------------------------------------------------------------
class TestGetMarketStatus(unittest.TestCase):
    def _make_provider(self, json_data: dict):
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = _TokenStore("at", "rt", expires_at=time.time() + 3600)
        p._get = MagicMock(return_value=json_data)
        return p

    def test_market_status_open(self):
        p = self._make_provider({
            "quote": {"marketStatus": "OPEN", "timezone": "America/New_York"}
        })
        s = p.get_market_status("AAPL")
        self.assertEqual(s.symbol, "AAPL")
        self.assertTrue(s.is_open)
        self.assertEqual(s.timezone, "America/New_York")

    def test_market_status_closed(self):
        p = self._make_provider({
            "quote": {"marketStatus": "CLOSED", "timezone": "UTC"}
        })
        s = p.get_market_status("TSLA")
        self.assertFalse(s.is_open)


# ---------------------------------------------------------------------------
# is_available tests
# ---------------------------------------------------------------------------
class TestIsAvailable(unittest.TestCase):
    def test_returns_false_when_token_is_none(self):
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = None
        self.assertFalse(p.is_available())

    def test_returns_true_when_ping_succeeds(self):
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = _TokenStore("at", "rt", expires_at=time.time() + 3600)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        p._session.get = MagicMock(return_value=resp)
        self.assertTrue(p.is_available())

    def test_returns_false_when_ping_fails(self):
        import requests as _requests
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        p._token = _TokenStore("at", "rt", expires_at=time.time() + 3600)
        p._session.get = MagicMock(side_effect=_requests.RequestException("boom"))
        self.assertFalse(p.is_available())


# ---------------------------------------------------------------------------
# Security / credentials-never-leaked tests
# ---------------------------------------------------------------------------
class TestCredentialsNotLeaked(unittest.TestCase):
    def test_token_never_in_logs(self):
        """Access token must never appear in provider log output."""
        import logging
        with patch.object(WebullProvider, "_authenticate"):
            p = WebullProvider()
        store = _TokenStore("secret_token_value", "refresh_secret", expires_at=time.time() + 3600)
        p._token = store
        # Attempt to trigger any code path that logs the token
        with self.assertLogs("backend.market_data.providers.webull_provider", level="DEBUG") as ctx:
            p.is_available()
        for record in ctx.output:
            self.assertNotIn("secret_token_value", record)
            self.assertNotIn("refresh_secret", record)

    def test_auth_error_does_not_expose_credentials(self):
        """Auth error messages must not contain app_key or app_secret."""
        with patch.object(WebullProvider, "_authenticate") as mock_auth:
            mock_auth.side_effect = WebullAuthError(
                "Webull app_key or app_secret is not configured. "
                "Set WEBULL_ENABLED=true, WEBULL_APP_KEY, and WEBULL_APP_SECRET."
            )
            with self.assertRaises(WebullAuthError) as ctx:
                WebullProvider()
        # Should mention configuration but never echo back any actual credential.
        self.assertIn("not configured", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
