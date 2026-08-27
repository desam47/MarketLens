"""
Tests for MACD indicator
"""
import unittest

from backend.indicators.macd import MACDIndicator


class TestMACDIndicator(unittest.TestCase):

    def setUp(self):
        self.indicator = MACDIndicator(fast=12, slow=26, signal=9)

    def test_macd_calculation(self):
        """Test MACD calculation with known values"""
        # Create test data with enough points for MACD calculation
        data = []
        for i in range(35):  # Need enough data for slow EMA (26) + signal (9)
            data.append({
                'close': 100 + i * 0.5  # Gradually increasing price
            })

        # Calculate MACD
        macd_values = self.indicator.calculate(data)

        # Should have calculated values
        self.assertGreater(len(macd_values), 0)

        # All values should be floats
        for value in macd_values:
            self.assertIsInstance(value, float)

    def test_macd_update(self):
        """Test MACD updating with new data points"""
        # Build up enough data via update() to get a MACD histogram value.
        # Need at least slow(26) + signal(9) = 35 updates before a histogram appears.
        for i in range(36):
            result = self.indicator.update({'close': 100 + i * 0.5})

        # After enough data, update() should return a float histogram value
        self.assertIsNotNone(result)
        self.assertIsInstance(result, float)

        # Update with another point to confirm continued functionality
        next_result = self.indicator.update({'close': 120.0})
        self.assertIsNotNone(next_result)
        self.assertIsInstance(next_result, float)

    def test_macd_insufficient_data(self):
        """Test MACD with insufficient data"""
        # Not enough data for MACD calculation
        data = [
            {'close': 100},
            {'close': 101},
            {'close': 102}
        ]

        # Calculate MACD
        macd_values = self.indicator.calculate(data)

        # Should return empty list
        self.assertEqual(len(macd_values), 0)


if __name__ == '__main__':
    unittest.main()