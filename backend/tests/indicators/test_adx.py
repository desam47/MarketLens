"""
Tests for ADX (Average Directional Index) indicator
"""

import math
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

    def _make_choppy_trending_data(self, n: int = 40) -> list[dict[str, float]]:
        """An uptrend with oscillating range/highs/lows, so +DM/-DM/TR
        vary bar to bar (unlike a straight-line trend, where DX pins at
        100 regardless of any warmup weighting bug and can't distinguish
        correct vs. buggy smoothing)."""
        bars = []
        price = 100.0
        for i in range(n):
            osc = math.sin(i * 0.7) * 1.5
            high = price + 1.0 + osc + (0.4 if i % 3 == 0 else 0)
            low = price - 1.2 + osc - (0.3 if i % 5 == 0 else 0)
            close = price + osc * 0.5
            bars.append({"high": high, "low": low, "close": close})
            price += 0.3
        return bars

    def test_adx_update_matches_calculate_after_warmup(self):
        """Streaming update() and batch calculate() must produce the same
        first ADX value for identical data.

        Regression test: update()'s warmup used to seed the smoothed
        DM+/DM-/TR averages from the last `period` bars, then fall
        through into the Phase-2 Wilder-update formula using that same
        bar's DM/TR values a second time -- double-weighting it and
        skewing the first (and, more mildly, several subsequent) ADX
        values relative to the correct offline calculate() path.
        """
        data = self._make_choppy_trending_data(40)

        batch_values = self.indicator.calculate(data)
        self.assertGreater(len(batch_values), 0)

        streaming = ADXIndicator(period=14)
        first_streamed = None
        last_streamed = None
        for bar in data:
            result = streaming.update(bar)
            if result is not None:
                last_streamed = result
                if first_streamed is None:
                    first_streamed = result

        self.assertIsNotNone(first_streamed)
        self.assertIsNotNone(last_streamed)
        # The very first ADX value carries the full weight of the bug (the
        # warmup-seed bar's DM/TR applied twice); calculate() never double
        # counts, so this must match closely. Buggy code was off by ~6.7
        # points on this fixture; a correct implementation matches exactly.
        self.assertAlmostEqual(first_streamed, batch_values[0], delta=0.5)
        self.assertAlmostEqual(last_streamed, batch_values[-1], delta=0.5)


if __name__ == "__main__":
    unittest.main()
