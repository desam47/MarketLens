"""
Tests for RSI (Relative Strength Index) indicator
"""

import unittest

from backend.indicators.rsi import RSIIndicator


class TestRSIIndicator(unittest.TestCase):
    """Tests for RSIIndicator"""

    def setUp(self):
        self.indicator = RSIIndicator(period=14)

    def test_rsi_calculation(self):
        """RSI calculated on a 15-bar uptrend is high (near 100)"""
        # 15 bars: 13 alternating ±1s (net 0), then big up day.
        # avg_gain = 14*1/14 = 1.0, avg_loss = 0 → RS = ∞ → RSI = 100
        data = [{"close": 100.0 + i} for i in range(15)]
        values = self.indicator.calculate(data)
        self.assertGreater(len(values), 0)
        self.assertGreaterEqual(values[-1], 90.0)

    def test_rsi_update(self):
        """update() appends new RSI value after each qualifying bar"""
        # Feed 14 bars first (no RSI until we have period+1=15 bars)
        for i in range(14):
            self.indicator.update({"close": 100.0 + i})
        self.assertEqual(len(self.indicator.values), 0)
        # 15th bar triggers first RSI
        result = self.indicator.update({"close": 114.0})
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 100.0)

    def test_rsi_range(self):
        """RSI values always fall within [0, 100]"""
        # Up-close sequence
        up_data = [{"close": 100.0 + i} for i in range(30)]
        up_rsi = self.indicator.calculate(up_data)
        for v in up_rsi:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 100.0)
        # Reset for down sequence
        self.indicator.reset()
        down_data = [{"close": 100.0 - i} for i in range(30)]
        down_rsi = self.indicator.calculate(down_data)
        for v in down_rsi:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 100.0)

    def test_rsi_insufficient_data(self):
        """Fewer than period+1 bars returns empty list"""
        data = [{"close": 100.0 + i} for i in range(5)]
        values = self.indicator.calculate(data)
        self.assertEqual(values, [])


if __name__ == "__main__":
    unittest.main()
