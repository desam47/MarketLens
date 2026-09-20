"""
Market regime detection engine for identifying market conditions.
"""
import logging
from collections import deque
from datetime import datetime
from enum import StrEnum
from itertools import islice
from typing import Any

from ..engines.timeframe import Timeframe
from ..indicators.adx import ADXIndicator
from ..indicators.atr import ATRIndicator
from ..indicators.bollinger_bands import BollingerBandsIndicator
from ..indicators.ema import EMAIndicator
from ..multitimeframe.multi_timeframe_engine import (
    ConfluenceSignal,
    MultiTimeframeEngine,
)
from ..trend.trend_engine import TrendDirection, TrendEngine, TrendSignal

logger = logging.getLogger(__name__)


class MarketRegime(StrEnum):
    """Phase 8: spec-compliant market regime classifications.

    Per the Phase 8 spec, a regime is one of:
        RISK_ON     - market participants are risk-on (uptrend)
        RISK_OFF    - market participants are risk-off (downtrend)
        NEUTRAL     - market is range-bound / no clear direction
        TRANSITION  - market is in flux / high volatility
        UNKNOWN     - not enough data to classify

    The 4 spec names are the externally-visible values. The
    ``MarketContextEngine`` aggregates 4 sub-regimes (SPY/QQQ/IWM/VIX)
    into a single market-wide regime. Internally, the per-symbol
    ``MarketRegimeEngine`` still runs the same classification logic;
    its old internal buckets (TRENDING_UP, RANGING, etc.) collapse
    to the 4 spec names before being returned.
    """
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    NEUTRAL = "neutral"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


class RegimeSignal:
    """Represents a market regime signal"""

    def __init__(self,
                 symbol: str,
                 regime: MarketRegime,
                 confidence: float,  # 0.0 to 1.0
                 strength: float,   # 0.0 to 1.0
                 supporting_factors: dict[str, Any],
                 timestamp: datetime):
        self.symbol = symbol
        self.regime = regime
        self.confidence = confidence
        self.strength = strength
        self.supporting_factors = supporting_factors
        self.timestamp = timestamp

    def __repr__(self):
        return (f"RegimeSignal({self.symbol} {self.regime.value} "
                f"conf:{self.confidence:.2f} str:{self.strength:.2f})")


class MarketRegimeEngine:
    """Engine for detecting market regime conditions"""

    def __init__(self, symbol: str, trend_engine: TrendEngine | None = None,
                 multitimeframe_engine: MultiTimeframeEngine | None = None):
        self.symbol = symbol

        # Phase 3.9.2: accept injected component engines so all callers
        # share the same warmed-up TrendEngine / MultiTimeframeEngine
        # instead of each instance building its own (which diverged
        # their indicator state and wasted CPU seeding the same bars
        # N times). Fall back to creating a fresh engine only when no
        # shared instance is provided (e.g. unit tests, one-off scripts).
        # When only an MTF engine is injected, reuse its existing shared trend
        # engine. Otherwise create one canonical trend engine and make every MTF
        # timeframe reference it. Regime updates can then feed the market tick
        # exactly once and refresh confluence as a read-only aggregation step.
        mtf_trends = (
            list(multitimeframe_engine.trend_engines.values())
            if multitimeframe_engine is not None
            else []
        )
        self.trend_engine = (
            trend_engine
            if trend_engine is not None
            else mtf_trends[0] if mtf_trends
            else TrendEngine(symbol)
        )
        self.multitimeframe_engine = (
            multitimeframe_engine
            if multitimeframe_engine is not None
            else MultiTimeframeEngine(symbol, trend_engine=self.trend_engine)
        )
        for timeframe in self.multitimeframe_engine.analysis_timeframes:
            self.multitimeframe_engine.trend_engines[timeframe] = self.trend_engine

        # Initialize technical indicators for regime detection
        self.atr_indicator = ATRIndicator(period=14)
        self.adx_indicator = ADXIndicator(period=14)
        self.bb_indicator = BollingerBandsIndicator(period=20, std_dev=2.0)
        self.ema_fast = EMAIndicator(period=20)
        self.ema_slow = EMAIndicator(period=50)

        # Regime history. Bounded deque so the cap is enforced by the
        # container on append (O(1)) instead of re-slicing the whole list
        # on every tick once the cap is reached.
        self.max_regime_history = 1000
        self.regime_history: deque[RegimeSignal] = deque(maxlen=self.max_regime_history)

        # Price history for breakout detection
        self.max_price_history = 100
        self.price_history: deque[tuple[datetime, float]] = deque(
            maxlen=self.max_price_history
        )

        # Regime thresholds (these would ideally be configurable)
        self.volatility_threshold_high = 0.05  # 5% ATR as % of price
        self.volatility_threshold_low = 0.01   # 1% ATR as % of price
        self.adx_trending_threshold = 25       # ADX > 25 indicates trending
        self.adx_strong_threshold = 40         # ADX > 40 indicates strong trend
        self.bb_width_threshold = 0.05         # Bollinger Band width threshold for squeeze

    def update(self, price: float, volume: float, timestamp: datetime,
               provider: str = "", high: float | None = None, low: float | None = None,
               open_price: float | None = None) -> None:
        """Update regime detection with new market data"""
        # Use provided OHLC or approximate from price
        high_price = high if high is not None else price
        low_price = low if low is not None else price
        open_price_val = open_price if open_price is not None else price

        # Update component engines
        self.trend_engine.update(price, volume, timestamp, provider)
        self.multitimeframe_engine.refresh_confluence(timestamp)

        # Update indicators
        # For simplicity, we'll use price as close, and approximate high/low/volume
        # In a real implementation, you'd pass actual OHLCV data
        ohlcv_data_point = {
            'open': open_price_val,
            'high': high_price,
            'low': low_price,
            'close': price,
            'volume': volume
        }

        # Update ATR (needs historical data)
        self.atr_indicator.update(ohlcv_data_point)

        # Update ADX (needs historical data)
        self.adx_indicator.update(ohlcv_data_point)

        # Update Bollinger Bands
        self.bb_indicator.update(ohlcv_data_point)

        # Update EMAs
        self.ema_fast.update({'close': price})
        self.ema_slow.update({'close': price})

        # Update price history for breakout detection. The deque's maxlen
        # evicts the oldest entry automatically — no manual re-slice needed.
        self.price_history.append((timestamp, price))

        # Generate regime signal
        self._generate_regime_signal(timestamp)

    def _generate_regime_signal(self, timestamp: datetime) -> None:
        """Generate a market regime signal based on all available data"""
        # Get inputs from component systems
        confluence_signal = self.multitimeframe_engine.get_current_confluence()
        overall_trend = self.trend_engine.get_overall_trend()

        # Get indicator values
        atr_value = self.atr_indicator.get_latest()
        adx_value = self.adx_indicator.get_latest()
        bb_bands = self.bb_indicator.get_bands()
        bb_upper = bb_bands['upper'][-1] if bb_bands['upper'] else None
        bb_middle = bb_bands['middle'][-1] if bb_bands['middle'] else None
        bb_lower = bb_bands['lower'][-1] if bb_bands['lower'] else None
        ema_fast_val = self.ema_fast.get_latest()
        ema_slow_val = self.ema_slow.get_latest()

        current_price = self.price_history[-1][1] if self.price_history else 0

        # Calculate volatility (ATR as percentage of price)
        volatility_pct = (atr_value / current_price) if current_price > 0 and atr_value else 0

        # Determine regime
        regime, confidence, strength, factors = self._classify_regime(
            confluence_signal, overall_trend,
            volatility_pct, adx_value,
            bb_upper, bb_middle, bb_lower,
            ema_fast_val, ema_slow_val,
            current_price, timestamp
        )

        # Create regime signal
        signal = RegimeSignal(
            symbol=self.symbol,
            regime=regime,
            confidence=confidence,
            strength=strength,
            supporting_factors=factors,
            timestamp=timestamp
        )

        # Store in history. The deque's maxlen caps it at
        # self.max_regime_history and evicts the oldest on overflow.
        self.regime_history.append(signal)

    def _classify_regime(self, confluence_signal: ConfluenceSignal,
                        overall_trend: TrendSignal,
                        volatility_pct: float, adx_value: float | None,
                        bb_upper: float | None, bb_middle: float | None, bb_lower: float | None,
                        ema_fast_val: float | None, ema_slow_val: float | None,
                        current_price: float, timestamp: datetime) -> tuple[MarketRegime, float, float, dict[str, Any]]:
        """Classify the current market regime"""

        factors = {}

        # Handle case where we don't have enough data
        if overall_trend is None or confluence_signal is None:
            factors['reason'] = 'insufficient_data'
            return MarketRegime.UNKNOWN, 0.0, 0.0, factors

        # Store key values for debugging
        factors['confluence_direction'] = confluence_signal.direction.value
        factors['confluence_alignment'] = confluence_signal.alignment_score
        factors['confluence_strength'] = confluence_signal.strength
        factors['trend_direction'] = overall_trend.direction.value
        factors['trend_strength'] = overall_trend.strength.value
        factors['trend_confidence'] = overall_trend.confidence
        factors['volatility_pct'] = volatility_pct
        factors['adx_value'] = adx_value if adx_value else 0

        # Calculate additional factors
        # BB Squeeze detection (low volatility)
        bb_width = None
        if bb_upper and bb_lower and bb_middle and bb_middle > 0:
            bb_width = (bb_upper - bb_lower) / bb_middle
            factors['bb_width'] = bb_width

        # EMA alignment
        ema_aligned = None
        if ema_fast_val and ema_slow_val and current_price > 0:
            ema_aligned = (ema_fast_val > ema_slow_val)  # Fast above slow = uptrend bias
            factors['ema_fast_above_slow'] = ema_aligned
            factors['ema_fast'] = ema_fast_val
            factors['ema_slow'] = ema_slow_val

        # Price relative to Bollinger Bands
        bb_position = None
        if bb_upper and bb_lower and bb_middle:
            if current_price > bb_upper:
                bb_position = 'above_upper'
            elif current_price < bb_lower:
                bb_position = 'below_lower'
            else:
                bb_position = 'within_bands'
            factors['bb_position'] = bb_position

            # Distance from middle band as percentage
            if bb_middle > 0:
                bb_distance_pct = abs(current_price - bb_middle) / bb_middle
                factors['bb_distance_pct'] = bb_distance_pct

        # Regime classification logic

        # 1. Check for extreme volatility → TRANSITION
        if volatility_pct > self.volatility_threshold_high:
            factors['primary_reason'] = 'high_volatility'
            return MarketRegime.TRANSITION, min(0.9, volatility_pct * 10), 0.8, factors

        # 2. Check for low volatility → NEUTRAL (or breakout direction)
        if volatility_pct < self.volatility_threshold_low:
            factors['primary_reason'] = 'low_volatility'
            if bb_width and bb_width < self.bb_width_threshold:
                # Bollinger Band squeeze — potential breakout coming
                factors['squeeze_detected'] = True
                if overall_trend.direction == TrendDirection.UPTREND:
                    return MarketRegime.RISK_ON, 0.7, 0.6, factors
                elif overall_trend.direction == TrendDirection.DOWNTREND:
                    return MarketRegime.RISK_OFF, 0.7, 0.6, factors
                else:
                    return MarketRegime.NEUTRAL, 0.6, 0.4, factors
            else:
                return MarketRegime.NEUTRAL, 0.8, 0.3, factors

        # 3. Check for trending markets (using ADX and alignment)
        is_trending = adx_value and adx_value > self.adx_trending_threshold
        good_alignment = confluence_signal.alignment_score > 0.6

        if is_trending and good_alignment:
            factors['primary_reason'] = 'strong_trend_good_alignment'
            if overall_trend.direction == TrendDirection.UPTREND:
                confidence = min(0.9, 0.5 + (adx_value - 25) / 50 * 0.4)
                strength = min(0.9, 0.4 + confluence_signal.alignment_score * 0.5)
                return MarketRegime.RISK_ON, confidence, strength, factors
            elif overall_trend.direction == TrendDirection.DOWNTREND:
                confidence = min(0.9, 0.5 + (adx_value - 25) / 50 * 0.4)
                strength = min(0.9, 0.4 + confluence_signal.alignment_score * 0.5)
                return MarketRegime.RISK_OFF, confidence, strength, factors

        # 4. Check for weak/trending but poor alignment → NEUTRAL
        if is_trending and not good_alignment:
            factors['primary_reason'] = 'trending_poor_alignment'
            if overall_trend.confidence < 0.5:
                return MarketRegime.NEUTRAL, 0.6, 0.4, factors
            else:
                return MarketRegime.NEUTRAL, 0.5, 0.3, factors

        # 5. Check for ranging markets (low ADX, mixed signals) → NEUTRAL
        if not is_trending or (adx_value and adx_value < self.adx_trending_threshold):
            factors['primary_reason'] = 'low_adx_ranging'
            if bb_width and bb_width > self.bb_width_threshold * 1.5:
                return MarketRegime.NEUTRAL, 0.7, 0.5, factors
            elif ema_fast_val and ema_slow_val:
                return MarketRegime.NEUTRAL, 0.6, 0.4, factors
            else:
                return MarketRegime.NEUTRAL, 0.5, 0.3, factors

        # 6. Default fallback
        factors['primary_reason'] = 'default_fallback'
        return MarketRegime.NEUTRAL, 0.4, 0.2, factors

    def get_current_regime(self) -> RegimeSignal | None:
        """Get the current market regime signal"""
        if self.regime_history:
            return self.regime_history[-1]
        return None

    def get_regime_history(self, limit: int | None = None) -> list[RegimeSignal]:
        """Get market regime signal history, oldest → newest."""
        # regime_history is a deque, which does not support slicing — take
        # the last `limit` entries via islice over the tail.
        if limit is None or limit >= len(self.regime_history):
            return list(self.regime_history)
        if limit <= 0:
            return []
        return list(islice(self.regime_history,
                           len(self.regime_history) - limit, None))

    def get_regime_for_timeframe(self, timeframe: Timeframe) -> RegimeSignal | None:
        """Get regime analysis for a specific timeframe (simplified - returns overall)"""
        # In a more sophisticated implementation, we might have timeframe-specific regime analysis
        return self.get_current_regime()

    def is_regime_change(self, lookback: int = 3) -> bool:
        """Check if the regime has changed in the last N periods"""
        if len(self.regime_history) < lookback + 1:
            return False

        current_regime = self.regime_history[-1].regime
        past_regime = self.regime_history[-(lookback + 1)].regime

        return current_regime != past_regime

    def get_regime_stability(self, lookback: int = 10) -> float:
        """Get how stable the regime has been (0.0 = constantly changing, 1.0 = stable)"""
        if len(self.regime_history) < lookback:
            return 0.0

        recent_regimes = [
            signal.regime
            for signal in islice(self.regime_history,
                                 len(self.regime_history) - lookback, None)
        ]
        if not recent_regimes:
            return 0.0

        # Count the most common regime
        from collections import Counter
        regime_counts = Counter(recent_regimes)
        most_common_count = regime_counts.most_common(1)[0][1]

        return most_common_count / len(recent_regimes)
