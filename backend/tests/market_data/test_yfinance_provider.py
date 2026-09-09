"""
Tests for Yahoo Finance market data provider
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

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

if __name__ == '__main__':
    unittest.main()
