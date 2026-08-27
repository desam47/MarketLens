"""
Tests for EMA indicator
"""
import unittest

from backend.indicators.ema import EMAIndicator


class TestEMAIndicator(unittest.TestCase):

    def setUp(self):
        self.indicator = EMAIndicator(period=5)

    def test_ema_calculation(self):
        """Test EMA calculation with known values"""
        # Test data: closing prices
        data = [
            {'close': 10},
            {'close': 12},
            {'close': 13},
            {'close': 11},
            {'close': 14},
            {'close': 15},
            {'close': 13},
            {'close': 16}
        ]

        # Calculate EMA
        ema_values = self.indicator.calculate(data)

        # We should have EMA values starting from the 5th data point (index 4)
        # First EMA value should be SMA of first 5 prices: (10+12+13+11+14)/5 = 12
        self.assertGreater(len(ema_values), 0)
        self.assertAlmostEqual(ema_values[0], 12.0, places=2)

        # Test that values are reasonable
        for value in ema_values:
            self.assertIsInstance(value, float)
            self.assertGreater(value, 0)

    def test_ema_update(self):
        """Test EMA updating with new data points"""
        # Initialize with some data
        initial_data = [
            {'close': 10},
            {'close': 12},
            {'close': 13},
            {'close': 11},
            {'close': 14}
        ]

        # Calculate initial EMA
        initial_ema = self.indicator.calculate(initial_data)
        self.assertGreater(len(initial_ema), 0)

        # Update with new data
        new_data = {'close': 15}
        updated_ema = self.indicator.update(new_data)

        # Should return a value
        self.assertIsNotNone(updated_ema)
        self.assertIsInstance(updated_ema, float)

        # Update again
        newer_data = {'close': 13}
        newer_ema = self.indicator.update(newer_data)
        self.assertIsNotNone(newer_ema)
        self.assertIsInstance(newer_ema, float)

    def test_ema_reset(self):
        """Test EMA reset functionality"""
        # Add some data (need at least period=5 data points)
        data = [
            {'close': 10},
            {'close': 12},
            {'close': 13},
            {'close': 11},
            {'close': 14}
        ]
        self.indicator.calculate(data)

        # Should have values
        self.assertGreater(len(self.indicator.values), 0)

        # Reset
        self.indicator.reset()

        # Should be empty after reset
        self.assertEqual(len(self.indicator.values), 0)
        self.assertIsNone(self.indicator.get_latest())

if __name__ == '__main__':
    unittest.main()
