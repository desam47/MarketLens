"""
Tests for multi-timeframe engine
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.engines.timeframe import Timeframe
from backend.multitimeframe.multi_timeframe_engine import (
    ConfluenceDirection,
    MultiTimeframeEngine,
)


class TestMultiTimeframeEngine(unittest.TestCase):

    def setUp(self):
        self.symbol = "AAPL"
        self.engine = MultiTimeframeEngine(self.symbol)

    def test_engine_initialization(self):
        """Test that multi-timeframe engine initializes correctly"""
        self.assertEqual(self.engine.symbol, self.symbol)
        self.assertIsInstance(self.engine.trend_engines, dict)
        self.assertIsInstance(self.engine.confluence_history, list)

        # Check that trend engines are initialized for key timeframes
        expected_timeframes = [
            Timeframe.FIVE_MINUTE,
            Timeframe.FIFTEEN_MINUTE,
            Timeframe.ONE_HOUR,
            Timeframe.FOUR_HOUR,
            Timeframe.ONE_DAY
        ]
        for tf in expected_timeframes:
            self.assertIn(tf, self.engine.trend_engines)

    def test_engine_update_with_data(self):
        """Test updating the engine with market data"""
        base_time = datetime.now()

        # Simulate an uptrend
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]

        for i, price in enumerate(prices):
            timestamp = base_time + timedelta(minutes=i*5)  # 5-minute intervals
            self.engine.update(price, 1000, timestamp)

        # Should have processed the data
        # Check that we have some confluence signals
        confluence = self.engine.get_current_confluence()
        # Might not have enough data yet for a full signal, but engine should not crash
        self.assertIsNotNone(confluence)  # Should have some signal even if weak

        # Test that we can get confluence history
        history = self.engine.get_confluence_history()
        self.assertIsInstance(history, list)

    def test_get_current_confluence_no_data(self):
        """Test getting current confluence with no data"""
        # Should return None when no data has been processed
        confluence = self.engine.get_current_confluence()
        # With no data, it should return None
        self.assertIsNone(confluence)

    def test_get_confluence_history_empty(self):
        """Test getting confluence history with no data"""
        history = self.engine.get_confluence_history()
        self.assertIsInstance(history, list)

    def test_get_timeframe_trend(self):
        """Test getting trend for a specific timeframe"""
        base_time = datetime.now()

        # Update with some data
        for i in range(5):
            price = 100 + i
            timestamp = base_time + timedelta(minutes=i*5)
            self.engine.update(price, 1000, timestamp)

        # Should be able to get trend for a timeframe
        trend = self.engine.get_timeframe_trend(Timeframe.FIVE_MINUTE)
        # Might be None if not enough data, but should not crash
        self.assertTrue(trend is None or hasattr(trend, 'direction'))

    def test_get_all_timeframe_trends(self):
        """Test getting trends for all timeframes"""
        base_time = datetime.now()

        # Update with some data
        for i in range(5):
            price = 100 + i
            timestamp = base_time + timedelta(minutes=i*5)
            self.engine.update(price, 1000, timestamp)

        # Should be able to get all trends
        trends = self.engine.get_all_timeframe_trends()
        self.assertIsInstance(trends, dict)

        # Each trend should be a TrendSignal or None
        for tf, trend in trends.items():
            if trend is not None:
                self.assertTrue(hasattr(trend, 'direction'))
                self.assertTrue(hasattr(trend, 'strength'))
                self.assertTrue(hasattr(trend, 'confidence'))

    def test_confluence_calculation(self):
        """Test that confluence calculations work correctly"""
        # This test verifies the internal logic works
        base_time = datetime.now()

        # Create a clear uptrend scenario
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112]

        for i, price in enumerate(prices):
            timestamp = base_time + timedelta(minutes=i*5)
            self.engine.update(price, 1000, timestamp)

        # Get confluence signal
        confluence = self.engine.get_current_confluence()
        self.assertIsNotNone(confluence)

        # Should have valid values
        self.assertIsInstance(confluence.direction, ConfluenceDirection)
        self.assertGreaterEqual(confluence.strength, 0.0)
        self.assertLessEqual(confluence.strength, 1.0)
        self.assertGreaterEqual(confluence.alignment_score, 0.0)
        self.assertLessEqual(confluence.alignment_score, 1.0)
        self.assertIsInstance(confluence.timestamp, datetime)
        self.assertIsInstance(confluence.timeframe_signals, dict)


if __name__ == '__main__':
    unittest.main()