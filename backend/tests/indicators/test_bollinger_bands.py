"""
Tests for Bollinger Bands indicator
"""
import unittest

from backend.indicators.bollinger_bands import BollingerBandsIndicator


class TestBollingerBandsIndicator(unittest.TestCase):
    """Tests for BollingerBandsIndicator"""

    def setUp(self):
        self.indicator = BollingerBandsIndicator(period=20, std_dev=2.0)

    def _make_data(self, n: int = 30, base: float = 100.0) -> list[dict[str, float]]:
        """Generate stepped data: 5 bars at 102, 5 at 98, 5 at 102, ... mixes variation."""
        bars = []
        for i in range(n):
            # Alternate ±2 every 5 bars
            block = i // 5
            offset = 2.0 if block % 2 == 0 else -2.0
            close = base + offset
            bars.append({
                "close": close,
                "high": close + 0.5,
                "low": close - 0.5,
            })
        return bars

    def _latest(self, key: str) -> float:
        """Get the latest value of one of the band series."""
        bands = self.indicator.get_bands()
        series = bands[key]
        self.assertGreater(len(series), 0, f"{key} series is empty")
        return series[-1]

    def test_bollinger_bands_ordering(self):
        """upper > middle > lower for every computed bar."""
        data = self._make_data(30)
        self.indicator.calculate(data)
        upper = self._latest("upper")
        middle = self._latest("middle")
        lower = self._latest("lower")
        self.assertGreater(upper, middle)
        self.assertGreater(middle, lower)

    def test_bollinger_bands_bandwidth(self):
        """bandwidth = (upper - lower) / middle."""
        data = self._make_data(30)
        self.indicator.calculate(data)
        upper = self._latest("upper")
        middle = self._latest("middle")
        lower = self._latest("lower")
        bandwidth = self._latest("bandwidth")
        expected = (upper - lower) / middle
        self.assertAlmostEqual(bandwidth, expected, places=6)

    def test_bollinger_bands_percent_b(self):
        """%B is between 0 and 1 for closes inside the bands."""
        data = self._make_data(30)
        self.indicator.calculate(data)
        pct_b = self._latest("percent_b")
        # With stepped data close is the period mean → %B ≈ 0.5
        self.assertGreaterEqual(pct_b, 0.0)
        self.assertLessEqual(pct_b, 1.0)

    def test_bollinger_bands_update(self):
        """update() with high/low/close maintains state and produces a value."""
        data = self._make_data(25)
        for bar in data:
            self.indicator.update(bar)
        # After 25 updates, get_bands should have 5+ entries (period=20, so
        # warm-up consumes 19 bars, then one band per subsequent bar).
        bands = self.indicator.get_bands()
        self.assertGreater(len(bands["upper"]), 0)
        self.assertGreater(bands["upper"][-1], 0.0)

    def test_update_matches_calculate(self):
        """O(1) update() must produce the same band values as offline calculate()."""
        data = self._make_data(30, base=100.0)
        # Offline path
        calc = BollingerBandsIndicator(period=20, std_dev=2.0)
        calc.calculate(data)
        # Online path: feed the same bars one at a time
        for bar in data:
            self.indicator.update(bar)
        # After 30 bars, both should have the same number of band values
        calc_bands = calc.get_bands()
        online_bands = self.indicator.get_bands()
        for key in ("upper", "middle", "lower", "bandwidth", "percent_b"):
            calc_vals = calc_bands[key]
            online_vals = online_bands[key]
            self.assertGreater(len(calc_vals), 0, f"{key} should have values")
            # The last N values should match (both have the same warmup)
            self.assertEqual(len(calc_vals), len(online_vals))
            for i, (cv, ov) in enumerate(zip(calc_vals, online_vals, strict=True)):
                self.assertAlmostEqual(
                    cv, ov, places=9,
                    msg=f"{key}[{i}]: update()={ov:.9f} vs calculate()={cv:.9f}",
                )


if __name__ == "__main__":
    unittest.main()
