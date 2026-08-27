"""
Tests for Indicator Engine
"""
import unittest

from backend.indicators import (
    EMAIndicator,
    IndicatorEngine,
    RSIIndicator,
    SMAIndicator,
)


class TestIndicatorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = IndicatorEngine()

    def test_add_remove_indicator(self):
        """Test adding and removing indicators"""
        ema = EMAIndicator(period=5)
        sma = SMAIndicator(period=10)

        # Add indicators
        self.engine.add_indicator(ema)
        self.engine.add_indicator(sma)

        # Check they were added
        self.assertIn("EMA", self.engine.list_indicators())
        self.assertIn("SMA", self.engine.list_indicators())
        self.assertEqual(len(self.engine.list_indicators()), 2)

        # Remove one indicator
        self.engine.remove_indicator("EMA")

        # Check it was removed
        self.assertNotIn("EMA", self.engine.list_indicators())
        self.assertIn("SMA", self.engine.list_indicators())
        self.assertEqual(len(self.engine.list_indicators()), 1)

    def test_update_data(self):
        """Test updating engine with new data"""
        ema = EMAIndicator(period=3)
        self.engine.add_indicator(ema)

        # Test data
        data = [
            {'close': 10, 'high': 11, 'low': 9, 'volume': 100},
            {'close': 12, 'high': 13, 'low': 11, 'volume': 120},
            {'close': 14, 'high': 15, 'low': 13, 'volume': 140},
            {'close': 13, 'high': 14, 'low': 12, 'volume': 130},
            {'close': 15, 'high': 16, 'low': 14, 'volume': 150}
        ]

        # Update engine with each data point
        for datum in data:
            self.engine.update_data(datum)

        # Check that data history was stored
        self.assertEqual(len(self.engine.data_history), 5)

        # Check that indicator was updated
        latest_values = self.engine.get_latest_values()
        self.assertIn("EMA", latest_values)
        self.assertIsNotNone(latest_values["EMA"])

    def test_calculate_all(self):
        """Test calculating all indicators for given data"""
        ema = EMAIndicator(period=3)
        sma = SMAIndicator(period=3)
        rsi = RSIIndicator(period=3)

        self.engine.add_indicator(ema)
        self.engine.add_indicator(sma)
        self.engine.add_indicator(rsi)

        # Test data
        data = [
            {'close': 10, 'high': 11, 'low': 9, 'volume': 100},
            {'close': 12, 'high': 13, 'low': 11, 'volume': 120},
            {'close': 14, 'high': 15, 'low': 13, 'volume': 140},
            {'close': 13, 'high': 14, 'low': 12, 'volume': 130},
            {'close': 15, 'high': 16, 'low': 14, 'volume': 150},
            {'close': 11, 'high': 12, 'low': 10, 'volume': 110},
            {'close': 13, 'high': 14, 'low': 12, 'volume': 130}
        ]

        # Calculate all indicators
        results = self.engine.calculate_all(data)

        # Check results
        self.assertIn("EMA", results)
        self.assertIn("SMA", results)
        self.assertIn("RSI", results)

        # Should have calculated values
        self.assertGreater(len(results["EMA"]), 0)
        self.assertGreater(len(results["SMA"]), 0)
        self.assertGreater(len(results["RSI"]), 0)

        # Values should be numbers
        for value in results["EMA"]:
            self.assertIsInstance(value, (int, float))

    def test_get_indicator(self):
        """Test getting a specific indicator"""
        ema = EMAIndicator(period=5)
        self.engine.add_indicator(ema)

        # Get the indicator
        retrieved_ema = self.engine.get_indicator("EMA")

        # Check it's the same object
        self.assertIs(retrieved_ema, ema)

        # Test getting non-existent indicator
        none_indicator = self.engine.get_indicator("NONEXISTENT")
        self.assertIsNone(none_indicator)


if __name__ == '__main__':
    unittest.main()