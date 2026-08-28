"""
Tests for Volume SMA indicator
"""
import unittest

from backend.indicators.volume_sma import VolumeSMAIndicator


class TestVolumeSMAIndicator(unittest.TestCase):
    """Tests for VolumeSMAIndicator"""

    def setUp(self):
        self.indicator = VolumeSMAIndicator(period=20)

    def test_volume_sma_calculation(self):
        """Volume SMA on a 20-bar series with constant volume = 100 is 100."""
        data = [{"volume": 100.0} for _ in range(20)]
        values = self.indicator.calculate(data)
        # First output starts at index period-1, so 1 value
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0], 100.0)

    def test_volume_sma_update(self):
        """update() returns latest SMA after enough bars have been seen."""
        for v in [10, 20, 30, 40, 50]:
            self.indicator.update({"volume": float(v)})
        # Only 5 updates, period=20 → SMA not yet defined
        self.assertEqual(len(self.indicator.values), 0)
        # Push 15 more to reach 20
        for _ in range(15):
            self.indicator.update({"volume": 100.0})
        latest = self.indicator.get_latest()
        self.assertIsNotNone(latest)
        self.assertGreater(latest, 0.0)

    def test_volume_sma_insufficient_data(self):
        """Fewer than `period` bars returns empty list."""
        data = [{"volume": 100.0} for _ in range(5)]
        values = self.indicator.calculate(data)
        self.assertEqual(values, [])


if __name__ == "__main__":
    unittest.main()
