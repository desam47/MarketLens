"""
Tests for Relative Volume indicator
"""

import unittest

from backend.indicators.relative_volume import RelativeVolumeIndicator


class TestRelativeVolumeIndicator(unittest.TestCase):
    """Tests for RelativeVolumeIndicator"""

    def setUp(self):
        self.indicator = RelativeVolumeIndicator(period=20)

    def test_relative_volume_calculation(self):
        """Relative Volume on constant volume = 1.0 for every bar."""
        # 21 bars (period + 1) with volume=100. Average = 100. RV = 100/100 = 1.0
        data = [{"volume": 100.0} for _ in range(21)]
        values = self.indicator.calculate(data)
        self.assertGreater(len(values), 0)
        for v in values:
            self.assertAlmostEqual(v, 1.0, places=2)

    def test_relative_volume_update(self):
        """update() produces a value after enough bars have been seen."""
        for _ in range(25):
            self.indicator.update({"volume": 100.0})
        latest = self.indicator.get_latest()
        self.assertIsNotNone(latest)
        self.assertAlmostEqual(latest, 1.0, places=2)

    def test_relative_volume_reset(self):
        """reset() clears values list."""
        data = [{"volume": 100.0} for _ in range(25)]
        self.indicator.calculate(data)
        self.assertGreater(len(self.indicator.values), 0)
        self.indicator.reset()
        self.assertEqual(self.indicator.values, [])


if __name__ == "__main__":
    unittest.main()
