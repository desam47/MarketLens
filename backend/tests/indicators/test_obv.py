"""
Tests for On-Balance Volume (OBV) indicator
"""
import unittest

from backend.indicators.obv import OBVIndicator


class TestOBVIndicator(unittest.TestCase):
    """Tests for OBVIndicator"""

    def setUp(self):
        self.indicator = OBVIndicator()

    def test_obv_calculation(self):
        """Hand-computed OBV for a 5-bar up/down sequence."""
        # Bars: 100, 102, 101, 105, 103  Volumes: 10, 20, 30, 40, 50
        # Changes:    up,  dn,  up,  dn
        # OBV:     0,  +20, -30, +40, -50
        data = [
            {"close": 100.0, "volume": 10.0},
            {"close": 102.0, "volume": 20.0},
            {"close": 101.0, "volume": 30.0},
            {"close": 105.0, "volume": 40.0},
            {"close": 103.0, "volume": 50.0},
        ]
        values = self.indicator.calculate(data)
        self.assertEqual(len(values), 5)
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[1], 20.0)
        self.assertEqual(values[2], -10.0)
        self.assertEqual(values[3], 30.0)
        self.assertEqual(values[4], -20.0)

    def test_obv_update(self):
        """update() returns latest OBV after each bar."""
        result = self.indicator.update({"close": 100.0, "volume": 10.0})
        self.assertEqual(result, 0.0)
        result = self.indicator.update({"close": 102.0, "volume": 20.0})
        self.assertEqual(result, 20.0)
        result = self.indicator.update({"close": 101.0, "volume": 30.0})
        self.assertEqual(result, -10.0)

    def test_obv_reset(self):
        """reset() clears values list."""
        data = [
            {"close": 100.0, "volume": 10.0},
            {"close": 102.0, "volume": 20.0},
        ]
        self.indicator.calculate(data)
        self.assertGreater(len(self.indicator.values), 0)
        self.indicator.reset()
        self.assertEqual(self.indicator.values, [])


if __name__ == "__main__":
    unittest.main()
