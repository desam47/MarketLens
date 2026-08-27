"""
Tests for market regime engine
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.regime.market_regime_engine import MarketRegime, MarketRegimeEngine


class TestMarketRegimeEngine(unittest.TestCase):

    def setUp(self):
        self.symbol = "AAPL"
        self.engine = MarketRegimeEngine(self.symbol)

    def test_engine_initialization(self):
        """Test that market regime engine initializes correctly"""
        self.assertEqual(self.engine.symbol, self.symbol)
        self.assertIsNotNone(self.engine.trend_engine)
        self.assertIsNotNone(self.engine.multitimeframe_engine)
        self.assertIsNotNone(self.engine.atr_indicator)
        self.assertIsNotNone(self.engine.adx_indicator)
        self.assertIsNotNone(self.engine.bb_indicator)
        self.assertIsNotNone(self.engine.ema_fast)
        self.assertIsNotNone(self.engine.ema_slow)
        self.assertIsInstance(self.engine.regime_history, list)

    def test_engine_update_with_data(self):
        """Test updating the engine with market data"""
        base_time = datetime.now()

        # Simulate an uptrend with moderate volatility
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
        volumes = [1000] * len(prices)

        for i, (price, volume) in enumerate(zip(prices, volumes)):
            timestamp = base_time + timedelta(minutes=i*5)
            # Provide approximate OHLC
            high = price + 0.5
            low = price - 0.5
            open_price = price - 0.2 if i > 0 else price
            self.engine.update(price, volume, timestamp, "", high, low, open_price)

        # Should have processed the data
        # Check that we have some regime signals
        regime_signal = self.engine.get_current_regime()
        # Might not have enough data yet for a strong signal, but engine should not crash
        self.assertIsNotNone(regime_signal)  # Should have some signal even if unknown

        # Test that we can get regime history
        history = self.engine.get_regime_history()
        self.assertIsInstance(history, list)

    def test_get_current_regime_no_data(self):
        """Test getting current regime with no data"""
        # Should return None when no data has been processed
        regime_signal = self.engine.get_current_regime()
        self.assertIsNone(regime_signal)

    def test_get_regime_history_empty(self):
        """Test getting regime history with no data"""
        history = self.engine.get_regime_history()
        self.assertIsInstance(history, list)
        self.assertEqual(len(history), 0)

    def test_regime_classification_logic(self):
        """Test that regime classification works with known inputs"""
        base_time = datetime.now()

        # Create a clear trending up scenario with low volatility
        prices = [100, 100.5, 101, 101.5, 102, 102.5, 103, 103.5, 104, 104.5, 105]
        volumes = [1000] * len(prices)

        for i, (price, volume) in enumerate(zip(prices, volumes)):
            timestamp = base_time + timedelta(minutes=i*5)
            # Small ranges for low volatility
            high = price + 0.2
            low = price - 0.2
            open_price = price - 0.1 if i > 0 else price
            self.engine.update(price, volume, timestamp, "", high, low, open_price)

        # Get regime signal
        regime_signal = self.engine.get_current_regime()
        self.assertIsNotNone(regime_signal)

        # Should have valid values
        self.assertIsInstance(regime_signal.regime, MarketRegime)
        self.assertGreaterEqual(regime_signal.confidence, 0.0)
        self.assertLessEqual(regime_signal.confidence, 1.0)
        self.assertGreaterEqual(regime_signal.strength, 0.0)
        self.assertLessEqual(regime_signal.strength, 1.0)
        self.assertIsInstance(regime_signal.timestamp, datetime)
        self.assertIsInstance(regime_signal.supporting_factors, dict)

    def test_regime_change_detection(self):
        """Test regime change detection"""
        base_time = datetime.now()

        # Create initial quiet market conditions
        prices = [100] * 10  # Flat prices
        volumes = [1000] * 10

        for i, (price, volume) in enumerate(zip(prices, volumes)):
            timestamp = base_time + timedelta(minutes=i*5)
            high = price + 0.1
            low = price - 0.1
            open_price = price
            self.engine.update(price, volume, timestamp, "", high, low, open_price)

        initial_regime = self.engine.get_current_regime()
        self.assertIsNotNone(initial_regime)

        # Now create trending conditions
        trending_prices = [100 + i*0.5 for i in range(10)]  # Steady uptrend
        trending_volumes = [1500] * 10  # Higher volume

        for i, (price, volume) in enumerate(zip(trending_prices, trending_volumes)):
            timestamp = base_time + timedelta(minutes=(i+10)*5)
            high = price + 0.3
            low = price - 0.2
            open_price = price - 0.1 if i > 0 else price
            self.engine.update(price, volume, timestamp, "", high, low, open_price)

        # Check if regime changed
        regime_changed = self.engine.is_regime_change(lookback=3)
        # This might or might not be true depending on exact calculations, but should not crash
        self.assertIsInstance(regime_changed, bool)

    def test_regime_stability(self):
        """Test regime stability measurement"""
        base_time = datetime.now()

        # Create consistent regime
        prices = [100 + i*0.2 for i in range(15)]  # Steady uptrend
        volumes = [1200] * 15

        for i, (price, volume) in enumerate(zip(prices, volumes)):
            timestamp = base_time + timedelta(minutes=i*5)
            high = price + 0.2
            low = price - 0.1
            open_price = price - 0.05 if i > 0 else price
            self.engine.update(price, volume, timestamp, "", high, low, open_price)

        # Get stability (should be reasonably high for consistent trend)
        stability = self.engine.get_regime_stability(lookback=10)
        self.assertIsInstance(stability, float)
        self.assertGreaterEqual(stability, 0.0)
        self.assertLessEqual(stability, 1.0)


if __name__ == '__main__':
    unittest.main()