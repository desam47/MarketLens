"""
Tests for strategy selector
"""
import os
import sys
import unittest
from datetime import datetime

# Add the backend directory to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.engines.timeframe import Timeframe
from backend.multitimeframe.multi_timeframe_engine import (
    ConfluenceDirection,
    ConfluenceSignal,
)
from backend.regime.market_regime_engine import MarketRegime, RegimeSignal
from backend.strategy.strategy_selector import (
    StrategySelector,
    StrategySignal,
    StrategyType,
)
from backend.trend.trend_engine import TrendDirection, TrendSignal, TrendStrength


class TestStrategySelector(unittest.TestCase):

    def setUp(self):
        self.symbol = "AAPL"
        self.selector = StrategySelector(self.symbol)

    def test_selector_initialization(self):
        """Test that strategy selector initializes correctly"""
        self.assertEqual(self.selector.symbol, self.symbol)
        self.assertIsInstance(self.selector.selection_history, list)
        self.assertIsInstance(self.selector.strategy_params, dict)

    def test_select_strategy_trending_regime(self):
        """Test strategy selection for trending regime"""
        # Create trending regime signal
        regime_signal = RegimeSignal(
            symbol=self.symbol,
            regime=MarketRegime.TRENDING_UP,
            confidence=0.8,
            strength=0.7,
            supporting_factors={},
            timestamp=datetime.now()
        )

        # Create strong uptrend signal
        trend_signal = TrendSignal(
            symbol=self.symbol,
            timeframe=Timeframe.ONE_HOUR,
            direction=TrendDirection.UPTREND,
            strength=TrendStrength.STRONG,
            confidence=0.9,
            timestamp=datetime.now()
        )

        # Create aligned confluence signal
        confluence_signal = ConfluenceSignal(
            symbol=self.symbol,
            direction=ConfluenceDirection.STRONG_UPTREND,
            strength=0.8,
            alignment_score=0.9,
            timeframe_signals={Timeframe.ONE_HOUR: trend_signal},
            timestamp=datetime.now()
        )

        # Select strategy
        signal = self.selector.select_strategy(regime_signal, trend_signal, confluence_signal)

        # Should select trend following
        self.assertEqual(signal.strategy_type, StrategyType.TREND_FOLLOWING)
        self.assertGreater(signal.confidence, 0.5)
        self.assertIn('fast_ma', signal.parameters)
        self.assertIn('slow_ma', signal.parameters)

    def test_select_strategy_ranging_regime(self):
        """Test strategy selection for ranging regime"""
        # Create ranging regime signal
        regime_signal = RegimeSignal(
            symbol=self.symbol,
            regime=MarketRegime.RANGING,
            confidence=0.7,
            strength=0.3,
            supporting_factors={},
            timestamp=datetime.now()
        )

        # Create weak sideways trend signal
        trend_signal = TrendSignal(
            symbol=self.symbol,
            timeframe=Timeframe.ONE_HOUR,
            direction=TrendDirection.SIDEWAYS,
            strength=TrendStrength.WEAK,
            confidence=0.6,
            timestamp=datetime.now()
        )

        # Create neutral confluence signal
        confluence_signal = ConfluenceSignal(
            symbol=self.symbol,
            direction=ConfluenceDirection.NEUTRAL,
            strength=0.4,
            alignment_score=0.5,
            timeframe_signals={Timeframe.ONE_HOUR: trend_signal},
            timestamp=datetime.now()
        )

        # Select strategy
        signal = self.selector.select_strategy(regime_signal, trend_signal, confluence_signal)

        # Should favor mean reversion for ranging market
        # (Could be trend following if trend is strong, but here it's weak)
        self.assertIn(signal.strategy_type, [StrategyType.MEAN_REVERSION, StrategyType.TREND_FOLLOWING])
        self.assertGreater(signal.confidence, 0.3)

    def test_select_strategy_volatile_regime(self):
        """Test strategy selection for volatile regime"""
        # Create volatile regime signal
        regime_signal = RegimeSignal(
            symbol=self.symbol,
            regime=MarketRegime.VOLATILE,
            confidence=0.8,
            strength=0.8,
            supporting_factors={},
            timestamp=datetime.now()
        )

        # Select strategy
        signal = self.selector.select_strategy(regime_signal)

        # Should favor breakout or volatility strategies
        self.assertIn(signal.strategy_type, [StrategyType.BREAKOUT, StrategyType.VOLATILITY])
        self.assertGreater(signal.confidence, 0.5)

    def test_select_strategy_quiet_regime(self):
        """Test strategy selection for quiet regime"""
        # Create quiet regime signal
        regime_signal = RegimeSignal(
            symbol=self.symbol,
            regime=MarketRegime.QUIET,
            confidence=0.9,
            strength=0.2,
            supporting_factors={},
            timestamp=datetime.now()
        )

        # Select strategy
        signal = self.selector.select_strategy(regime_signal)

        # Should favor mean reversion for quiet markets
        self.assertEqual(signal.strategy_type, StrategyType.MEAN_REVERSION)
        self.assertGreater(signal.confidence, 0.5)

    def test_select_strategy_breakout_regime(self):
        """Test strategy selection for breakout regime"""
        # Create breakout regime signal
        regime_signal = RegimeSignal(
            symbol=self.symbol,
            regime=MarketRegime.BREAKOUT_UP,
            confidence=0.85,
            strength=0.9,
            supporting_factors={},
            timestamp=datetime.now()
        )

        # Select strategy
        signal = self.selector.select_strategy(regime_signal)

        # Should favor breakout strategies
        self.assertEqual(signal.strategy_type, StrategyType.BREAKOUT)
        self.assertGreater(signal.confidence, 0.5)

    def test_fallback_on_error(self):
        """Test that selector falls back gracefully on error"""
        # Create regime signal with None values that might cause issues
        regime_signal = RegimeSignal(
            symbol=self.symbol,
            regime=MarketRegime.UNKNOWN,
            confidence=0.0,  # Low confidence
            strength=0.0,
            supporting_factors={},
            timestamp=datetime.now()
        )

        # Should still return a valid signal (fallback to mean reversion)
        signal = self.selector.select_strategy(regime_signal)

        self.assertIsInstance(signal, StrategySignal)
        self.assertEqual(signal.symbol, self.symbol)
        self.assertGreaterEqual(signal.confidence, 0.0)
        self.assertLessEqual(signal.confidence, 1.0)

    def test_selection_history(self):
        """Test that selection history is maintained"""
        regime_signal = RegimeSignal(
            symbol=self.symbol,
            regime=MarketRegime.TRENDING_UP,
            confidence=0.8,
            strength=0.7,
            supporting_factors={},
            timestamp=datetime.now()
        )

        # Make multiple selections
        for i in range(3):
            self.selector.select_strategy(regime_signal)

        history = self.selector.get_selection_history()
        self.assertEqual(len(history), 3)

        # Test limit parameter
        limited_history = self.selector.get_selection_history(limit=2)
        self.assertEqual(len(limited_history), 2)

    def test_get_current_strategy(self):
        """Test getting current strategy"""
        regime_signal = RegimeSignal(
            symbol=self.symbol,
            regime=MarketRegime.TRENDING_UP,
            confidence=0.8,
            strength=0.7,
            supporting_factors={},
            timestamp=datetime.now()
        )

        # Initially no strategy
        self.assertIsNone(self.selector.get_current_strategy())

        # After selection
        signal = self.selector.select_strategy(regime_signal)
        current = self.selector.get_current_strategy()

        self.assertIsNotNone(current)
        self.assertEqual(current.symbol, self.symbol)
        self.assertEqual(current.strategy_type, signal.strategy_type)


if __name__ == '__main__':
    unittest.main()