"""
Tests for Swing Low indicator
"""

import unittest

from backend.indicators.swing_low import SwingLowIndicator


class TestSwingLowIndicator(unittest.TestCase):
    def setUp(self):
        self.indicator = SwingLowIndicator(lookback_period=2)

    def test_swing_low_calculation(self):
        """Test Swing Low calculation with known values"""
        # Test data: low prices with a clear swing low at index 4 (value 8)
        # Pattern: 15, 13, 11, 12, 8, 10, 14 (swing low at 8)
        data = [
            {"low": 15},
            {"low": 13},
            {"low": 11},
            {"low": 12},
            {"low": 8},  # Swing low: lower than 2 bars on each side
            {"low": 10},
            {"low": 14},
        ]

        # Calculate Swing Low
        swing_values = self.indicator.calculate(data)

        # Should detect one swing low at value 8
        self.assertEqual(len(swing_values), 1)
        self.assertAlmostEqual(swing_values[0], 8.0, places=2)

    def test_swing_low_no_swing(self):
        """Test Swing Low with no swing points"""
        # Test data: steadily decreasing prices - no swing lows
        data = [{"low": 15}, {"low": 14}, {"low": 13}, {"low": 12}, {"low": 11}, {"low": 10}]

        # Calculate Swing Low
        swing_values = self.indicator.calculate(data)

        # Should detect no swing lows
        self.assertEqual(len(swing_values), 0)

    def test_swing_low_update(self):
        """Test Swing Low updating with new data points"""
        # Initialize with enough data for initial calculation
        initial_data = [
            {"low": 15},
            {"low": 13},
            {"low": 11},
            {"low": 12},
            {"low": 8},  # This should be a swing low
            {"low": 10},
        ]

        # Calculate initial Swing Low
        self.indicator.calculate(initial_data)
        # Note: With lookback_period=2, we need 5 bars to confirm the swing low
        # The swing low at index 4 (value 8) needs bars 2,3,5,6 to confirm
        # So we won't see it yet with just 6 bars

        # Update with new data that confirms the swing low
        new_data = {"low": 14}  # This confirms index 4 as swing low
        updated_swing = self.indicator.update(new_data)

        # Should return the swing low value (8.0) now that it's confirmed
        self.assertAlmostEqual(updated_swing, 8.0, places=2)

    def test_swing_low_insufficient_data(self):
        """Test Swing Low with insufficient data"""
        # Not enough data for lookback_period=2 (need 2*2+1=5 bars)
        data = [{"low": 15}, {"low": 13}, {"low": 11}]

        # Calculate Swing Low
        swing_values = self.indicator.calculate(data)

        # Should return empty list
        self.assertEqual(len(swing_values), 0)


if __name__ == "__main__":
    unittest.main()
