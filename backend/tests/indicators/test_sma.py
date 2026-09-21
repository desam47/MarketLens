"""
Tests for SMA indicator
"""

import unittest

from backend.indicators.sma import SMAIndicator


class TestSMAIndicator(unittest.TestCase):
    def setUp(self):
        self.indicator = SMAIndicator(period=3)

    def test_sma_calculation(self):
        """Test SMA calculation with known values"""
        # Test data: closing prices
        data = [{"close": 10}, {"close": 12}, {"close": 14}, {"close": 13}, {"close": 15}]

        # Calculate SMA
        sma_values = self.indicator.calculate(data)

        # First SMA value should be SMA of first 3 prices: (10+12+14)/3 = 12
        # Second SMA value: (12+14+13)/3 = 13
        # Third SMA value: (14+13+15)/3 = 14
        self.assertEqual(len(sma_values), 3)
        self.assertAlmostEqual(sma_values[0], 12.0, places=2)
        self.assertAlmostEqual(sma_values[1], 13.0, places=2)
        self.assertAlmostEqual(sma_values[2], 14.0, places=2)

    def test_sma_update(self):
        """Test SMA updating with new data points"""
        # Initialize with some data
        initial_data = [{"close": 10}, {"close": 12}, {"close": 14}]

        # Calculate initial SMA
        initial_sma = self.indicator.calculate(initial_data)
        self.assertEqual(len(initial_sma), 1)
        self.assertAlmostEqual(initial_sma[0], 12.0, places=2)

        # Update with new data
        new_data = {"close": 13}
        updated_sma = self.indicator.update(new_data)

        # Should return a value: (12+14+13)/3 = 13
        self.assertAlmostEqual(updated_sma, 13.0, places=2)

        # Update again
        newer_data = {"close": 15}
        newer_sma = self.indicator.update(newer_data)

        # Should return a value: (14+13+15)/3 = 14
        self.assertAlmostEqual(newer_sma, 14.0, places=2)

    def test_sma_insufficient_data(self):
        """Test SMA with insufficient data"""
        indicator = SMAIndicator(period=5)

        # Not enough data
        data = [{"close": 10}, {"close": 12}]

        sma_values = indicator.calculate(data)
        self.assertEqual(len(sma_values), 0)

        # Update with insufficient data
        update_result = indicator.update({"close": 14})
        self.assertIsNone(update_result)


if __name__ == "__main__":
    unittest.main()
