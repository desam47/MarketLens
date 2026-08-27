"""
Tests for trend engine
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))

from backend.engines.timeframe import Timeframe
from backend.trend.trend_engine import (
    TrendDirection,
    TrendEngine,
    TrendSignal,
    TrendStrength,
)


class TestTrendEngine(unittest.TestCase):
    
    def setUp(self):
        self.symbol = "AAPL"
        self.engine = TrendEngine(self.symbol)
    
    def test_engine_initialization(self):
        """Test that trend engine initializes correctly"""
        self.assertEqual(self.engine.symbol, self.symbol)
        self.assertIsNotNone(self.engine.timeframe_engine)
        self.assertIsInstance(self.engine.indicators, dict)
        self.assertIsInstance(self.engine.trend_history, dict)
        
        # Check that indicators are initialized for major timeframes
        expected_timeframes = [
            "ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE",
            "ONE_HOUR", "FOUR_HOUR", "ONE_DAY"
        ]
        for tf_str in expected_timeframes:
            # Find the Timeframe enum member
            from backend.engines.timeframe import Timeframe
            tf = getattr(Timeframe, tf_str)
            self.assertIn(tf, self.engine.indicators)
            self.assertGreater(len(self.engine.indicators[tf]), 0)
    
    def test_engine_update_with_data(self):
        """Test updating the engine with market data"""
        base_time = datetime.now()
        
        # Simulate an uptrend
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
        
        for i, price in enumerate(prices):
            timestamp = base_time + timedelta(minutes=i)
            self.engine.update(price, 1000, timestamp)
        
        # Should have processed the data
        # Check that we have some trend signals
        from backend.engines.timeframe import Timeframe
        self.engine.get_current_trend(Timeframe.ONE_DAY)
        # Might not have enough data yet for a full signal, but engine should not crash

        # Test that we can get trend history
        history = self.engine.get_trend_history(Timeframe.ONE_HOUR)
        self.assertIsInstance(history, list)
    
    def test_trend_signal_creation(self):
        """Test creating a trend signal"""
        timestamp = datetime.now()
        signal = TrendSignal(
            symbol=self.symbol,
            timeframe=Timeframe.ONE_HOUR,
            direction=TrendDirection.UPTREND,
            strength=TrendStrength.MODERATE,
            confidence=0.8,
            timestamp=timestamp
        )
        
        self.assertEqual(signal.symbol, self.symbol)
        self.assertEqual(signal.direction, TrendDirection.UPTREND)
        self.assertEqual(signal.strength, TrendStrength.MODERATE)
        self.assertEqual(signal.confidence, 0.8)
        self.assertEqual(signal.timestamp, timestamp)
    
    def test_get_current_trend_no_data(self):
        """Test getting current trend with no data"""
        # Should return None when no data has been processed
        tf = Timeframe.ONE_HOUR
        trend = self.engine.get_current_trend(tf)
        self.assertIsNone(trend)
    
    def test_get_trend_history_empty(self):
        """Test getting trend history with no data"""
        tf = Timeframe.ONE_HOUR
        history = self.engine.get_trend_history(tf)
        self.assertEqual(history, [])
    
    def test_get_multi_timeframe_trend(self):
        """Getting multi-timeframe trend"""
        trends = self.engine.get_multi_timeframe_trend()
        self.assertIsInstance(trends, dict)
        # Should be empty initially
        self.assertEqual(len(trends), 0)
    
    def test_get_overall_trend_no_data(self):
        """Test getting overall trend with no data"""
        overall = self.engine.get_overall_trend()
        self.assertIsNone(overall)

if __name__ == '__main__':
    unittest.main()
