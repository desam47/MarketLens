"""
Tests for ATR (Average True Range) indicator
"""

import unittest

from backend.indicators.atr import ATRIndicator


class TestATRIndicator(unittest.TestCase):
    """Tests for ATRIndicator"""

    def setUp(self):
        self.indicator = ATRIndicator(period=14)

    def _make_ohlc(
        self, n: int = 30, base: float = 100.0, step: float = 0.5
    ) -> list[dict[str, float]]:
        """Generate a series of OHLC bars with constant daily range = 1.0."""
        bars = []
        for i in range(n):
            close = base + i * step
            bars.append(
                {
                    "open": close - 0.25,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                }
            )
        return bars

    def test_atr_calculation(self):
        """ATR on a constant-range series converges to that range (1.0)."""
        data = self._make_ohlc(30)
        values = self.indicator.calculate(data)
        # ATR needs period+1 bars to produce its first value
        self.assertGreater(len(values), 0)
        # Daily range is high-low = 1.0; true range starts at 1.0 too.
        # After warm-up, ATR should be very close to 1.0.
        self.assertAlmostEqual(values[-1], 1.0, places=2)

    def test_atr_update(self):
        """update() appends ATR values once enough bars have been seen."""
        data = self._make_ohlc(20)
        for bar in data:
            self.indicator.update(bar)
        latest = self.indicator.get_latest()
        self.assertIsNotNone(latest)
        self.assertAlmostEqual(latest, 1.0, places=2)

    def test_atr_insufficient_data(self):
        """ATR with fewer than period+1 bars returns empty list."""
        data = self._make_ohlc(5)
        values = self.indicator.calculate(data)
        self.assertEqual(values, [])


if __name__ == "__main__":
    unittest.main()
