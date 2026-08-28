"""
Unit tests for ``backend.backtesting.replay`` helpers.

All functions are pure and do not touch the database, so testing
requires only the indicator implementations and the Pydantic Bar model.
"""
import math
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.backtesting.replay import (
    HIGH_VOLUME_MULTIPLIER,
    VOLUME_LOOKBACK,
    _bars_to_dicts,
    build_indicator_values,
    build_scan_result,
    relative_volume,
)
from backend.models.market_data import Bar, DataStatus


def _bar(close, high=None, low=None, volume=1_000_000, days_ago=0):
    """Make a synthetic Bar for testing."""
    ts = datetime(2025, 1, 1) + timedelta(days=days_ago)
    high = high or (close * 1.01)
    low = low or (close * 0.99)
    return Bar(
        symbol="AAPL", timestamp=ts, open=close, high=high, low=low,
        close=close, volume=volume, timeframe="1d",
        provider="test", data_status=DataStatus.LIVE,
    )


class TestBarsToDicts(unittest.TestCase):

    def test_converts_close_high_low(self):
        bars = [_bar(100.0), _bar(101.0)]
        dicts = _bars_to_dicts(bars)
        self.assertEqual(dicts[0], {"close": 100.0, "high": 101.0, "low": 99.0})
        self.assertEqual(dicts[1], {"close": 101.0, "high": 102.01, "low": 99.99})

    def test_empty_list_returns_empty(self):
        self.assertEqual(_bars_to_dicts([]), [])


class TestBuildIndicatorValues(unittest.TestCase):

    def _rsi_window(self, closes):
        """Return len(closes) bars ordered oldest→newest, last is "current"."""
        n = len(closes)
        bars = [_bar(c, volume=500_000, days_ago=n - 1 - i) for i, c in enumerate(closes)]
        return bars

    def test_empty_window_returns_defaults(self):
        result = build_indicator_values([], 1_000_000)
        self.assertIsNone(result["rsi"])
        self.assertIsNone(result["macd"])
        self.assertEqual(result["volume"], 1_000_000)
        self.assertIsNone(result["close"])

    def test_rsi_oversold_triggered(self):
        # First 14 flat (~50 RSI), last 6 declining to RSI < 30.
        closes = [100.0] * 14 + [90, 80, 70, 60, 55, 40]
        window = self._rsi_window(closes)
        result = build_indicator_values(window, 1_000_000)
        self.assertIsNotNone(result["rsi"])
        self.assertLess(result["rsi"], 30)

    def test_rsi_overbought_triggered(self):
        closes = [100.0] * 14 + [110, 120, 130, 140, 145, 160]
        window = self._rsi_window(closes)
        result = build_indicator_values(window, 1_000_000)
        self.assertIsNotNone(result["rsi"])
        self.assertGreater(result["rsi"], 70)

    def test_macd_positive_when_closing_up(self):
        # 60 bars of exponential uptrend: MACD histogram should be positive.
        closes = [100.0 * math.exp(i * 0.01) for i in range(60)]
        window = self._rsi_window(closes)
        result = build_indicator_values(window, 1_000_000)
        self.assertIsNotNone(result["macd"])
        self.assertGreater(result["macd"], 0)

    def test_macd_negative_when_closing_down(self):
        # 60 bars of oscillatory price: should produce both + and - histogram.
        closes = [100.0 + 20 * math.sin(i / 3) for i in range(60)]
        window = self._rsi_window(closes)
        result = build_indicator_values(window, 1_000_000)
        self.assertIsNotNone(result["macd"])
        self.assertLess(result["macd"], 0)

    def test_close_equals_last_bar_close(self):
        closes = [100.0, 101.0, 102.0]
        window = self._rsi_window(closes[:14] + [102.0])
        result = build_indicator_values(window, 1_000_000)
        self.assertEqual(result["close"], 102.0)


class TestBuildScanResult(unittest.TestCase):

    def test_trend_signals_left_empty(self):
        # Build a window of warmup bars + current bar.
        bars = [_bar(100.0 + i, volume=500_000, days_ago=14 - i) for i in range(15)]
        result = build_scan_result("AAPL", datetime(2025, 1, 16), bars)
        self.assertEqual(result.symbol, "AAPL")
        self.assertEqual(result.timestamp, datetime(2025, 1, 16))
        self.assertEqual(result.trend_signals, {})
        self.assertIn("rsi", result.indicator_values)

    def test_result_has_empty_signal_list(self):
        bars = [_bar(100.0, volume=500_000, days_ago=14 - i) for i in range(15)]
        result = build_scan_result("AAPL", datetime(2025, 1, 16), bars)
        self.assertIsInstance(result.signals, list)


class TestRelativeVolume(unittest.TestCase):

    def test_empty_history_returns_zero(self):
        self.assertEqual(relative_volume(1_000_000, []), 0.0)

    def test_current_equals_mean_returns_one(self):
        bars = [_bar(100.0, volume=1_000_000) for _ in range(20)]
        self.assertAlmostEqual(relative_volume(1_000_000, bars), 1.0)

    def test_twice_mean_returns_two(self):
        bars = [_bar(100.0, volume=500_000) for _ in range(20)]
        result = relative_volume(1_000_000, bars)
        self.assertAlmostEqual(result, 2.0)

    def test_high_volume_multiplier_threshold(self):
        bars = [_bar(100.0, volume=500_000) for _ in range(20)]
        rel = relative_volume(999_999, bars)  # just under 2×
        self.assertLess(rel, HIGH_VOLUME_MULTIPLIER)

        rel = relative_volume(1_000_001, bars)  # just over 2×
        self.assertGreaterEqual(rel, HIGH_VOLUME_MULTIPLIER)

    def test_volume_lookback_respected(self):
        # Only the last VOLUME_LOOKBACK bars are used.
        # First 10 bars have volume=100k, last 20 have volume=500k.
        bars = (
            [_bar(100.0, volume=100_000) for _ in range(10)] +
            [_bar(100.0, volume=500_000) for _ in range(VOLUME_LOOKBACK)]
        )
        rel = relative_volume(500_000, bars)
        # Should be based on last VOLUME_LOOKBACK bars with mean=500k → ratio=1.
        self.assertAlmostEqual(rel, 1.0, places=1)


if __name__ == "__main__":
    unittest.main()
