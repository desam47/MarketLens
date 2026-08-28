"""
Trend and market structure engine
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from ..config.settings import settings as _settings
from ..engines.timeframe import (
    Timeframe,
    multi_symbol_timeframe_engine,
)
from ..indicators.base_indicator import IndicatorEngine

logger = logging.getLogger(__name__)

# Per-timeframe EMA fast/slow periods — kept here (not in settings) because
# they are a per-timeframe lookup, not a single configurable default. The
# actual indicator default *parameters* (RSI period, MACD, etc.) live in
# ``TrendSettings.indicators`` (Principle 12).
_TIMEFRAME_EMA: dict[Timeframe, tuple[int, int]] = {
    Timeframe.ONE_MINUTE: (9, 21),
    Timeframe.TWO_MINUTE: (9, 21),
    Timeframe.THREE_MINUTE: (12, 26),
    Timeframe.FIVE_MINUTE: (12, 26),
    Timeframe.FIFTEEN_MINUTE: (20, 50),
    Timeframe.THIRTY_MINUTE: (15, 30),
    Timeframe.ONE_HOUR: (20, 50),
    Timeframe.FOUR_HOUR: (50, 100),
    Timeframe.ONE_DAY: (50, 200),
    Timeframe.ONE_WEEK: (50, 200),
}

class TrendDirection(StrEnum):
    """Trend direction"""
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    SIDEWAYS = "sideways"
    UNKNOWN = "unknown"

class TrendStrength(StrEnum):
    """Trend strength"""
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"

class TrendClassification(StrEnum):
    """8-class trend classification per Phase 6 spec.

    Buckets (mirroring ``docs/prompts/PHASE 6.md`` exactly):
        +70 to +100 = STRONG_BULLISH
        +30 to +69  = BULLISH
        +10 to +29  = WEAK_BULLISH
         -9 to  +9  = NEUTRAL
        -10 to -29  = WEAK_BEARISH
        -30 to -69  = BEARISH
        -70 to -100 = STRONG_BEARISH
        NO_SIGNAL           = data insufficient
    """
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    WEAK_BULLISH = "weak_bullish"
    NEUTRAL = "neutral"
    WEAK_BEARISH = "weak_bearish"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"
    NO_SIGNAL = "no_signal"


def classify_score(score: float | None) -> TrendClassification:
    """Map a -100..+100 score to its 8-class bucket.

    Phase 6 spec: pure function over ``score``. ``None`` (insufficient
    data) is mapped to ``NO_SIGNAL``. Out-of-range scores are clamped
    to the nearest bucket.
    """
    if score is None:
        return TrendClassification.NO_SIGNAL
    if score >= 70:
        return TrendClassification.STRONG_BULLISH
    if score >= 30:
        return TrendClassification.BULLISH
    if score >= 10:
        return TrendClassification.WEAK_BULLISH
    if score > -10:
        return TrendClassification.NEUTRAL
    if score > -30:
        return TrendClassification.WEAK_BEARISH
    if score > -70:
        return TrendClassification.BEARISH
    return TrendClassification.STRONG_BEARISH


def strength_to_float(strength: TrendStrength) -> float:
    """Map the 4-value ``TrendStrength`` enum to a 0..1 numeric scale.

    Used by ``TrendSnapshot`` so callers can plot a single normalized
    strength number without unpacking the enum.
    """
    return {
        TrendStrength.WEAK: 0.25,
        TrendStrength.MODERATE: 0.5,
        TrendStrength.STRONG: 0.75,
        TrendStrength.VERY_STRONG: 1.0,
    }.get(strength, 0.5)

class TrendSignal:
    """Represents a trend signal.

    The 8-class ``classification`` field (Phase 6) mirrors the spec bucket
    for display and filtering. It is *derived* from ``score`` (not independent).
    The 4-value ``direction`` field is kept for backwards compatibility with
    callers that already match on ``TrendDirection``.
    """

    def __init__(self, symbol: str, timeframe: Timeframe,
                 direction: TrendDirection, strength: TrendStrength,
                 confidence: float, timestamp: datetime,
                 score: float | None = None,
                 classification: TrendClassification | None = None,
                 data_quality: str = "ok"):
        self.symbol = symbol
        self.timeframe = timeframe
        self.direction = direction
        self.strength = strength
        self.confidence = confidence  # 0.0 to 1.0
        self.timestamp = timestamp
        # ``score`` is the raw weighted directional signal in -100..+100
        # (used by TrendTransitionEngine and DivergenceEngine). It defaults
        # to ``confidence`` scaled by direction so the field is always
        # meaningful even for callers that never computed it.
        if score is None:
            sign = 0
            if direction == TrendDirection.UPTREND:
                sign = 1
            elif direction == TrendDirection.DOWNTREND:
                sign = -1
            self.score = sign * confidence * 100.0
        else:
            self.score = float(score)
        # Phase 6: 8-class classification (derived from score)
        self.classification = (
            classification
            if classification is not None
            else classify_score(self.score)
        )
        # Phase 6: data quality stamp. Defaults to "ok" so existing callers
        # are unaffected; set by the engine when stale/duplicate/gap is hit.
        self.data_quality = data_quality
        self.indicators: dict[str, Any] = {}

    def __repr__(self):
        return (f"TrendSignal({self.symbol} {self.timeframe.value} "
                f"{self.direction.value} {self.classification.value} "
                f"conf:{self.confidence:.2f})")


@dataclass
class TrendSnapshot:
    """Flat snapshot of trend state per Phase 6 spec.

    All nine fields listed in the spec example are present, plus
    ``direction`` (8-class) and ``strength`` (0..1 numeric) for easy
    consumption by scanners, dashboards, and backtests without needing to
    unpack enums.
    """
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    direction: TrendClassification   # 8-class enum
    score: float                   # -100..+100
    strength: float                # 0.0..1.0
    momentum: float                # ROC value (%)
    structure: float              # Bollinger %B value
    data_quality: str             # "ok" | "stale" | "duplicate" | "gap"
    strategy_version: str         # from TrendSettings.strategy_version

class TrendEngine:
    """Engine for determining market trend across multiple timeframes"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.timeframe_engine = multi_symbol_timeframe_engine.get_engine_for_symbol(symbol)
        if self.timeframe_engine is None:
            # Create engine if it doesn't exist
            multi_symbol_timeframe_engine.update_tick(symbol, 0, 0, datetime.now())
            self.timeframe_engine = multi_symbol_timeframe_engine.get_engine_for_symbol(symbol)

        # Phase 0 Principle 15: data quality tracking.
        # Populated during update() so the next call can detect stale,
        # duplicate, or gap conditions.
        self._last_update_time: datetime | None = None
        self._last_price: float | None = None

        # Initialize indicators for each timeframe
        self.indicators: dict[Timeframe, dict[str, Any]] = {}
        self._initialize_indicators()

        # Trend history
        self.trend_history: dict[Timeframe, list[TrendSignal]] = {}
        for timeframe in Timeframe:
            if timeframe != Timeframe.TICK:
                self.trend_history[timeframe] = []

    def _initialize_indicators(self):
        """Initialize technical indicators for trend analysis.

        Builds per-timeframe indicator stacks using ``IndicatorEngine`` (Phase 0
        Principle 17: facade, not concrete imports) and ``TrendSettings`` (Phase 0
        Principle 12: no hard-coded indicator parameters). Short timeframes get
        a minimal stack; richer timeframes add ADX, SuperTrend, and Bollinger.
        Phase 6 also adds ``relative_volume`` and ``roc`` (Momentum) to the
        stack for timeframes >= 15m — these are the volume and momentum
        scoring components called out in the spec.
        """
        defaults = _settings.trend.indicators
        for timeframe, (ema_fast, ema_slow) in _TIMEFRAME_EMA.items():
            stack = IndicatorEngine.build_timeframe_stack(ema_fast, ema_slow, defaults)
            # Add Phase 6 momentum + volume components for timeframes with
            # enough data to make them meaningful.
            if timeframe not in (
                Timeframe.ONE_MINUTE,
                Timeframe.TWO_MINUTE,
                Timeframe.THREE_MINUTE,
                Timeframe.FIVE_MINUTE,
            ):
                stack["relative_volume"] = IndicatorEngine.create_indicator(
                    "relative_volume", {"period": defaults.bollinger_period},
                )
                stack["roc"] = IndicatorEngine.create_indicator(
                    "roc", {"period": 12},
                )
            # Sub-5m timeframes get a minimal set — no ADX, no supertrend,
            # no bollinger. 2m/3m follow the 1m pattern (just ema + rsi + macd).
            if timeframe in (Timeframe.ONE_MINUTE, Timeframe.TWO_MINUTE, Timeframe.THREE_MINUTE):
                self.indicators[timeframe] = {
                    k: v
                    for k, v in stack.items()
                    if k in ("ema_fast", "ema_slow", "rsi", "macd")
                }
            elif timeframe == Timeframe.FIVE_MINUTE:
                self.indicators[timeframe] = {
                    k: v
                    for k, v in stack.items()
                    if k in ("ema_fast", "ema_slow", "rsi", "macd", "adx")
                }
            else:
                self.indicators[timeframe] = stack

    def update(self, price: float, volume: float,
               timestamp: datetime, provider: str = ""):
        """Update trend engine with new market data.

        Phase 0 Principle 15 — data quality validated before analysis.
        Before feeding the tick to the timeframe/indicator stack we run
        three cheap checks: stale (timestamp is older than the configured
        threshold), duplicate (price matches the previous tick within
        tolerance), and gap (more wall-clock time has passed than the
        configured max gap). Each violation is logged so callers can
        observe data quality without inspecting internals.
        """
        dq = _settings.data_quality
        now = datetime.now()

        # --- Stale check -------------------------------------------------
        if self._last_update_time is not None:
            age = (now - self._last_update_time).total_seconds()
            if age > dq.stale_threshold_seconds:
                logger.warning(
                    "Data-quality [stale]: %s last tick was %.1fs ago "
                    "(threshold %.1fs)",
                    self.symbol, age, dq.stale_threshold_seconds,
                )

        # --- Duplicate check --------------------------------------------
        if self._last_price is not None:
            if abs(price - self._last_price) <= dq.duplicate_price_tolerance:
                logger.debug(
                    "Data-quality [duplicate]: %s price %s == last %s",
                    self.symbol, price, self._last_price,
                )

        # --- Gap check --------------------------------------------------
        # A gap is defined as: a large jump in incoming tick timestamps
        # compared to the previous tick timestamp. Useful for catching
        # missed bars / disconnect windows from the upstream feed.
        if self._last_update_time is not None and timestamp is not None:
            incoming_gap = (timestamp - self._last_update_time).total_seconds()
            if incoming_gap > dq.max_tick_gap_seconds:
                logger.warning(
                    "Data-quality [gap]: %s incoming tick is %.1fs after "
                    "previous (threshold %.1fs)",
                    self.symbol, incoming_gap, dq.max_tick_gap_seconds,
                )

        # Update the timeframe engine with new tick
        self.timeframe_engine.update_tick(price, volume, timestamp, provider)

        # Update indicators for each timeframe
        self._update_indicators(price, volume, timestamp)

        # Generate trend signals
        self._generate_trend_signals(timestamp)

        # Record state for the next update() call.
        self._last_update_time = now
        self._last_price = price

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
        result = self._calculate_trend(timeframe, indicator_values)
        direction, strength, confidence, raw_score, classification = result

        return TrendSignal(
            symbol=self.symbol,
            timeframe=timeframe,
            direction=direction,
            strength=strength,
            confidence=confidence,
            timestamp=timestamp,
            score=raw_score,
            classification=classification,
        )

    def _calculate_trend(self, timeframe: Timeframe,
                        indicator_values: dict[str, float | None]
                        ) -> tuple:
        """Calculate trend direction, strength, and confidence from indicators.

        Phase 6: wires all 8 configurable components (EMA, RSI, MACD, ADX,
        SuperTrend, Bollinger Bands, Volume, Momentum/ROC). Returns a 5-tuple
        ``(direction, strength, confidence, score, classification)``.
        ``score`` is the raw weighted directional signal in -100..+100.
        ``classification`` maps ``score`` to the 8-class enum.
        """
        weights_cfg = _settings.trend.signal_weights

        # Pairs of (signal, weight) — never one without the other.
        components: list[tuple[float, float]] = []
        trend_strength = TrendStrength.MODERATE  # updated below

        # --- Component 1: EMA crossover ---
        ema_fast = indicator_values.get("ema_fast")
        ema_slow = indicator_values.get("ema_slow")
        if ema_fast is not None and ema_slow is not None:
            components.append(
                (1.0 if ema_fast > ema_slow else -1.0, weights_cfg.ema)
            )

        # --- Component 2: RSI overbought/oversold ---
        rsi = indicator_values.get("rsi")
        if rsi is not None:
            if rsi > 70:
                components.append((-1.0, weights_cfg.rsi))
            elif rsi < 30:
                components.append((1.0, weights_cfg.rsi))
            else:
                components.append((0.0, weights_cfg.rsi))

        # --- Component 3: MACD sign ---
        macd = indicator_values.get("macd")
        if macd is not None:
            components.append(
                (1.0 if macd > 0 else -1.0, weights_cfg.macd)
            )

        # --- Component 4: ADX trend strength (affects confidence, not score) ---
        adx = indicator_values.get("adx")
        if adx is not None:
            if adx > 40:
                trend_strength = TrendStrength.STRONG
            elif adx > 25:
                trend_strength = TrendStrength.MODERATE
            else:
                trend_strength = TrendStrength.WEAK

        # --- Component 5: SuperTrend direction ---
        # ``indicator_values`` holds the scalar SuperTrend line value.
        # The instance attribute ``is_uptrend`` (set by SuperTrend.update())
        # tells us whether the current close is above the line.
        supertrend_ind = next(
            (ind for ind in self.indicators.get(timeframe, {}).values()
             if getattr(ind, "name", "") == "SuperTrend"),
            None,
        )
        if supertrend_ind is not None:
            st_is_up = getattr(supertrend_ind, "is_uptrend", None)
            if st_is_up is not None:
                components.append(
                    (1.0 if st_is_up else -1.0, weights_cfg.supertrend)
                )

        # --- Component 6: Bollinger Bands market structure ---
        # Use ``percent_b`` deviation from 0.5 as the signal.
        # ``get_latest()`` on BollingerBandsIndicator returns %B.
        bb = indicator_values.get("bollinger_bands")
        if bb is not None:
            # %B ∈ [0,1]: 0 = at lower band, 0.5 = at middle, 1 = at upper.
            # Deviation from 0.5 gives a directional signal in [-0.5, +0.5].
            struct = bb - 0.5
            components.append((struct, weights_cfg.bollinger))

        # --- Component 7: Volume confirmation ---
        # RelativeVolume = current volume / its SMA.  >1 = above-average volume.
        # Only use when RV has warmed up; otherwise skip this component.
        rel_vol = indicator_values.get("relative_volume")
        if rel_vol is not None and rel_vol > 0:
            if rel_vol > 1.5:
                components.append((0.5, weights_cfg.relative_volume))
            elif rel_vol < 0.5:
                components.append((-0.5, weights_cfg.relative_volume))
            # else: 0.5..1.5 → neutral; omit

        # --- Component 8: Momentum (ROC sign) ---
        roc = indicator_values.get("roc")
        if roc is not None:
            if roc > 0:
                components.append((1.0, weights_cfg.momentum))
            elif roc < 0:
                components.append((-1.0, weights_cfg.momentum))
            # else: omit

        # --- Combine ---
        if components:
            # Filter to only pairs where weight > 0
            active = [(s, w) for s, w in components if w > 0]
            if active:
                signals_out = [s for s, _ in active]
                weights_out = [w for _, w in active]
                weighted_sum = sum(s * w for s, w in zip(signals_out, weights_out, strict=True))
                total_weight = sum(weights_out)
                avg_signal = weighted_sum / total_weight
            else:
                avg_signal = 0.0

            if avg_signal > 0.3:
                direction = TrendDirection.UPTREND
            elif avg_signal < -0.3:
                direction = TrendDirection.DOWNTREND
            else:
                direction = TrendDirection.SIDEWAYS

            # Confidence based on agreement and trend strength
            confidence = min(abs(avg_signal), 1.0)
            if trend_strength == TrendStrength.STRONG:
                confidence = min(confidence * 1.2, 1.0)
            elif trend_strength == TrendStrength.WEAK:
                confidence = confidence * 0.8

            raw_score = max(-100.0, min(100.0, avg_signal * 100.0))
            classification = classify_score(raw_score)
        else:
            direction = TrendDirection.UNKNOWN
            confidence = 0.0
            raw_score = 0.0
            classification = TrendClassification.NO_SIGNAL

        return direction, trend_strength, confidence, raw_score, classification

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

    def build_snapshot(
        self, timeframe: Timeframe, timestamp: datetime | None = None
    ) -> TrendSnapshot | None:
        """Build a ``TrendSnapshot`` for a given timeframe.

        Returns ``None`` if no trend signal exists yet (insufficient data).
        The snapshot's ``direction`` uses the 8-class ``TrendClassification``
        enum (Phase 6 spec); ``strength`` is normalized to 0..1 using
        ``ADX``; ``momentum`` and ``structure`` are the raw ROC and %B
        values respectively.
        """
        signal = self.get_current_trend(timeframe)
        if signal is None:
            return None

        # Pull the raw ROC and %B from the indicator instances so the
        # snapshot carries actual indicator values, not just the TrendSignal
        # summary.
        timeframe_indicators = self.indicators.get(timeframe, {})
        raw_momentum = 0.0
        raw_structure = 0.0

        for ind in timeframe_indicators.values():
            if ind.name == "ROC":
                vals = ind.get_values()
                raw_momentum = vals[-1] if vals else 0.0
            if ind.name == "Bollinger_Bands":
                bands = ind.get_bands()
                pct_b = bands.get("percent_b", [])
                raw_structure = pct_b[-1] if pct_b else 0.0

        return TrendSnapshot(
            symbol=self.symbol,
            timeframe=timeframe,
            timestamp=timestamp or datetime.now(),
            direction=signal.classification,
            score=signal.score,
            strength=strength_to_float(signal.strength),
            momentum=raw_momentum,
            structure=raw_structure,
            data_quality=signal.data_quality,
            strategy_version=_settings.trend.strategy_version,
        )

    def get_overall_trend(self) -> TrendSignal | None:
        """Get overall trend based on multiple timeframes.

        Timeframe weights are read from ``TrendSettings.timeframe_weights``
        (Principle 11 / Principle 12 — no hard-coded weights).
        """
        # Resolve string-keyed weights from settings to a Timeframe-keyed dict.
        # Unrecognised keys are silently ignored so settings can omit timeframes
        # without crashing.
        timeframe_weights: dict[Timeframe, float] = {
            Timeframe(v): w
            for v, w in _settings.trend.timeframe_weights.items()
            if v in (tf.value for tf in Timeframe)
        }

        trends = self.get_multi_timeframe_trend()
        if not trends:
            return None

        # Calculate weighted average direction
        direction_scores = []
        total_weight = 0

        for timeframe, trend in trends.items():
            weight = timeframe_weights.get(timeframe, 0.1)  # 0.1 is a safe default; overridden by settings
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
                timestamp=datetime.now(),
                score=avg_score * 100.0,
            )

        return None

# Global trend engine factory
def get_trend_engine(symbol: str) -> TrendEngine:
    """Get or create trend engine for a symbol"""
    # In a real implementation, you might cache these
    return TrendEngine(symbol)
