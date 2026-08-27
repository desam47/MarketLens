"""
Multi-timeframe analysis engine for detecting trend confluence and alignment.
"""
import logging
from datetime import datetime
from enum import Enum

from ..engines.timeframe import Timeframe
from ..trend.trend_engine import TrendDirection, TrendEngine, TrendSignal, TrendStrength

logger = logging.getLogger(__name__)


class ConfluenceDirection(str, Enum):
    """Overall market direction based on multiple timeframes"""
    STRONG_UPTREND = "strong_uptrend"
    UPTREND = "uptrend"
    WEAK_UPTREND = "weak_uptrend"
    NEUTRAL = "neutral"
    WEAK_DOWNTREND = "weak_downtrend"
    DOWNTREND = "downtrend"
    STRONG_DOWNTREND = "strong_downtrend"


class ConfluenceSignal:
    """Represents a multi-timeframe confluence signal"""

    def __init__(self,
                 symbol: str,
                 direction: ConfluenceDirection,
                 strength: float,  # 0.0 to 1.0
                 alignment_score: float,  # How aligned timeframes are (0.0 to 1.0)
                 timeframe_signals: dict[Timeframe, TrendSignal],
                 timestamp: datetime):
        self.symbol = symbol
        self.direction = direction
        self.strength = strength
        self.alignment_score = alignment_score
        self.timeframe_signals = timeframe_signals
        self.timestamp = timestamp

    def __repr__(self):
        return (f"ConfluenceSignal({self.symbol} {self.direction.value} "
                f"str:{self.strength:.2f} align:{self.alignment_score:.2f})")


class MultiTimeframeEngine:
    """Engine for analyzing trends across multiple timeframes"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.trend_engines: dict[Timeframe, TrendEngine] = {}

        # Confluence history
        self.confluence_history: list[ConfluenceSignal] = []

        # Define which timeframes to analyze (prioritizing key ones)
        self.analysis_timeframes = [
            Timeframe.FIVE_MINUTE,
            Timeframe.FIFTEEN_MINUTE,
            Timeframe.ONE_HOUR,
            Timeframe.FOUR_HOUR,
            Timeframe.ONE_DAY
        ]

        self._initialize_trend_engines()

    def _initialize_trend_engines(self):
        """Initialize trend engines for each timeframe we want to analyze"""
        for timeframe in self.analysis_timeframes:
            self.trend_engines[timeframe] = TrendEngine(self.symbol)

    def update(self, price: float, volume: float, timestamp: datetime,
               provider: str = "") -> None:
        """Update all timeframe engines with new market data"""
        # Update each trend engine
        for timeframe, engine in self.trend_engines.items():
            engine.update(price, volume, timestamp, provider)

        # Generate confluence signal
        self._generate_confluence_signal(timestamp)

    def _generate_confluence_signal(self, timestamp: datetime) -> None:
        """Generate a multi-timeframe confluence signal"""
        # Get current trends from all timeframes
        timeframe_signals = {}
        for timeframe, engine in self.trend_engines.items():
            trend = engine.get_current_trend(timeframe)
            if trend:
                timeframe_signals[timeframe] = trend

        if not timeframe_signals:
            return

        # Calculate alignment score (how many timeframes agree)
        alignment_score = self._calculate_alignment(timeframe_signals)

        # Determine overall direction and strength
        direction, strength = self._calculate_overall_direction(timeframe_signals)

        # Create confluence signal
        signal = ConfluenceSignal(
            symbol=self.symbol,
            direction=direction,
            strength=strength,
            alignment_score=alignment_score,
            timeframe_signals=timeframe_signals,
            timestamp=timestamp
        )

        # Store in history
        self.confluence_history.append(signal)

        # Keep only last 1000 signals to prevent memory issues
        if len(self.confluence_history) > 1000:
            self.confluence_history = self.confluence_history[-1000:]

    def _calculate_alignment(self, signals: dict[Timeframe, TrendSignal]) -> float:
        """Calculate how aligned the timeframes are (0.0 = no alignment, 1.0 = perfect alignment)"""
        if len(signals) < 2:
            return 1.0  # Single timeframe is perfectly aligned with itself

        # Count uptrend, downtrend, and sideways signals
        uptrend_count = 0
        downtrend_count = 0
        sideways_count = 0

        for signal in signals.values():
            if signal.direction == TrendDirection.UPTREND:
                uptrend_count += 1
            elif signal.direction == TrendDirection.DOWNTREND:
                downtrend_count += 1
            else:
                sideways_count += 1

        total = len(signals)
        max_count = max(uptrend_count, downtrend_count, sideways_count)

        # Alignment is the percentage of timeframes in the dominant direction
        return max_count / total if total > 0 else 0.0

    def _calculate_overall_direction(self,
                                   signals: dict[Timeframe, TrendSignal]) -> tuple[ConfluenceDirection, float]:
        """Calculate overall direction and strength from timeframe signals"""
        if not signals:
            return ConfluenceDirection.NEUTRAL, 0.0

        # Weight timeframes by their importance (longer timeframes get more weight)
        timeframe_weights = {
            Timeframe.FIVE_MINUTE: 0.1,
            Timeframe.FIFTEEN_MINUTE: 0.15,
            Timeframe.ONE_HOUR: 0.2,
            Timeframe.FOUR_HOUR: 0.25,
            Timeframe.ONE_DAY: 0.3
        }

        # Calculate weighted scores
        weighted_score = 0.0
        total_weight = 0.0
        strength_values = []

        for timeframe, signal in signals.items():
            weight = timeframe_weights.get(timeframe, 0.1)

            # Convert direction to numeric score
            if signal.direction == TrendDirection.UPTREND:
                score = 1
            elif signal.direction == TrendDirection.DOWNTREND:
                score = -1
            else:
                score = 0

            weighted_score += score * weight * signal.confidence
            total_weight += weight

            # Convert strength to numeric value for averaging
            strength_map = {
                TrendStrength.WEAK: 1,
                TrendStrength.MODERATE: 2,
                TrendStrength.STRONG: 3,
                TrendStrength.VERY_STRONG: 4
            }
            strength_values.append(strength_map.get(signal.strength, 2))

        if total_weight > 0:
            avg_score = weighted_score / total_weight
        else:
            avg_score = 0.0

        # Calculate average strength (normalized to 0-1)
        avg_strength_raw = sum(strength_values) / len(strength_values) if strength_values else 2
        avg_strength = (avg_strength_raw - 1) / 3  # Convert 1-4 range to 0-1

        # Determine confluence direction based on score and alignment
        alignment = self._calculate_alignment(signals)

        # Strong signals require both good alignment and decent score
        if alignment > 0.6:  # At least 60% alignment
            if avg_score > 0.3:
                direction = ConfluenceDirection.STRONG_UPTREND
            elif avg_score > 0.1:
                direction = ConfluenceDirection.UPTREND
            elif avg_score > -0.1:
                direction = ConfluenceDirection.NEUTRAL
            elif avg_score > -0.3:
                direction = ConfluenceDirection.WEAK_DOWNTREND
            else:
                direction = ConfluenceDirection.STRONG_DOWNTREND
        else:
            # Poor alignment - reduce to weaker signals
            if avg_score > 0.2:
                direction = ConfluenceDirection.WEAK_UPTREND
            elif avg_score < -0.2:
                direction = ConfluenceDirection.WEAK_DOWNTREND
            else:
                direction = ConfluenceDirection.NEUTRAL

        # Strength is combination of alignment and average strength
        final_strength = (alignment * 0.5) + (avg_strength * 0.5)
        final_strength = max(0.0, min(1.0, final_strength))  # Clamp to 0-1

        return direction, final_strength

    def get_current_confluence(self) -> ConfluenceSignal | None:
        """Get the current confluence signal"""
        if self.confluence_history:
            return self.confluence_history[-1]
        return None

    def get_confluence_history(self, limit: int | None = None) -> list[ConfluenceSignal]:
        """Get confluence signal history"""
        if limit is None:
            return self.confluence_history.copy()
        return self.confluence_history[-limit:] if len(self.confluence_history) > limit else self.confluence_history.copy()

    def get_timeframe_trend(self, timeframe: Timeframe) -> TrendSignal | None:
        """Get current trend for a specific timeframe"""
        engine = self.trend_engines.get(timeframe)
        if engine:
            return engine.get_current_trend(timeframe)
        return None

    def get_all_timeframe_trends(self) -> dict[Timeframe, TrendSignal]:
        """Get current trends for all analyzed timeframes"""
        trends = {}
        for timeframe, engine in self.trend_engines.items():
            trend = engine.get_current_trend(timeframe)
            if trend:
                trends[timeframe] = trend
        return trends