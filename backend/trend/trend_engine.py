"""
Trend and market structure engine
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from ..config.settings import settings as _settings
from ..engines.timeframe import (
    Timeframe,
    multi_symbol_timeframe_engine,
)
from ..indicators.base_indicator import IndicatorEngine
from ..utils.timezone import NY, UTC

logger = logging.getLogger(__name__)

# Periods below which ATR-based indicators are unreliable due to noise.
# SuperTrend with ATR(10) is too choppy on sub-5m; use a longer ATR
# for intraday timeframes.  Maps timeframe → ATR period.
_TF_ATR_PERIOD: dict[Timeframe, int] = {
    Timeframe.ONE_MINUTE: 20,
    Timeframe.TWO_MINUTE: 20,
    Timeframe.THREE_MINUTE: 20,
    Timeframe.FIVE_MINUTE: 14,
    Timeframe.FIFTEEN_MINUTE: 10,
    Timeframe.THIRTY_MINUTE: 10,
    Timeframe.ONE_HOUR: 10,
    Timeframe.FOUR_HOUR: 10,
    Timeframe.ONE_DAY: 10,
    Timeframe.ONE_WEEK: 10,
}

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


def _ensure_aware(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC.

    Naive datetimes are interpreted as **America/New_York**, not UTC — the
    DB and the provider layer both store naive NY wall time (see
    ``backend/utils/timezone``). Stamping such a value as UTC shifted every
    engine timestamp 4-5h into the past, which surfaced as a permanently
    "stuck" dashboard. Expressed in UTC here so this module's existing
    stale/duplicate/gap arithmetic is unchanged.
    """
    if dt is None:
        raise ValueError("timestamp must not be None")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY)
    return dt.astimezone(UTC)

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
            multi_symbol_timeframe_engine.update_tick(symbol, 0, 0, datetime.now(timezone.utc))
            self.timeframe_engine = multi_symbol_timeframe_engine.get_engine_for_symbol(symbol)

        # Per-Phase 6.1 fix: reset per-TF candle-open tracking so a new
        # TrendEngine (e.g. the second MTF engine in a test) doesn't inherit
        # stale _last_candle_open values from the previous engine. Candle data
        # is preserved; only the per-TF boundary tracker is cleared.
        for tf in Timeframe:
            self.timeframe_engine._last_candle_open[tf] = None

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

        Per-timeframe ATR period overrides (Phase 6.1 — see ``_TF_ATR_PERIOD``)
        propagate to SuperTrend so short intraday timeframes use a longer ATR
        and don't whipsaw.
        """
        defaults = _settings.trend.indicators
        for timeframe, (ema_fast, ema_slow) in _TIMEFRAME_EMA.items():
            stack = IndicatorEngine.build_timeframe_stack(ema_fast, ema_slow, defaults)
            # Phase 6.1: tune the SuperTrend ATR period per timeframe.
            st = stack.get("supertrend")
            if st is not None:
                st_period = _TF_ATR_PERIOD.get(timeframe, defaults.supertrend_atr_period)
                # Rebuild the SuperTrend with the right ATR period so the
                # underlying ATRIndicator also uses the tuned period.
                stack["supertrend"] = IndicatorEngine.create_indicator(
                    "supertrend",
                    {
                        "atr_period": st_period,
                        "multiplier": defaults.supertrend_multiplier,
                    },
                )
            # Phase 6.1: add a standalone ATR indicator so MACD and ROC can
            # normalise their signals by the current volatility regime.
            # (SuperTrend has its own internal ATR; this one is for scoring.)
            stack["atr"] = IndicatorEngine.create_indicator(
                "atr", {"period": _TF_ATR_PERIOD.get(timeframe, defaults.adx_period)},
            )
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
               timestamp: datetime, provider: str = "", **_: object):
        """Update trend engine with new market data.

        Phase 0 Principle 15 — data quality validated before analysis.
        Normalizes ``timestamp`` to timezone-aware UTC so all internal comparisons
        (stale, gap, duplicate checks) work consistently even when callers pass
        naive datetimes.
        """
        timestamp = _ensure_aware(timestamp)
        # Phase 0 Principle 15 — data quality validated before analysis.
        # Before feeding the tick to the timeframe/indicator stack we run
        # three cheap checks: stale (timestamp is older than the configured
        # threshold), duplicate (price matches the previous tick within
        # tolerance), and gap (more wall-clock time has passed than the
        # configured max gap). Each violation is logged so callers can
        # observe data quality without inspecting internals.
        #
        # Indicators are fed ONLY from closed candles (real OHLCV), not from
        # the live-tick synthesis. This ensures SuperTrend, ADX, and Bollinger
        # all compute from true price ranges rather than flat single-price bars.
        dq = _settings.data_quality
        now = datetime.now(timezone.utc)

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
        if self._last_update_time is not None and timestamp is not None:
            incoming_gap = (timestamp - self._last_update_time).total_seconds()
            if incoming_gap > dq.max_tick_gap_seconds:
                logger.warning(
                    "Data-quality [gap]: %s incoming tick is %.1fs after "
                    "previous (threshold %.1fs)",
                    self.symbol, incoming_gap, dq.max_tick_gap_seconds,
                )

        # Update the timeframe engine with new tick (accumulates into candles).
        self.timeframe_engine.update_tick(price, volume, timestamp, provider)

        # Feed indicators ONLY from closed candles (real OHLCV). For the shortest
        # active timeframe (typically 1m), we update on every tick so the signal
        # is near-real-time. For longer TFs, we only feed when the candle closes.
        self._update_indicators_from_candles(timestamp)

        # Generate trend signals from the updated indicators.
        self._generate_trend_signals(timestamp)

        # Record state for the next update() call.
        self._last_update_time = now
        self._last_price = price

    def _update_indicators_from_candles(self, timestamp: datetime):
        """Feed indicators from the timeframe engine's real OHLCV candles.

        Each timeframe is updated at its own cadence:
          - Short TFs (1m/2m/3m): update on every tick so the signal is
            near-real-time; use the in-progress candle's accumulated OHLC.
          - Longer TFs (5m+): prefer the in-progress candle's accumulated
            OHLC so all ticks contribute to the trend signal during the candle's
            lifetime. Fall back to the last closed candle when available; this
            handles cases where a candle has just closed and the in-progress one
            is brand-new.

        This is the Phase 6.1 fix that replaced the previous
        ``_update_indicators`` path, which passed ``open=high=low=close=price``
        on every tick — collapsing SuperTrend bands, ADX, and Bollinger
        variance to near-zero.
        """
        short_tfs = {
            Timeframe.ONE_MINUTE,
            Timeframe.TWO_MINUTE,
            Timeframe.THREE_MINUTE,
        }

        for timeframe, indicators in self.indicators.items():
            if timeframe == Timeframe.TICK:
                continue

            closed_candles = self.timeframe_engine.get_closed_candles(timeframe)
            current_candle = self.timeframe_engine.current_candles.get(timeframe)

            # Short TFs: always update using the in-progress candle.
            # Other TFs: prefer the in-progress candle (most recent data). Fall
            # back to the last closed candle only when there is no in-progress
            # candle (e.g. a very-long-period TF whose candle hasn't opened yet).
            if timeframe in short_tfs:
                candle = current_candle
            elif current_candle is not None:
                candle = current_candle
            elif closed_candles:
                candle = closed_candles[-1]
            else:
                continue

            if candle is None or candle.open is None:
                continue

            data_point = {
                "open": float(candle.open),
                "high": float(candle.high),
                "low": float(candle.low),
                "close": float(candle.close),
                "volume": float(candle.volume),
            }

            for name, indicator in indicators.items():
                try:
                    indicator.update(data_point)
                except Exception as e:
                    logger.debug(
                        f"Error updating {name} indicator for {timeframe}: {e}"
                    )

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

        Phase 6.1: all components are continuous signals, not binary.
        Key improvements over Phase 6:
          - RSI: continuous signal (RSI - 50) / 50, so RSI 60 → +0.2
          - MACD: uses histogram normalized by ATR, not bare MACD sign
          - ADX: DI+/DI- gives the actual direction; ADX controls strength
          - ROC: continuous, normalized by ATR
          - Direction threshold is adaptive: tighten when indicators agree
          - Bollinger Bands: enabled (was zero-weight in Phase 6)

        Returns a 5-tuple:
            (direction, strength, confidence, score, classification)
        ``score`` is the raw weighted directional signal in -100..+100.
        ``classification`` maps ``score`` to the 8-class enum.
        """
        weights_cfg = _settings.trend.signal_weights

        components: list[tuple[float, float]] = []
        trend_strength = TrendStrength.MODERATE
        adx_value: float | None = None
        atr_value: float | None = None

        # --- Component 1: EMA crossover ---
        # Use the slope of the EMA spread as well as the sign.
        ema_fast = indicator_values.get("ema_fast")
        ema_slow = indicator_values.get("ema_slow")
        if ema_fast is not None and ema_slow is not None:
            spread = ema_fast - ema_slow
            # Spread sign is the base signal; magnitude normalises the weight.
            spread_signal = max(-1.0, min(1.0, spread / (ema_slow * 0.01 + 1e-9)))
            components.append((spread_signal, weights_cfg.ema))

        # --- Component 2: RSI continuous ---
        # RSI 50 = neutral, RSI 100 = max bullish, RSI 0 = max bearish.
        # Map to [-1, +1] around the 50 midpoint.
        rsi = indicator_values.get("rsi")
        if rsi is not None:
            rsi_signal = max(-1.0, min(1.0, (rsi - 50) / 50))
            components.append((rsi_signal, weights_cfg.rsi))

        # --- Component 3: MACD histogram continuous ---
        # The MACD indicator stores the histogram (MACD - signal) as its main
        # value. Normalise by ATR so the score is scale-independent.
        macd = indicator_values.get("macd")
        if macd is not None:
            # Get ATR for normalisation (look it up from the ATR indicator).
            atr_ind = next(
                (ind for ind in self.indicators.get(timeframe, {}).values()
                 if getattr(ind, "name", "") == "ATR"),
                None,
            )
            if atr_ind is not None:
                atr_vals = atr_ind.get_values()
                atr_value = atr_vals[-1] if atr_vals else None
            # Normalised histogram: divide by ATR (price-related scale).
            # Falls back to raw sign if ATR unavailable.
            if macd != 0.0 and atr_value is not None and atr_value > 0:
                norm = (macd / atr_value) / 10.0  # /10 so ±2-3 hist ≈ ±0.2-0.3 signal
                macd_signal = max(-1.0, min(1.0, norm))
            else:
                macd_signal = 1.0 if macd > 0 else -1.0
            components.append((macd_signal, weights_cfg.macd))

        # --- Component 4: ADX strength + DI+/DI- direction ---
        # Pull DI+/DI- and ADX from the ADX indicator instance.
        adx_ind = next(
            (ind for ind in self.indicators.get(timeframe, {}).values()
             if getattr(ind, "name", "") == "ADX"),
            None,
        )
        if adx_ind is not None:
            di_plus_vals = getattr(adx_ind, "_di_plus", None)
            di_minus_vals = getattr(adx_ind, "_di_minus", None)
            if di_plus_vals is not None and di_minus_vals is not None:
                di_sum = di_plus_vals + di_minus_vals
                if di_sum > 0:
                    # DI+ > DI- → bullish directional pressure.
                    # Normalise by the sum so a +30 DI+/−10 DI- bar yields
                    # (30−10)/(30+10) = +0.5 (strong bullish).
                    di_signal = (di_plus_vals - di_minus_vals) / di_sum
                    di_signal = max(-1.0, min(1.0, di_signal))
                    components.append((di_signal, weights_cfg.adx))
            # ADX itself controls strength (not direction).
            adx_v = indicator_values.get("adx")
            if adx_v is not None:
                adx_value = adx_v
                if adx_v > 40:
                    trend_strength = TrendStrength.STRONG
                elif adx_v > 25:
                    trend_strength = TrendStrength.MODERATE
                elif adx_v > 15:
                    trend_strength = TrendStrength.WEAK
                else:
                    trend_strength = TrendStrength.WEAK

        # --- Component 5: SuperTrend direction ---
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
        # %B ∈ [0,1]: deviation from 0.5 gives a continuous signal.
        bb = indicator_values.get("bollinger_bands")
        if bb is not None:
            # Scale to [-1, +1]: %B=1.0 → +1, %B=0.0 → -1
            bb_signal = max(-1.0, min(1.0, (bb - 0.5) * 2))
            components.append((bb_signal, weights_cfg.bollinger))

        # --- Component 7: Volume confirmation ---
        rel_vol = indicator_values.get("relative_volume")
        if rel_vol is not None and rel_vol > 0:
            # Map to a smooth signal: > 2× avg → bullish, < 0.5× avg → bearish.
            vol_signal = max(-1.0, min(1.0, (rel_vol - 1.0) * 2))
            components.append((vol_signal, weights_cfg.relative_volume))

        # --- Component 8: Momentum (ROC) continuous ---
        # Normalise ROC by ATR so the signal is scale-independent.
        roc = indicator_values.get("roc")
        if roc is not None:
            if roc != 0.0 and atr_value is not None and atr_value > 0:
                # ROC is in percent; ATR is in price units.
                # Normalise: e.g. 2% ROC / (ATR/close*100) ≈ momentum in ATR units.
                # Simplified: cap ROC signal at ±1.0 directly.
                roc_signal = max(-1.0, min(1.0, roc / 5.0))
            else:
                roc_signal = 1.0 if roc > 0 else -1.0
            components.append((roc_signal, weights_cfg.momentum))

        # --- Combine ---
        if components:
            active = [(s, w) for s, w in components if w > 0]
            if active:
                signals_out = [s for s, _ in active]
                weights_out = [w for _, w in active]
                weighted_sum = sum(s * w for s, w in zip(signals_out, weights_out, strict=True))
                total_weight = sum(weights_out)
                avg_signal = weighted_sum / total_weight
            else:
                avg_signal = 0.0

            # Adaptive threshold: tighten when ADX shows a strong trend.
            # Weak trend (ADX < 20) → need stronger consensus (±0.35).
            # Strong trend (ADX > 35) → smaller threshold (±0.15) since
            # multiple indicators are confirming.
            if adx_value is not None and adx_value > 35:
                threshold = 0.15
            elif adx_value is not None and adx_value < 20:
                threshold = 0.35
            else:
                threshold = 0.25

            if avg_signal > threshold:
                direction = TrendDirection.UPTREND
            elif avg_signal < -threshold:
                direction = TrendDirection.DOWNTREND
            else:
                direction = TrendDirection.SIDEWAYS

            # Confidence: base on average signal magnitude, modulated by ADX.
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
            timestamp=timestamp or datetime.now(timezone.utc),
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
                timestamp=datetime.now(timezone.utc),
                score=avg_score * 100.0,
            )

        return None

# Global trend engine factory
def get_trend_engine(symbol: str) -> TrendEngine:
    """Get or create trend engine for a symbol"""
    # In a real implementation, you might cache these
    return TrendEngine(symbol)
