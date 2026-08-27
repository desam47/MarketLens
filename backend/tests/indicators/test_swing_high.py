"""
Tests for Swing High indicator
"""
import unittest

from backend.indicators.swing_high import SwingHighIndicator


class TestSwingHighIndicator(unittest.TestCase):

    def setUp(self):
        self.indicator = SwingHighIndicator(lookback_period=2)

    def test_swing_high_calculation(self):
        """Test Swing High calculation with known values"""
        # Test data: high prices with a clear swing high at index 4 (value 15)
        # Pattern: 10, 12, 14, 13, 15, 11, 9 (swing high at 15)
        data = [
            {'high': 10},
            {'high': 12},
            {'high': 14},
            {'high': 13},
            {'high': 15},  # Swing high: higher than 2 bars on each side
            {'high': 11},
            {'high': 9}
        ]

        # Calculate Swing High
        swing_values = self.indicator.calculate(data)

        # Should detect one swing high at value 15
        self.assertEqual(len(swing_values), 1)
        self.assertAlmostEqual(swing_values[0], 15.0, places=2)

    def test_swing_high_no_swing(self):
        """Test Swing High with no swing points"""
        # Test data: steadily increasing prices - no swing highs
        data = [
            {'high': 10},
            {'high': 11},
            {'high': 12},
            {'high': 13},
            {'high': 14},
            {'high': 15}
        ]

        # Calculate Swing High
        swing_values = self.indicator.calculate(data)

        # Should detect no swing highs
        self.assertEqual(len(swing_values), 0)

    def test_swing_high_update(self):
        """Test Swing High updating with new data points"""
        # Initialize with enough data for initial calculation
        initial_data = [
            {'high': 10},
            {'high': 12},
            {'high': 14},
            {'high': 13},
            {'high': 15},  # This should be a swing high
            {'high': 11}
        ]

        # Calculate initial Swing High
        self.indicator.calculate(initial_data)
        # Note: With lookback_period=2, we need 5 bars to confirm the swing high
        # The swing high at index 4 (value 15) needs bars 2,3,5,6 to confirm
        # So we won't see it yet with just 6 bars

        # Update with new data that confirms the swing high
        new_data = {'high': 9}  # This confirms index 4 as swing high
        updated_swing = self.indicator.update(new_data)

        # Should return the swing high value (15.0) now that it's confirmed
        self.assertAlmostEqual(updated_swing, 15.0, places=2)

    def test_swing_high_insufficient_data(self):
        """Test Swing High with insufficient data"""
        # Not enough data for lookback_period=2 (need 2*2+1=5 bars)
        data = [
            {'high': 10},
            {'high': 12},
            {'high': 14}
        ]

        # Calculate Swing High
        swing_values = self.indicator.calculate(data)

        # Should return empty list
        self.assertEqual(len(swing_values), 0)


if __name__ == '__main__':
    unittest.main()