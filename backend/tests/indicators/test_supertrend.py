"""
Tests for SuperTrend indicator
"""
import unittest

from backend.indicators.supertrend import SuperTrendIndicator


class TestSuperTrendIndicator(unittest.TestCase):
    """Tests for SuperTrendIndicator"""

    def setUp(self):
        self.indicator = SuperTrendIndicator(atr_period=10, multiplier=3.0)

    def _make_trending_data(self, n: int = 50) -> list[dict[str, float]]:
        """Generate a strong uptrend with consistent ranges."""
        bars = []
        for i in range(n):
            base = 100.0 + i * 0.5
            bars.append({
                "open": base,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + 0.3,
            })
        return bars

    def test_supertrend_calculation(self):
        """SuperTrend produces one value per bar after warm-up."""
        data = self._make_trending_data(50)
        values = self.indicator.calculate(data)
        self.assertGreater(len(values), 0)
        # All values should be finite numbers
        for v in values:
            self.assertIsInstance(v, float)
            self.assertGreater(v, 0.0)

    def test_supertrend_update(self):
        """update() maintains the indicator state and produces a value."""
        data = self._make_trending_data(30)
        last_value = None
        for bar in data:
            last_value = self.indicator.update(bar)
        self.assertIsNotNone(last_value)
        self.assertGreater(last_value, 0.0)

    def test_supertrend_reset(self):
        """reset() clears internal state and values."""
        data = self._make_trending_data(20)
        for bar in data:
            self.indicator.update(bar)
        self.assertGreater(len(self.indicator.values), 0)
        self.indicator.reset()
        self.assertEqual(self.indicator.values, [])
        self.assertIsNone(self.indicator.get_latest())

    def test_update_matches_calculate_direction(self):
        """O(1) update() must agree with offline calculate() on the final
        trend direction (uptrend/downtrend) and on the sign of the value
        (above/below close). Exact decimal equivalence isn't possible
        because the offline path uses a simple MA over TRs while the
        online path uses Wilder smoothing — but the sign of the
        SuperTrend vs. close should match."""
        data = self._make_trending_data(50)
        # Offline path
        calc = SuperTrendIndicator(atr_period=10, multiplier=3.0)
        calc.calculate(data)
        # Online path
        for bar in data:
            self.indicator.update(bar)
        # The latest SuperTrend value from each path should both lie
        # *below* the latest close (this is an uptrend series).
        calc_last = calc.values[-1]
        online_last = self.indicator.values[-1]
        last_close = data[-1]["close"]
        self.assertLess(calc_last, last_close,
                        "Offline SuperTrend should be below close in uptrend")
        self.assertLess(online_last, last_close,
                        "Online SuperTrend should be below close in uptrend")
        # Direction flag should both be True (uptrend).
        self.assertTrue(calc.is_uptrend)
        self.assertTrue(self.indicator.is_uptrend)


if __name__ == "__main__":
    unittest.main()
