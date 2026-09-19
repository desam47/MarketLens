"""
Tests for Yahoo Finance market data provider
"""
import json
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))

from backend.market_data.providers.yfinance_provider import YFinanceProvider
from backend.models.market_data import (
    Bar,
    DataStatus,
    ProviderCapabilities,
    Quote,
)


class TestYFinanceProvider(unittest.TestCase):

    def setUp(self):
        self.provider = YFinanceProvider()

    @patch.object(YFinanceProvider, '_fetch_chart')
    def test_get_quote_success(self, mock_fetch_chart):
        """Test successful quote retrieval"""
        # The provider calls _fetch_chart(...) then reads meta/regularMarketPrice
        # from the response. Mock at that layer to avoid live HTTP.
        mock_fetch_chart.return_value = {
            "meta": {
                "regularMarketPrice": 150.0,
                "regularMarketTime": 1700000000,
                "bid": 149.5,
                "ask": 150.5,
                "regularMarketVolume": 1000000,
            }
        }

        # Test
        quote = self.provider.get_quote("AAPL")

        # Assertions
        self.assertIsInstance(quote, Quote)
        self.assertEqual(quote.symbol, "AAPL")
        self.assertEqual(quote.price, 150.0)
        self.assertEqual(quote.bid, 149.5)
        self.assertEqual(quote.ask, 150.5)
        self.assertEqual(quote.volume, 1000000)
        self.assertEqual(quote.provider, "yahoo_finance")
        self.assertEqual(quote.data_status, DataStatus.DELAYED)
        self.assertIsInstance(quote.timestamp, datetime)

    @patch.object(YFinanceProvider, '_fetch_chart')
    def test_get_quote_failure(self, mock_fetch_chart):
        """Test quote retrieval failure handling"""
        # Mock _fetch_chart to raise so get_quote propagates the exception
        mock_fetch_chart.side_effect = Exception("Network error")

        # Test that exception is raised
        with self.assertRaises(Exception):
            self.provider.get_quote("INVALID")

    @patch.object(YFinanceProvider, '_fetch_chart')
    def test_get_historical_bars_success(self, mock_fetch_chart):
        """Test successful historical bars retrieval"""
        import pandas as pd

        dates = pd.date_range(end=datetime.now(), periods=5, freq='1D')
        timestamps = [int(d.timestamp()) for d in dates]
        mock_fetch_chart.return_value = {
            "timestamp": timestamps,
            "indicators": {
                "quote": [{
                    "open":  [100.0, 101.0, 102.0, 103.0, 104.0],
                    "high":  [105.0, 106.0, 107.0, 108.0, 109.0],
                    "low":   [95.0, 96.0, 97.0, 98.0, 99.0],
                    "close": [103.0, 104.0, 105.0, 106.0, 107.0],
                    "volume": [1000, 1100, 1200, 1300, 1400],
                }]
            }
        }

        # Test with explicit timeframe + range
        bars = self.provider.get_historical_bars("AAPL", timeframe="1d", range_="5d")

        self.assertIsInstance(bars, list)
        self.assertEqual(len(bars), 5)
        for bar in bars:
            self.assertIsInstance(bar, Bar)
            self.assertEqual(bar.symbol, "AAPL")
            self.assertEqual(bar.timeframe, "1d")
            self.assertEqual(bar.provider, "yahoo_finance")
            self.assertEqual(bar.data_status, DataStatus.HISTORICAL)
            self.assertIsInstance(bar.timestamp, datetime)
            self.assertGreater(bar.close, 0)
        # Verify chronological order
        for i in range(len(bars) - 1):
            self.assertLess(bars[i].timestamp, bars[i + 1].timestamp)

    @patch.object(YFinanceProvider, '_fetch_chart')
    def test_get_historical_bars_empty(self, mock_fetch_chart):
        """Test that empty chart data returns an empty list without error"""
        mock_fetch_chart.return_value = {
            "timestamp": [],
            "indicators": {"quote": [{}]}
        }

        bars = self.provider.get_historical_bars("NOVALID", timeframe="1d", range_="5d")
        self.assertEqual(bars, [])

    @patch.object(YFinanceProvider, '_fetch_chart')
    def test_get_historical_bars_with_null_closes(self, mock_fetch_chart):
        """Null closes in chart data should be skipped rather than cause errors"""
        import pandas as pd

        dates = pd.date_range(end=datetime.now(), periods=3, freq='1D')
        timestamps = [int(d.timestamp()) for d in dates]
        mock_fetch_chart.return_value = {
            "timestamp": timestamps,
            "indicators": {
                "quote": [{
                    "open":  [100.0, None, 102.0],
                    "high":  [105.0, 106.0, 107.0],
                    "low":   [95.0, 96.0, 97.0],
                    "close": [103.0, None, 105.0],  # middle bar has null close
                    "volume": [1000, 1100, 1200],
                }]
            }
        }

        bars = self.provider.get_historical_bars("AAPL", timeframe="1d", range_="3d")
        # The middle bar should be skipped; only 2 bars returned
        self.assertEqual(len(bars), 2)
        for bar in bars:
            self.assertIsNotNone(bar.close)

    @patch.object(YFinanceProvider, '_fetch_chart')
    def test_get_historical_bars_drops_boundary_aligned_flat_zero_volume_last_row(self, mock_fetch_chart):
        """A live-snapshot trailing row that happens to land exactly on a
        clean interval boundary (seconds == 0) must still be dropped.

        Regression for a variant of the already-handled trailing-snapshot
        case: the existing guard only caught snapshots stamped at a
        non-zero second (e.g. 15:15:47). Found live 2026-09-09 via a "why
        do 4h bars only cover 08:00/12:00" question — a 1h ("60m")
        snapshot bar for DVLT landed exactly at 16:00:00 with
        open==high==low==close and volume==0, which the seconds-only check
        didn't flag, and which then broke the 4h resample built on top of
        it (a single degenerate 1h bar aggregating into a degenerate 4h
        "close" candle).
        """
        import pandas as pd

        dates = pd.date_range(end=datetime.now(), periods=3, freq='h')
        # Force the LAST timestamp onto an exact minute boundary (seconds=0)
        # — the whole point of this test is that the seconds-based check
        # alone would NOT catch this row.
        timestamps = [int(d.timestamp()) - (int(d.timestamp()) % 60) for d in dates]
        mock_fetch_chart.return_value = {
            "timestamp": timestamps,
            "indicators": {
                "quote": [{
                    "open":   [100.0, 101.0, 5.05],
                    "high":   [105.0, 106.0, 5.05],
                    "low":    [95.0, 96.0, 5.05],
                    "close":  [103.0, 104.0, 5.05],  # last row: flat OHLC
                    "volume": [1000, 1100, 0],        # last row: zero volume
                }]
            }
        }

        bars = self.provider.get_historical_bars("DVLT", timeframe="1h", range_="1d")
        # Only the 2 genuine bars should survive; the synthetic 3rd is dropped.
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1].close, 104.0)

    def test_get_historical_bars_keeps_genuine_flat_bar_with_real_volume(self):
        """A real bar that legitimately has open==close (no price movement)
        must NOT be dropped just for being flat — only flat AND
        zero-volume together indicate a synthetic snapshot."""
        import pandas as pd

        dates = pd.date_range(end=datetime.now(), periods=2, freq='h')
        timestamps = [int(d.timestamp()) - (int(d.timestamp()) % 60) for d in dates]
        with patch.object(YFinanceProvider, '_fetch_chart') as mock_fetch_chart:
            mock_fetch_chart.return_value = {
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{
                        "open":   [100.0, 5.05],
                        "high":   [105.0, 5.05],
                        "low":    [95.0, 5.05],
                        "close":  [103.0, 5.05],   # last row: flat OHLC...
                        "volume": [1000, 500],      # ...but genuine volume
                    }]
                }
            }
            bars = self.provider.get_historical_bars("DVLT", timeframe="1h", range_="1d")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1].close, 5.05)

    @patch.object(YFinanceProvider, '_fetch_chart')
    def test_get_historical_bars_unknown_timeframe_raises(self, mock_fetch_chart):
        """An unsupported timeframe string should raise ValueError from _resolve_interval"""
        mock_fetch_chart.return_value = {"timestamp": [], "indicators": {"quote": [{}]}}
        with self.assertRaises(ValueError):
            self.provider.get_historical_bars("AAPL", timeframe="INVALID_TF", range_="3mo")

    @patch.object(YFinanceProvider, '_fetch_chart')
    def test_get_historical_bars_passes_resolved_interval(self, mock_fetch_chart):
        """Verify timeframe→interval resolution reaches the network call."""
        import pandas as pd

        dates = pd.date_range(end=datetime.now(), periods=3, freq='1D')
        timestamps = [int(d.timestamp()) for d in dates]
        mock_fetch_chart.return_value = {
            "timestamp": timestamps,
            "indicators": {
                "quote": [{
                    "open":  [100.0, 101.0, 102.0],
                    "high":  [105.0, 106.0, 107.0],
                    "low":   [95.0, 96.0, 97.0],
                    "close": [103.0, 104.0, 105.0],
                    "volume": [1000, 1100, 1200],
                }]
            }
        }

        # "1d" → "1d" (identity), "3mo" → "3mo" (identity)
        self.provider.get_historical_bars("AAPL", timeframe="1d", range_="3mo")
        args, kwargs = mock_fetch_chart.call_args
        # The provider passes the resolved interval + range into _fetch_chart
        self.assertEqual(kwargs.get("interval"), "1d")
        self.assertEqual(kwargs.get("range_"), "3mo")
        self.assertEqual(args[0], "AAPL")

    @patch.object(YFinanceProvider, '_fetch_chart')
    def test_get_latest_bar_success(self, mock_fetch_chart):
        """Test successful latest bar retrieval"""
        # Mock _fetch_chart to return controlled chart data
        import pandas as pd

        # Build a deterministic chart response matching what _rows() expects
        dates = pd.date_range(end=datetime.now(), periods=5, freq='1D')
        timestamps = [int(d.timestamp()) for d in dates]
        mock_fetch_chart.return_value = {
            "timestamp": timestamps,
            "indicators": {
                "quote": [{
                    "open":  [100, 101, 102, 103, 104],
                    "high":  [105, 106, 107, 108, 109],
                    "low":   [95, 96, 97, 98, 99],
                    "close": [103, 104, 105, 106, 107],
                    "volume": [1000, 1100, 1200, 1300, 1400],
                }]
            }
        }

        # Test
        bar = self.provider.get_latest_bar("AAPL", "1d")

        # Assertions
        self.assertIsInstance(bar, Bar)
        self.assertEqual(bar.symbol, "AAPL")
        self.assertEqual(bar.timeframe, "1d")
        self.assertEqual(bar.provider, "yahoo_finance")
        self.assertEqual(bar.data_status, DataStatus.DELAYED)
        self.assertIsInstance(bar.timestamp, datetime)
        self.assertGreater(bar.close, 0)

    def test_get_capabilities(self):
        """Test provider capabilities"""
        capabilities = self.provider.get_capabilities()

        self.assertIsInstance(capabilities, ProviderCapabilities)
        self.assertEqual(capabilities.provider_name, "yahoo_finance")
        self.assertTrue(capabilities.supports_historical_bars)
        self.assertTrue(capabilities.supports_latest_quote)
        self.assertTrue(capabilities.supports_latest_bar)
        self.assertTrue(capabilities.supports_batch_quotes)  # yfinance supports batch quotes
        self.assertTrue(capabilities.supports_market_status)
        self.assertEqual(capabilities.min_timeframe, "1m")
        self.assertEqual(capabilities.max_timeframe, "3mo")

    def test_is_available(self):
        """Test provider availability check"""
        with patch.object(self.provider, 'get_quote') as mock_get_quote:
            # Test when provider is healthy
            mock_get_quote.return_value = Quote(
                symbol="AAPL",
                price=150.0,
                timestamp=datetime.now(),
                provider="yahoo_finance",
                data_status=DataStatus.DELAYED
            )
            self.assertTrue(self.provider.is_available())

            # Test when provider fails
            mock_get_quote.side_effect = Exception("API error")
            self.assertFalse(self.provider.is_available())

# ---------------------------------------------------------------------------
# Batch quotes: Yahoo's v7 endpoint needs a session cookie + crumb
# ---------------------------------------------------------------------------
_MOD = "backend.market_data.providers.yfinance_provider"


def _resp(status=200, body=None, text=None):
    return SimpleNamespace(status_code=status, text=text if text is not None else json.dumps(body or {}))


def _quote_body(*symbols):
    return {"quoteResponse": {"result": [
        {"symbol": s, "regularMarketPrice": 100.0 + i, "regularMarketTime": 1789761600,
         "bid": 99.0, "ask": 101.0, "regularMarketVolume": 1000}
        for i, s in enumerate(symbols)]}}


class TestBatchQuotesAuth(unittest.TestCase):
    """The batch-quote call used to be anonymous and got HTTP 401 on every cycle
    (the live quote loop failed every ~30 s); it was also completely untested."""

    def setUp(self):
        self.provider = YFinanceProvider()
        self.crumb_fetches = 0
        self.quote_calls = []
        self.quote_status = [200]           # popped per quote call; the last one repeats
        self.crumb_response = lambda: _resp(200, text="crumb-123")
        patcher = patch(f"{_MOD}.curl_requests")
        self.cr = patcher.start()
        self.addCleanup(patcher.stop)

        def make_session(**kw):
            session = MagicMock()
            session.cookies.items.return_value = [("A3", "cookie-value")]

            def sget(url, **k):
                if "getcrumb" in url:
                    self.crumb_fetches += 1
                    return self.crumb_response()
                return _resp(404, text="")   # fc.yahoo.com
            session.get.side_effect = sget
            return session

        self.cr.Session.side_effect = make_session

        def qget(url, **k):
            self.quote_calls.append((url, k))
            status = self.quote_status.pop(0) if len(self.quote_status) > 1 else self.quote_status[0]
            if status != 200:
                return _resp(status, text='{"finance":{"error":{"code":"Unauthorized"}}}')
            return _resp(200, _quote_body(*k["params"]["symbols"].split(",")))
        self.cr.get.side_effect = qget

    def test_sends_the_crumb_and_cookie_and_parses_the_quotes(self):
        out = self.provider.get_batch_quotes(["AAPL", "MSFT"])
        _, kwargs = self.quote_calls[0]
        self.assertEqual(kwargs["params"], {"symbols": "AAPL,MSFT", "crumb": "crumb-123"})
        self.assertEqual(kwargs["cookies"], {"A3": "cookie-value"})
        self.assertEqual(set(out), {"AAPL", "MSFT"})
        self.assertEqual(out["AAPL"].price, 100.0)
        self.assertEqual(out["AAPL"].provider, "yahoo_finance")
        self.assertEqual(out["AAPL"].data_status, DataStatus.DELAYED)

    def test_crumb_is_cached_across_calls(self):
        self.provider.get_batch_quotes(["AAPL"])
        self.provider.get_batch_quotes(["MSFT"])
        self.assertEqual(self.crumb_fetches, 1)
        self.assertEqual(len(self.quote_calls), 2)

    def test_crumb_is_refetched_after_its_ttl(self):
        self.provider.get_batch_quotes(["AAPL"])
        self.provider._crumb_at -= 3601          # an hour and a second ago
        self.provider.get_batch_quotes(["AAPL"])
        self.assertEqual(self.crumb_fetches, 2)

    def test_a_401_refreshes_the_crumb_once_and_retries(self):
        self.quote_status = [401, 200]
        out = self.provider.get_batch_quotes(["AAPL"])
        self.assertEqual(out["AAPL"].price, 100.0)
        self.assertEqual(len(self.quote_calls), 2)
        self.assertEqual(self.crumb_fetches, 2)  # initial + the forced refresh

    def test_a_persistent_401_raises_after_exactly_one_retry(self):
        self.quote_status = [401]
        with self.assertRaises(RuntimeError) as ctx:
            self.provider.get_batch_quotes(["AAPL"])
        self.assertIn("HTTP 401", str(ctx.exception))
        self.assertEqual(len(self.quote_calls), 2)   # not an endless loop

    def test_a_failed_crumb_fetch_raises_a_clear_error(self):
        for bad in (lambda: _resp(429, text="Too Many Requests"),
                    lambda: _resp(200, text="<html>consent</html>"),
                    lambda: _resp(200, text="")):
            self.provider._crumb = None
            self.crumb_response = bad
            with self.assertRaises(RuntimeError) as ctx:
                self.provider.get_batch_quotes(["AAPL"])
            self.assertIn("crumb", str(ctx.exception).lower())
        self.assertEqual(self.quote_calls, [], "must not call the quote endpoint without a crumb")

    def test_special_symbols_are_passed_via_params_not_string_concatenation(self):
        self.provider.get_batch_quotes(["^VIX", "BRK-B", "BRK.B"])
        url, kwargs = self.quote_calls[0]
        self.assertNotIn("?", url)
        self.assertEqual(kwargs["params"]["symbols"], "^VIX,BRK-B,BRK.B")

    def test_a_symbol_missing_from_the_response_becomes_an_error_quote(self):
        self.cr.get.side_effect = lambda url, **k: _resp(200, _quote_body("AAPL"))
        out = self.provider.get_batch_quotes(["AAPL", "ZZZZ"])
        self.assertEqual(out["ZZZZ"].data_status, DataStatus.ERROR)
        self.assertEqual(out["ZZZZ"].price, 0.0)

    def test_empty_input_makes_no_network_calls(self):
        self.assertEqual(self.provider.get_batch_quotes([]), {})
        self.cr.get.assert_not_called()
        self.cr.Session.assert_not_called()

    def test_concurrent_callers_share_one_crumb_fetch(self):
        barrier = threading.Barrier(8)

        def call(_):
            barrier.wait()
            return self.provider.get_batch_quotes(["AAPL"])

        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(call, range(8)))
        self.assertTrue(all("AAPL" in r for r in results))
        self.assertEqual(self.crumb_fetches, 1)


if __name__ == '__main__':
    unittest.main()
