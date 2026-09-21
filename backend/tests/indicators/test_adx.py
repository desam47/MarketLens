"""
Tests for ADX (Average Directional Index) indicator
"""

import unittest

from backend.indicators.adx import ADXIndicator


class TestADXIndicator(unittest.TestCase):
    """Tests for ADXIndicator"""

    def setUp(self):
        self.indicator = ADXIndicator(period=14)

    def _make_trending_data(self, n: int = 60) -> list[dict[str, float]]:
        """Generate a strong uptrend with rising highs and lows."""
        bars = []
        for i in range(n):
            base = 100.0 + i * 0.5
            bars.append(
                {
                    "high": base + 1.0,
                    "low": base - 1.0,
                    "close": base + 0.5,
                }
            )
        return bars

    def test_adx_calculation(self):
        """ADX on a trending series should produce non-zero values after warm-up"""
        data = self._make_trending_data(60)
        values = self.indicator.calculate(data)
        # ADX needs 2*period + 1 bars to produce its first value
        self.assertGreater(len(values), 0)
        # Strong trend → high ADX
        self.assertGreater(values[-1], 0.0)
        self.assertLessEqual(values[-1], 100.0)

    def test_adx_update(self):
        """update() appends new ADX after each qualifying bar"""
        data = self._make_trending_data(40)
        for bar in data:
            self.indicator.update(bar)
        # After feeding 40 bars, latest value should be set
        latest = self.indicator.get_latest()
        self.assertIsNotNone(latest)
        self.assertGreater(latest, 0.0)
        self.assertLessEqual(latest, 100.0)

    def test_adx_reset(self):
        """reset() clears values list"""
        data = self._make_trending_data(40)
        self.indicator.calculate(data)
        self.assertGreater(len(self.indicator.values), 0)
        self.indicator.reset()
        self.assertEqual(self.indicator.values, [])
        self.assertIsNone(self.indicator.get_latest())


if __name__ == "__main__":
    unittest.main()
