"""
Trend and market structure engine
"""
import logging
from datetime import datetime
from enum import Enum
from typing import Any

from ..engines.timeframe import (
    Timeframe,
    multi_symbol_timeframe_engine,
)
from ..indicators.adx import ADXIndicator
from ..indicators.bollinger_bands import BollingerBandsIndicator
from ..indicators.ema import EMAIndicator
from ..indicators.macd import MACDIndicator
from ..indicators.rsi import RSIIndicator
from ..indicators.supertrend import SuperTrendIndicator

logger = logging.getLogger(__name__)

class TrendDirection(str, Enum):
    """Trend direction"""
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    SIDEWAYS = "sideways"
    UNKNOWN = "unknown"

class TrendStrength(str, Enum):
    """Trend strength"""
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"

class TrendSignal:
    """Represents a trend signal"""
    
    def __init__(self, symbol: str, timeframe: Timeframe,
                 direction: TrendDirection, strength: TrendStrength,
                 confidence: float, timestamp: datetime):
        self.symbol = symbol
        self.timeframe = timeframe
        self.direction = direction
        self.strength = strength
        self.confidence = confidence  # 0.0 to 1.0
        self.timestamp = timestamp
        self.indicators: dict[str, Any] = {}
    
    def __repr__(self):
        return (f"TrendSignal({self.symbol} {self.timeframe.value} "
                f"{self.direction.value} {self.strength.value} "
                f"conf:{self.confidence:.2f})")

class TrendEngine:
    """Engine for determining market trend across multiple timeframes"""
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.timeframe_engine = multi_symbol_timeframe_engine.get_engine_for_symbol(symbol)
        if self.timeframe_engine is None:
            # Create engine if it doesn't exist
            multi_symbol_timeframe_engine.update_tick(symbol, 0, 0, datetime.now())
            self.timeframe_engine = multi_symbol_timeframe_engine.get_engine_for_symbol(symbol)
        
        # Initialize indicators for each timeframe
        self.indicators: dict[Timeframe, dict[str, Any]] = {}
        self._initialize_indicators()
        
        # Trend history
        self.trend_history: dict[Timeframe, list[TrendSignal]] = {}
        for timeframe in Timeframe:
            if timeframe != Timeframe.TICK:
                self.trend_history[timeframe] = []
    
    def _initialize_indicators(self):
        """Initialize technical indicators for trend analysis"""
        # Define indicator configurations for different timeframes
        # Shorter timeframes use faster indicators, longer timeframes use slower ones
        
        timeframe_configs = {
            Timeframe.ONE_MINUTE: {
                "ema_fast": EMAIndicator(9),
                "ema_slow": EMAIndicator(21),
                "rsi": RSIIndicator(14),
                "macd": MACDIndicator(12, 26, 9)
            },
            Timeframe.FIVE_MINUTE: {
                "ema_fast": EMAIndicator(12),
                "ema_slow": EMAIndicator(26),
                "rsi": RSIIndicator(14),
                "macd": MACDIndicator(12, 26, 9),
                "adx": ADXIndicator(14)
            },
            Timeframe.FIFTEEN_MINUTE: {
                "ema_fast": EMAIndicator(20),
                "ema_slow": EMAIndicator(50),
                "rsi": RSIIndicator(14),
                "macd": MACDIndicator(12, 26, 9),
                "adx": ADXIndicator(14),
                "supertrend": SuperTrendIndicator(10, 3.0)
            },
            Timeframe.ONE_HOUR: {
                "ema_fast": EMAIndicator(20),
                "ema_slow": EMAIndicator(50),
                "rsi": RSIIndicator(14),
                "macd": MACDIndicator(12, 26, 9),
                "adx": ADXIndicator(14),
                "supertrend": SuperTrendIndicator(10, 3.0),
                "bollinger_bands": BollingerBandsIndicator(20, 2.0)
            },
            Timeframe.FOUR_HOUR: {
                "ema_fast": EMAIndicator(50),
                "ema_slow": EMAIndicator(100),
                "rsi": RSIIndicator(14),
                "macd": MACDIndicator(12, 26, 9),
                "adx": ADXIndicator(14),
                "supertrend": SuperTrendIndicator(10, 3.0),
                "bollinger_bands": BollingerBandsIndicator(20, 2.0)
            },
            Timeframe.ONE_DAY: {
                "ema_fast": EMAIndicator(50),
                "ema_slow": EMAIndicator(200),
                "rsi": RSIIndicator(14),
                "macd": MACDIndicator(12, 26, 9),
                "adx": ADXIndicator(14),
                "supertrend": SuperTrendIndicator(10, 3.0),
                "bollinger_bands": BollingerBandsIndicator(20, 2.0)
            }
        }
        
        # Initialize indicators for each timeframe
        for timeframe, indicators in timeframe_configs.items():
            self.indicators[timeframe] = indicators
    
    def update(self, price: float, volume: float, 
               timestamp: datetime, provider: str = ""):
        """Update trend engine with new market data"""
        # Update the timeframe engine with new tick
        self.timeframe_engine.update_tick(price, volume, timestamp, provider)
        
        # Update indicators for each timeframe
        self._update_indicators(price, volume, timestamp)
        
        # Generate trend signals
        self._generate_trend_signals(timestamp)
    
    def _update_indicators(self, price: float, volume: float, 
                          timestamp: datetime):
        """Update all indicators with new data"""
        # Create a data point for indicators
        data_point = {
            'open': price,  # Simplified - in reality we'd need OHLC
            'high': price,
            'low': price,
            'close': price,
            'volume': volume
        }
        
        # Update indicators for each timeframe
        for timeframe, indicators in self.indicators.items():
            for name, indicator in indicators.items():
                try:
                    indicator.update(data_point)
                except Exception as e:
                    logger.debug(f"Error updating {name} indicator for {timeframe}: {e}")
    
    def _generate_trend_signals(self, timestamp: datetime):
        """Generate trend signals for each timeframe"""
        for timeframe, indicators in self.indicators.items():
            if timeframe == Timeframe.TICK:
                continue  # Skip tick timeframe for trend analysis
            
            try:
                signal = self._analyze_timeframe_trend(timeframe, indicators, timestamp)
                if signal:
                    self.trend_history[timeframe].append(signal)
                    # Keep history manageable (last 100 signals)
                    if len(self.trend_history[timeframe]) > 100:
                        self.trend_history[timeframe] = self.trend_history[timeframe][-100:]
            except Exception as e:
                logger.error(f"Error generating trend signal for {timeframe}: {e}")
    
    def _analyze_timeframe_trend(self, timeframe: Timeframe, 
                                indicators: dict[str, Any],
                                timestamp: datetime) -> TrendSignal | None:
        """Analyze trend for a specific timeframe"""
        # Get latest indicator values
        indicator_values = {}
        for name, indicator in indicators.items():
            try:
                value = indicator.get_latest()
                indicator_values[name] = value
            except Exception as e:
                logger.debug(f"Error getting value for {name} indicator: {e}")
                indicator_values[name] = None
        
        # Skip if we don't have enough data
        if all(v is None for v in indicator_values.values()):
            return None
        
        # Analyze trend based on available indicators
        direction, strength, confidence = self._calculate_trend(
            timeframe, indicator_values
        )
        
        return TrendSignal(
            symbol=self.symbol,
            timeframe=timeframe,
            direction=direction,
            strength=strength,
            confidence=confidence,
            timestamp=timestamp
        )
    
    def _calculate_trend(self, timeframe: Timeframe, 
                        indicator_values: dict[str, float | None]) -> tuple:
        """Calculate trend direction, strength, and confidence from indicators"""
        # This is a simplified trend calculation
        # In a real implementation, you'd use more sophisticated logic
        
        signals = []
        weights = []
        
        # EMA crossover signal
        ema_fast = indicator_values.get("ema_fast")
        ema_slow = indicator_values.get("ema_slow")
        if ema_fast is not None and ema_slow is not None:
            if ema_fast > ema_slow:
                signals.append(1)  # Uptrend signal
            else:
                signals.append(-1)  # Downtrend signal
            weights.append(0.3)  # Weight for EMA signal
        
        # RSI signal
        rsi = indicator_values.get("rsi")
        if rsi is not None:
            if rsi > 70:
                signals.append(-1)  # Overbought - potential downtrend
            elif rsi < 30:
                signals.append(1)   # Oversold - potential uptrend
            else:
                signals.append(0)   # Neutral
            weights.append(0.2)  # Weight for RSI signal
        
        # MACD signal
        macd = indicator_values.get("macd")
        if macd is not None:
            # For MACD, we'd normally look at MACD line vs signal line
            # Simplified: positive MACD suggests uptrend
            if macd > 0:
                signals.append(1)
            else:
                signals.append(-1)
            weights.append(0.2)  # Weight for MACD signal
        
        # ADX signal (trend strength)
        adx = indicator_values.get("adx")
        trend_strength = TrendStrength.MODERATE  # Default
        if adx is not None:
            if adx > 25:
                if adx > 40:
                    trend_strength = TrendStrength.STRONG
                else:
                    trend_strength = TrendStrength.MODERATE
            else:
                trend_strength = TrendStrength.WEAK
        
        # SuperTrend signal (placeholder; not yet fed into the trend model —
        # see TODO below to wire close vs. supertrend comparison)
        # TODO: indicator_values.get("supertrend") / ("close") comparison
        # _ = indicator_values.get("supertrend")
        # _ = indicator_values.get("close")
        
        # Calculate weighted average for direction
        if signals and weights:
            weighted_sum = sum(s * w for s, w in zip(signals, weights))
            total_weight = sum(weights)
            if total_weight > 0:
                avg_signal = weighted_sum / total_weight
                
                if avg_signal > 0.3:
                    direction = TrendDirection.UPTREND
                elif avg_signal < -0.3:
                    direction = TrendDirection.DOWNTREND
                else:
                    direction = TrendDirection.SIDEWAYS
                
                # Confidence based on agreement and strength
                confidence = min(abs(avg_signal), 1.0)
                # Adjust confidence based on trend strength
                if trend_strength == TrendStrength.STRONG:
                    confidence = min(confidence * 1.2, 1.0)
                elif trend_strength == TrendStrength.WEAK:
                    confidence = confidence * 0.8
            else:
                direction = TrendDirection.UNKNOWN
                confidence = 0.0
        else:
            direction = TrendDirection.UNKNOWN
            confidence = 0.0
        
        return direction, trend_strength, confidence
    
    def get_current_trend(self, timeframe: Timeframe) -> TrendSignal | None:
        """Get the current trend signal for a timeframe"""
        if self.trend_history.get(timeframe):
            return self.trend_history[timeframe][-1]
        return None
    
    def get_trend_history(self, timeframe: Timeframe, 
                         limit: int | None = None) -> list[TrendSignal]:
        """Get trend history for a timeframe"""
        history = self.trend_history.get(timeframe, [])
        if limit is not None:
            return history[-limit:] if len(history) > limit else history
        return history.copy()
    
    def get_multi_timeframe_trend(self) -> dict[Timeframe, TrendSignal]:
        """Get current trend for all timeframes"""
        trends = {}
        for timeframe in Timeframe:
            if timeframe != Timeframe.TICK:
                trend = self.get_current_trend(timeframe)
                if trend:
                    trends[timeframe] = trend
        return trends
    
    def get_overall_trend(self) -> TrendSignal | None:
        """Get overall trend based on multiple timeframes"""
        # Weight longer timeframes more heavily
        timeframe_weights = {
            Timeframe.ONE_MINUTE: 0.1,
            Timeframe.FIVE_MINUTE: 0.15,
            Timeframe.FIFTEEN_MINUTE: 0.2,
            Timeframe.ONE_HOUR: 0.25,
            Timeframe.FOUR_HOUR: 0.15,
            Timeframe.ONE_DAY: 0.15
        }
        
        trends = self.get_multi_timeframe_trend()
        if not trends:
            return None
        
        # Calculate weighted average direction
        direction_scores = []
        total_weight = 0
        
        for timeframe, trend in trends.items():
            weight = timeframe_weights.get(timeframe, 0.1)
            # Convert direction to numeric score
            if trend.direction == TrendDirection.UPTREND:
                score = 1
            elif trend.direction == TrendDirection.DOWNTREND:
                score = -1
            else:
                score = 0
            
            direction_scores.append(score * weight * trend.confidence)
            total_weight += weight
        
        if total_weight > 0:
            avg_score = sum(direction_scores) / total_weight
            
            if avg_score > 0.2:
                overall_direction = TrendDirection.UPTREND
            elif avg_score < -0.2:
                overall_direction = TrendDirection.DOWNTREND
            else:
                overall_direction = TrendDirection.SIDEWAYS
            
            # Overall confidence
            overall_confidence = min(abs(avg_score), 1.0)
            
            # Overall strength (average of individual strengths)
            strength_values = []
            for trend in trends.values():
                if trend.strength == TrendStrength.WEAK:
                    strength_values.append(1)
                elif trend.strength == TrendStrength.MODERATE:
                    strength_values.append(2)
                elif trend.strength == TrendStrength.STRONG:
                    strength_values.append(3)
                elif trend.strength == TrendStrength.VERY_STRONG:
                    strength_values.append(4)
            
            avg_strength = sum(strength_values) / len(strength_values) if strength_values else 2
            if avg_strength <= 1.5:
                overall_strength = TrendStrength.WEAK
            elif avg_strength <= 2.5:
                overall_strength = TrendStrength.MODERATE
            elif avg_strength <= 3.5:
                overall_strength = TrendStrength.STRONG
            else:
                overall_strength = TrendStrength.VERY_STRONG
            
            return TrendSignal(
                symbol=self.symbol,
                timeframe=Timeframe.ONE_DAY,  # Use daily as representative
                direction=overall_direction,
                strength=overall_strength,
                confidence=overall_confidence,
                timestamp=datetime.now()
            )
        
        return None

# Global trend engine factory
def get_trend_engine(symbol: str) -> TrendEngine:
    """Get or create trend engine for a symbol"""
    # In a real implementation, you might cache these
    return TrendEngine(symbol)
