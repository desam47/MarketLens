"""
Strategy selector that chooses optimal trading strategies based on
market regime, trend analysis, and multi-timeframe confluence.
"""

import logging
from datetime import datetime
from enum import StrEnum
from typing import Any

from ..multitimeframe.multi_timeframe_engine import (
    ConfluenceSignal,
)
from ..regime.market_regime_engine import MarketRegime, RegimeSignal
from ..trend.trend_engine import TrendDirection, TrendSignal, TrendStrength

logger = logging.getLogger(__name__)


class StrategyType(StrEnum):
    """Types of trading strategies"""

    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    SCALPING = "scalping"
    SWING = "swing"
    POSITION = "position"


class StrategySignal:
    """Represents a selected trading strategy with parameters"""

    def __init__(
        self,
        symbol: str,
        strategy_type: StrategyType,
        confidence: float,  # 0.0 to 1.0
        timeframe: str,  # Primary timeframe to trade
        parameters: dict[str, Any],
        regime_signal: RegimeSignal,
        trend_signal: TrendSignal | None = None,
        confluence_signal: ConfluenceSignal | None = None,
        timestamp: datetime = None,
    ):
        self.symbol = symbol
        self.strategy_type = strategy_type
        self.confidence = confidence
        self.timeframe = timeframe
        self.parameters = parameters
        self.regime_signal = regime_signal
        self.trend_signal = trend_signal
        self.confluence_signal = confluence_signal
        self.timestamp = timestamp or datetime.now()

    def __repr__(self):
        return (
            f"StrategySignal({self.symbol} {self.strategy_type.value} "
            f"conf:{self.confidence:.2f} tf:{self.timeframe})"
        )


class StrategySelector:
    """
    Selects optimal trading strategies based on market conditions:
    - Market regime (trending, ranging, volatile, etc.)
    - Trend strength and direction
    - Multi-timeframe confluence and alignment
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.selection_history: list[StrategySignal] = []

        # Strategy parameters for different market conditions
        self.strategy_params = {
            StrategyType.TREND_FOLLOWING: {
                "base": {"fast_ma": 9, "slow_ma": 21, "atr_period": 14},
                "strong_trend": {"fast_ma": 5, "slow_ma": 13, "atr_period": 10},
                "weak_trend": {"fast_ma": 12, "slow_ma": 26, "atr_period": 20},
            },
            StrategyType.MEAN_REVERSION: {
                "base": {"rsi_period": 14, "bb_std": 2.0, "lookback": 20},
                "high_vol": {"rsi_period": 10, "bb_std": 2.5, "lookback": 15},
                "low_vol": {"rsi_period": 20, "bb_std": 1.5, "lookback": 25},
            },
            StrategyType.BREAKOUT: {
                "base": {"bb_period": 20, "vol_threshold": 1.5, "momentum_period": 10},
                "squeeze": {"bb_period": 20, "vol_threshold": 1.2, "momentum_period": 8},
                "strong_vol": {"bb_period": 25, "vol_threshold": 2.0, "momentum_period": 15},
            },
            StrategyType.MOMENTUM: {
                "base": {"rsi_period": 14, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9},
                "strong": {"rsi_period": 10, "macd_fast": 8, "macd_slow": 21, "macd_signal": 7},
                "weak": {"rsi_period": 20, "macd_fast": 16, "macd_slow": 34, "macd_signal": 12},
            },
        }

    def select_strategy(
        self,
        regime_signal: RegimeSignal,
        trend_signal: TrendSignal | None = None,
        confluence_signal: ConfluenceSignal | None = None,
    ) -> StrategySignal:
        """
        Select optimal strategy based on market analysis

        Args:
            regime_signal: Current market regime from MarketRegimeEngine
            trend_signal: Current trend from TrendEngine (optional)
            confluence_signal: Multi-timeframe analysis from MultiTimeframeEngine (optional)

        Returns:
            StrategySignal with recommended strategy and parameters
        """
        # Defensive: a missing regime signal (engine still warming up, or
        # symbol has no data yet) shouldn't blow up the call. Fall back to a
        # conservative default and surface that explicitly via low confidence.
        if regime_signal is None:
            logger.debug(f"No regime signal for {self.symbol}; using conservative fallback")
            return StrategySignal(
                symbol=self.symbol,
                strategy_type=StrategyType.MEAN_REVERSION,
                confidence=0.0,
                timeframe="1h",
                parameters=self.strategy_params[StrategyType.MEAN_REVERSION]["base"],
                regime_signal=None,
                trend_signal=trend_signal,
                confluence_signal=confluence_signal,
                timestamp=datetime.now(),
            )

        try:
            # Determine base strategy from regime
            base_strategy = self._get_base_strategy_from_regime(regime_signal.regime)

            # Adjust based on trend strength
            adjusted_strategy = self._adjust_for_trend(base_strategy, trend_signal)

            # Adjust based on timeframe confluence
            final_strategy = self._adjust_for_confluence(adjusted_strategy, confluence_signal)

            # Get strategy-specific parameters
            parameters = self._get_strategy_parameters(
                final_strategy, regime_signal, trend_signal, confluence_signal
            )

            # Determine optimal timeframe
            timeframe = self._select_optimal_timeframe(
                regime_signal, trend_signal, confluence_signal
            )

            # Calculate overall confidence
            confidence = self._calculate_strategy_confidence(
                regime_signal, trend_signal, confluence_signal
            )

            # Create strategy signal
            signal = StrategySignal(
                symbol=self.symbol,
                strategy_type=final_strategy,
                confidence=confidence,
                timeframe=timeframe,
                parameters=parameters,
                regime_signal=regime_signal,
                trend_signal=trend_signal,
                confluence_signal=confluence_signal,
            )

            # Store in history
            self.selection_history.append(signal)
            if len(self.selection_history) > 100:
                self.selection_history = self.selection_history[-100:]

            logger.info(f"Selected strategy for {self.symbol}: {signal}")
            return signal

        except Exception as e:
            logger.error(f"Error selecting strategy for {self.symbol}: {e}")
            # Fallback to conservative mean reversion
            return StrategySignal(
                symbol=self.symbol,
                strategy_type=StrategyType.MEAN_REVERSION,
                confidence=0.3,
                timeframe="1h",
                parameters=self.strategy_params[StrategyType.MEAN_REVERSION]["base"],
                regime_signal=regime_signal,
                timestamp=datetime.now(),
            )

    def _get_base_strategy_from_regime(self, regime: MarketRegime) -> StrategyType:
        """Map market regime to base strategy type.

        Phase 8 spec maps the 4 spec regimes to strategy types:
            RISK_ON     → TREND_FOLLOWING  (follow the uptrend)
            RISK_OFF    → TREND_FOLLOWING  (downtrends are also trends)
            NEUTRAL     → MEAN_REVERSION   (range-bound)
            TRANSITION  → BREAKOUT         (volatility = breakout risk)
            UNKNOWN     → MEAN_REVERSION   (conservative fallback)
        """
        regime_map = {
            MarketRegime.RISK_ON: StrategyType.TREND_FOLLOWING,
            MarketRegime.RISK_OFF: StrategyType.TREND_FOLLOWING,
            MarketRegime.NEUTRAL: StrategyType.MEAN_REVERSION,
            MarketRegime.TRANSITION: StrategyType.BREAKOUT,
            MarketRegime.UNKNOWN: StrategyType.MEAN_REVERSION,
        }
        return regime_map.get(regime, StrategyType.MEAN_REVERSION)

    def _adjust_for_trend(
        self, base_strategy: StrategyType, trend_signal: TrendSignal | None
    ) -> StrategyType:
        """Adjust strategy based on trend strength and direction"""
        if not trend_signal:
            return base_strategy

        # In strong trends, favor trend-following even in ranging markets
        if trend_signal.strength in [TrendStrength.STRONG, TrendStrength.VERY_STRONG]:
            if base_strategy in [StrategyType.MEAN_REVERSION, StrategyType.SCALPING]:
                return StrategyType.TREND_FOLLOWING

        # In weak trends or choppy markets, favor mean reversion
        if trend_signal.strength == TrendStrength.WEAK:
            if base_strategy == StrategyType.TREND_FOLLOWING:
                # Only switch if not strongly trending
                if abs(self._get_trend_score(trend_signal)) < 0.5:
                    return StrategyType.MEAN_REVERSION

        return base_strategy

    def _adjust_for_confluence(
        self, base_strategy: StrategyType, confluence_signal: ConfluenceSignal | None
    ) -> StrategyType:
        """Adjust strategy based on multi-timeframe alignment"""
        if not confluence_signal:
            return base_strategy

        # High alignment (trending across timeframes) favors trend following
        if confluence_signal.alignment_score > 0.7:
            if base_strategy == StrategyType.MEAN_REVERSION:
                return StrategyType.TREND_FOLLOWING

        # Low alignment (conflicting timeframes) favors mean reversion or breakout
        if confluence_signal.alignment_score < 0.4:
            if base_strategy == StrategyType.TREND_FOLLOWING:
                return StrategyType.MEAN_REVERSION

        return base_strategy

    def _get_strategy_parameters(
        self,
        strategy_type: StrategyType,
        regime_signal: RegimeSignal,
        trend_signal: TrendSignal | None,
        confluence_signal: ConfluenceSignal | None,
    ) -> dict[str, Any]:
        """Get parameters optimized for current market conditions"""
        # Get base parameters
        params = self.strategy_params.get(strategy_type, {}).get("base", {}).copy()

        # Adjust based on regime volatility
        if regime_signal.regime == MarketRegime.TRANSITION:
            vol_adjustment = "high_vol"
        elif regime_signal.regime == MarketRegime.NEUTRAL:
            vol_adjustment = "low_vol"
        else:
            vol_adjustment = None

        if vol_adjustment and vol_adjustment in self.strategy_params.get(strategy_type, {}):
            # Update with volatility-specific parameters
            vol_params = self.strategy_params[strategy_type][vol_adjustment]
            params.update(vol_params)

        # Adjust based on trend strength
        if trend_signal:
            if trend_signal.strength == TrendStrength.STRONG:
                if "strong_trend" in self.strategy_params.get(strategy_type, {}):
                    params.update(self.strategy_params[strategy_type]["strong_trend"])
            elif trend_signal.strength == TrendStrength.WEAK:
                if "weak_trend" in self.strategy_params.get(strategy_type, {}):
                    params.update(self.strategy_params[strategy_type]["weak_trend"])

        # Adjust based on confluence
        if confluence_signal:
            # High alignment -> tighter stops, lower position size for trend following
            # Low alignment -> wider stops, smaller positions for mean reversion
            if confluence_signal.alignment_score > 0.8:
                params["position_size_multiplier"] = 1.2
                params["stop_loss_multiplier"] = 0.8
            elif confluence_signal.alignment_score < 0.3:
                params["position_size_multiplier"] = 0.6
                params["stop_loss_multiplier"] = 1.5

        return params

    def _select_optimal_timeframe(
        self,
        regime_signal: RegimeSignal,
        trend_signal: TrendSignal | None,
        confluence_signal: ConfluenceSignal | None,
    ) -> str:
        """Select the optimal timeframe to trade based on analysis"""
        # Default timeframes by regime (Phase 8: 4 spec names)
        regime_timeframes = {
            MarketRegime.RISK_ON: "4h",
            MarketRegime.RISK_OFF: "4h",
            MarketRegime.NEUTRAL: "1h",
            MarketRegime.TRANSITION: "15m",
            MarketRegime.UNKNOWN: "1h",
        }

        base_timeframe = regime_timeframes.get(regime_signal.regime, "1h")

        # Adjust based on trend signal timeframe if available and strong
        if trend_signal and trend_signal.confidence > 0.7:
            # Use the trend signal's timeframe if it's strong
            tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
            trend_tf = getattr(trend_signal.timeframe, "value", str(trend_signal.timeframe))
            if trend_tf in tf_map:
                # Weight toward higher timeframe if strong trend
                if trend_signal.confidence > 0.8:
                    return trend_tf

        # Adjust based on confluence
        if confluence_signal and confluence_signal.alignment_score > 0.7:
            # When timeframes are aligned, can use higher timeframe
            if base_timeframe in ["15m", "1h"]:
                return "4h"

        return base_timeframe

    def _calculate_strategy_confidence(
        self,
        regime_signal: RegimeSignal,
        trend_signal: TrendSignal | None,
        confluence_signal: ConfluenceSignal | None,
    ) -> float:
        """Calculate confidence in the selected strategy"""
        confidences = []

        # Regime confidence
        confidences.append(regime_signal.confidence)

        # Trend confidence (if available)
        if trend_signal:
            confidences.append(trend_signal.confidence)

        # Confluence confidence (derived from alignment and strength)
        if confluence_signal:
            confluence_confidence = (
                confluence_signal.alignment_score + confluence_signal.strength
            ) / 2
            confidences.append(confluence_confidence)

        # Weighted average (regime gets highest weight as it's the foundation)
        if len(confidences) == 1:
            return confidences[0]
        elif len(confidences) == 2:
            return confidences[0] * 0.6 + confidences[1] * 0.4
        else:
            return confidences[0] * 0.5 + confidences[1] * 0.3 + confidences[2] * 0.2

    def _get_trend_score(self, trend_signal: TrendSignal) -> float:
        """Convert trend signal to -1 to +1 score"""
        direction_map = {
            TrendDirection.UPTREND: 1,
            TrendDirection.DOWNTREND: -1,
            TrendDirection.SIDEWAYS: 0,
            TrendDirection.UNKNOWN: 0,
        }
        base_score = direction_map.get(trend_signal.direction, 0)

        # Adjust by strength
        strength_map = {
            TrendStrength.WEAK: 0.5,
            TrendStrength.MODERATE: 0.75,
            TrendStrength.STRONG: 1.0,
            TrendStrength.VERY_STRONG: 1.0,
        }
        strength_factor = strength_map.get(trend_signal.strength, 0.5)

        return base_score * strength_factor * trend_signal.confidence

    def get_selection_history(self, limit: int | None = None) -> list[StrategySignal]:
        """Get history of strategy selections"""
        history = self.selection_history
        if limit:
            return history[-limit:] if len(history) > limit else history
        return history.copy()

    def get_current_strategy(self) -> StrategySignal | None:
        """Get the most recently selected strategy"""
        return self.selection_history[-1] if self.selection_history else None
