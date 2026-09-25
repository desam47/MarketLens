"""
Trend and market structure engine
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..config.settings import settings as _settings
from ..engines.timeframe import (
    Timeframe,
    multi_symbol_timeframe_engine,
)
from ..indicators.base_indicator import BaseIndicator, IndicatorEngine
from ..utils.timezone import NY, ny_to_utc

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

# A single fixed SuperTrend multiplier (band width) for every timeframe
# under-serves both ends: short/noisy timeframes still whipsaw even with a
# longer ATR period, while long timeframes hug price too loosely and lag
# real reversals. Widen the band on short timeframes (fewer false flips)
# and tighten it on long timeframes (faster reaction) instead of one
# constant. ``ONE_DAY`` keeps the settings-configured default since it is
# the most-used/most-tested timeframe and the existing default already
# works reasonably there.
_TF_ST_MULTIPLIER: dict[Timeframe, float] = {
    Timeframe.ONE_MINUTE: 4.0,
    Timeframe.TWO_MINUTE: 4.0,
    Timeframe.THREE_MINUTE: 3.5,
    Timeframe.FIVE_MINUTE: 3.5,
    Timeframe.FIFTEEN_MINUTE: 3.0,
    Timeframe.THIRTY_MINUTE: 3.0,
    Timeframe.ONE_HOUR: 3.0,
    Timeframe.FOUR_HOUR: 2.75,
    Timeframe.ONE_WEEK: 2.5,
}

# Require N consecutive closes beyond the active band before accepting a
# trend flip (see ``SuperTrendIndicator.confirmation``). Only worth paying
# the extra lag for on the noisiest short timeframes — longer timeframes
# already lag from the tighter multiplier above and don't need it.
_TF_ST_CONFIRMATION: dict[Timeframe, int] = {
    Timeframe.ONE_MINUTE: 2,
    Timeframe.TWO_MINUTE: 2,
    Timeframe.THREE_MINUTE: 1,
    Timeframe.FIVE_MINUTE: 1,
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

_SHORT_HORIZON_TIMEFRAMES = frozenset(
    {Timeframe.ONE_MINUTE, Timeframe.TWO_MINUTE, Timeframe.THREE_MINUTE}
)
_SHORT_MOMENTUM_BAR_COUNT = 4


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
    return ny_to_utc(dt)


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


class ShortHorizonMomentum(StrEnum):
    """Measured short-horizon momentum states, distinct from ADX strength."""

    CHOPPY = "choppy"
    DEVELOPING = "developing"
    PERSISTENT = "persistent"


@dataclass(frozen=True)
class TrendScoringProfile:
    """The indicator stack behind a timeframe's directional score.

    Scores and agreement are intentionally *not* declared comparable across
    these profiles.  Calibration needs outcome research by timeframe and
    session; until that exists, consumers may use a profile to explain a
    signal but must not treat equal raw numbers as equal conviction.
    """

    id: str
    label: str
    components: tuple[str, ...]

    @property
    def component_count(self) -> int:
        return len(self.components)

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "components": list(self.components),
            "confidence_semantics": "weighted_indicator_agreement",
            "score_comparable_across_timeframes": False,
            "calibration_state": "profile_specific_not_calibrated",
        }


_DIRECTIONAL_CORE_PROFILE = TrendScoringProfile(
    id="directional_core",
    label="Directional core",
    components=("EMA", "RSI", "MACD"),
)
_INTRADAY_DIRECTIONAL_PROFILE = TrendScoringProfile(
    id="intraday_directional",
    label="Directional + ADX",
    components=("EMA", "RSI", "MACD", "ADX/DI"),
)
_FULL_TECHNICAL_PROFILE = TrendScoringProfile(
    id="full_technical",
    label="Full technical stack",
    components=("EMA", "RSI", "MACD", "ADX/DI", "SuperTrend", "Bollinger", "Relative volume", "ROC"),
)


def scoring_profile_for_timeframe(timeframe: Timeframe) -> TrendScoringProfile:
    """Return the declared scoring profile for one Trend timeframe.

    Under TREND_SIGNAL_V2, fast timeframes (1m/2m/3m/5m) gain SuperTrend and
    ADX so they use the intraday directional profile rather than the minimal
    directional-core profile.
    """
    if timeframe in _SHORT_HORIZON_TIMEFRAMES:
        if _settings.trend.signal_v2:
            return _INTRADAY_DIRECTIONAL_PROFILE
        return _DIRECTIONAL_CORE_PROFILE
    if timeframe == Timeframe.FIVE_MINUTE:
        return _INTRADAY_DIRECTIONAL_PROFILE
    return _FULL_TECHNICAL_PROFILE


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

    def __init__(
        self,
        symbol: str,
        timeframe: Timeframe,
        direction: TrendDirection,
        strength: TrendStrength,
        confidence: float,
        timestamp: datetime,
        score: float | None = None,
        classification: TrendClassification | None = None,
        data_quality: str = "ok",
        short_horizon_momentum: ShortHorizonMomentum | None = None,
        short_horizon_momentum_score: float | None = None,
        attribution: list[dict[str, Any]] | None = None,
        key_levels: dict[str, Any] | None = None,
        maturity: str | None = None,
        stop_distance_atr: float | None = None,
        htf_bias: dict[str, Any] | None = None,
        adx_slope: str | None = None,
    ):
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
            classification if classification is not None else classify_score(self.score)
        )
        # Phase 6: data quality stamp. Defaults to "ok" so existing callers
        # are unaffected; set by the engine when stale/duplicate/gap is hit.
        self.data_quality = data_quality
        # ADX is intentionally absent on 1m-3m.  Their trader-facing card
        # uses this separate, closed-bar momentum measure instead of claiming
        # the default ADX-style ``strength`` is a measurement.
        self.short_horizon_momentum = short_horizon_momentum
        self.short_horizon_momentum_score = short_horizon_momentum_score
        # TC-09: per-component contributions to this score (name/signal/weight/
        # contribution), and available indicator price levels (SuperTrend flip,
        # Bollinger edges). Both are optional and default empty for callers that
        # construct a signal without the engine's scoring internals.
        self.attribution: list[dict[str, Any]] = attribution or []
        self.key_levels: dict[str, Any] = key_levels or {}
        # E5: trend maturity label (Fresh/Developing/Healthy/Extended) derived
        # from SuperTrend band_distance_atr. Tells how far price sits from the
        # stop and whether the move is fresh or extended.
        self.maturity: str | None = maturity
        self.stop_distance_atr: float | None = stop_distance_atr
        # E3: ADX slope label — 'strengthening', 'fading', or 'flat'.
        # Derived from a rolling 8-bar ADX buffer; None when ADX isn't in the stack.
        self.adx_slope: str | None = adx_slope
        # E4: compact higher-timeframe bias tag so each card is actionable
        # standalone — e.g. {"timeframe": "1h", "direction": "uptrend", "score": 25.1}
        self.htf_bias: dict[str, Any] | None = htf_bias
        self.indicators: dict[str, Any] = {}

    def __repr__(self):
        return (
            f"TrendSignal({self.symbol} {self.timeframe.value} "
            f"{self.direction.value} {self.classification.value} "
            f"conf:{self.confidence:.2f})"
        )


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
    direction: TrendClassification  # 8-class enum
    score: float  # -100..+100
    strength: float  # 0.0..1.0
    momentum: float  # ROC value (%)
    structure: float  # Bollinger %B value
    data_quality: str  # "ok" | "stale" | "duplicate" | "gap"
    strategy_version: str  # from TrendSettings.strategy_version


class TrendEngine:
    """Engine for determining market trend across multiple timeframes"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.timeframe_engine = multi_symbol_timeframe_engine.get_engine_for_symbol(symbol)
        if self.timeframe_engine is None:
            # Create engine if it doesn't exist
            multi_symbol_timeframe_engine.update_tick(symbol, 0, 0, datetime.now(UTC))
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

        # Tracks the close_time of the last CLOSED candle already fed to
        # each short-TF's indicators (see _update_indicators_from_candles),
        # so a still-forming candle contributes exactly one indicator
        # update once it closes, not once per tick.
        self._last_fed_closed_ts: dict[Timeframe, datetime] = {}

        # Production bar ingestion is timeframe-specific. These maps prevent
        # a stored 1d bar from warming 1m indicators (and vice versa), retain
        # provenance for the API, and aggregate completed 1m bars into the
        # higher intraday timeframes without repeatedly appending the same
        # still-forming candle to stateful indicators.
        self._last_processed_bar_ts: dict[Timeframe, datetime] = {}
        self._bar_counts: dict[Timeframe, int] = {}
        self._bar_metadata: dict[Timeframe, dict[str, Any]] = {}
        self._live_aggregates: dict[Timeframe, dict[str, Any]] = {}
        # TC-09 change history: one entry per direction RUN (not per bar and not
        # per polling update), keyed by the closed-bar index at which that run
        # began. Counting closed bars (``_bar_counts``) — not ``trend_history``
        # entries, which grow on every tick-frequency ``update()`` — is what
        # keeps "held for N bars" honest.
        self._trend_state_runs: dict[Timeframe, list[dict[str, Any]]] = {}
        self._short_momentum_bars: dict[Timeframe, deque[dict[str, float]]] = {
            timeframe: deque(maxlen=_SHORT_MOMENTUM_BAR_COUNT)
            for timeframe in _SHORT_HORIZON_TIMEFRAMES
        }
        # E3: rolling ADX buffer — last 8 closed-bar ADX readings per timeframe.
        # Used to derive "strengthening" vs "fading" for the card.
        self._adx_history: dict[Timeframe, deque[float]] = {}

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
            # Tune the SuperTrend ATR period, band multiplier, and flip
            # confirmation per timeframe (see the maps above) so short
            # timeframes don't whipsaw and long timeframes don't lag behind
            # a single one-size-fits-all multiplier.
            st = stack.get("supertrend")
            if st is not None:
                st_period = _TF_ATR_PERIOD.get(timeframe, defaults.supertrend_atr_period)
                st_multiplier = _TF_ST_MULTIPLIER.get(timeframe, defaults.supertrend_multiplier)
                st_confirmation = _TF_ST_CONFIRMATION.get(timeframe, 0)
                # Rebuild the SuperTrend with the tuned params so the
                # underlying ATRIndicator also uses the tuned period.
                stack["supertrend"] = IndicatorEngine.create_indicator(
                    "supertrend",
                    {
                        "atr_period": st_period,
                        "multiplier": st_multiplier,
                        "confirmation": st_confirmation,
                    },
                )
            # Phase 6.1: add a standalone ATR indicator so MACD and ROC can
            # normalise their signals by the current volatility regime.
            # (SuperTrend has its own internal ATR; this one is for scoring.)
            stack["atr"] = IndicatorEngine.create_indicator(
                "atr",
                {"period": _TF_ATR_PERIOD.get(timeframe, defaults.adx_period)},
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
                    "relative_volume",
                    {"period": defaults.bollinger_period},
                )
                stack["roc"] = IndicatorEngine.create_indicator(
                    "roc",
                    {"period": 12},
                )
            # Sub-5m timeframes get a minimal set — no ADX, no supertrend,
            # no bollinger. 2m/3m follow the 1m pattern. ATR is retained for
            # the separate, explicitly named short-horizon momentum measure.
            # TREND_SIGNAL_V2: expand the fast-TF stacks so the direction gate
            # (SuperTrend) and regime gate (ADX) have data to work with.
            if timeframe in _SHORT_HORIZON_TIMEFRAMES:
                if _settings.trend.signal_v2:
                    # V2 full fast-TF stack: direction gate (ST), regime gate (ADX),
                    # conviction (MACD+EMA+RSI), volume (E1), momentum (E1).
                    self.indicators[timeframe] = {
                        k: v
                        for k, v in stack.items()
                        if k in (
                            "ema_fast", "ema_slow", "rsi", "macd", "atr",
                            "supertrend", "adx", "relative_volume", "roc",
                        )
                    }
                else:
                    self.indicators[timeframe] = {
                        k: v
                        for k, v in stack.items()
                        if k in ("ema_fast", "ema_slow", "rsi", "macd", "atr")
                    }
            elif timeframe == Timeframe.FIVE_MINUTE:
                if _settings.trend.signal_v2:
                    # V2: ADX already in 5m stack; add ST + ATR + volume + momentum (E1)
                    self.indicators[timeframe] = {
                        k: v
                        for k, v in stack.items()
                        if k in (
                            "ema_fast", "ema_slow", "rsi", "macd", "adx",
                            "supertrend", "atr", "relative_volume", "roc",
                        )
                    }
                else:
                    self.indicators[timeframe] = {
                        k: v
                        for k, v in stack.items()
                        if k in ("ema_fast", "ema_slow", "rsi", "macd", "adx")
                    }
            else:
                self.indicators[timeframe] = stack

    def update(
        self,
        price: float,
        volume: float,
        timestamp: datetime,
        provider: str = "",
        only_timeframe: Timeframe | None = None,
        timeframe: str | Timeframe | None = None,
        high: float | None = None,
        low: float | None = None,
        open_price: float | None = None,
        data_status: object | None = None,
        session: str | None = None,
        **_: object,
    ):
        """Update trend engine with new market data.

        Phase 0 Principle 15 — data quality validated before analysis.
        Normalizes ``timestamp`` to timezone-aware UTC so all internal comparisons
        (stale, gap, duplicate checks) work consistently even when callers pass
        naive datetimes.

        ``only_timeframe`` is a seeder-only kwarg: when set, only the trend
        signal for that timeframe is generated. Live ingestion never sets it.
        Per-tf seeding in ``backend.api.trend.registry`` sets it so feeding 1h
        bars doesn't pollute ``trend_history[1m]`` with 1h timestamps (which
        would otherwise mask the real recent 1m signals in the last-100 cap).
        """
        timestamp = _ensure_aware(timestamp)

        # Bar dispatches carry their source timeframe. Feed the exact OHLCV
        # into only that indicator stack; the legacy tick path below remains
        # for direct callers/tests that intentionally provide no timeframe.
        direct_tf = timeframe or only_timeframe
        if direct_tf is not None:
            try:
                tf = direct_tf if isinstance(direct_tf, Timeframe) else Timeframe(direct_tf)
            except ValueError:
                logger.warning("Ignoring unsupported trend timeframe %r for %s", direct_tf, self.symbol)
                return
            self._update_from_bar(
                tf,
                open_price=price if open_price is None else open_price,
                high=price if high is None else high,
                low=price if low is None else low,
                close=price,
                volume=volume,
                timestamp=timestamp,
                provider=provider,
                data_status=data_status,
                session=session,
                aggregate_live_1m=only_timeframe is None,
            )
            return
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
        now = datetime.now(UTC)

        # --- Stale check -------------------------------------------------
        if self._last_update_time is not None:
            age = (now - self._last_update_time).total_seconds()
            if age > dq.stale_threshold_seconds:
                logger.warning(
                    "Data-quality [stale]: %s last tick was %.1fs ago (threshold %.1fs)",
                    self.symbol,
                    age,
                    dq.stale_threshold_seconds,
                )

        # --- Duplicate check --------------------------------------------
        if self._last_price is not None:
            if abs(price - self._last_price) <= dq.duplicate_price_tolerance:
                logger.debug(
                    "Data-quality [duplicate]: %s price %s == last %s",
                    self.symbol,
                    price,
                    self._last_price,
                )

        # --- Gap check --------------------------------------------------
        if self._last_update_time is not None and timestamp is not None:
            incoming_gap = (timestamp - self._last_update_time).total_seconds()
            if incoming_gap > dq.max_tick_gap_seconds:
                logger.warning(
                    "Data-quality [gap]: %s incoming tick is %.1fs after "
                    "previous (threshold %.1fs)",
                    self.symbol,
                    incoming_gap,
                    dq.max_tick_gap_seconds,
                )

        # Update the timeframe engine with new tick (accumulates into candles).
        self.timeframe_engine.update_tick(price, volume, timestamp, provider)

        # Feed indicators ONLY from closed candles (real OHLCV). For the shortest
        # active timeframe (typically 1m), we update on every tick so the signal
        # is near-real-time. For longer TFs, we only feed when the candle closes.
        self._update_indicators_from_candles(timestamp)

        # Generate trend signals from the updated indicators.
        self._generate_trend_signals(timestamp, only_timeframe=only_timeframe)

        # Record state for the next update() call.
        self._last_update_time = now
        self._last_price = price

    @staticmethod
    def _status_value(value: object | None) -> str:
        if value is None:
            return "ok"
        raw = getattr(value, "value", value)
        normalized = str(raw).strip().lower()
        return "ok" if normalized in {"historical", "live", "ok"} else normalized

    @staticmethod
    def _bucket_start(timestamp: datetime, timeframe: Timeframe) -> datetime:
        """Return an ET-aligned bucket start as aware UTC."""
        local = timestamp.astimezone(NY)
        if timeframe == Timeframe.TWO_MINUTE:
            local = local.replace(minute=local.minute - local.minute % 2, second=0, microsecond=0)
        elif timeframe == Timeframe.THREE_MINUTE:
            local = local.replace(minute=local.minute - local.minute % 3, second=0, microsecond=0)
        elif timeframe == Timeframe.FIVE_MINUTE:
            local = local.replace(minute=local.minute - local.minute % 5, second=0, microsecond=0)
        elif timeframe == Timeframe.FIFTEEN_MINUTE:
            local = local.replace(minute=local.minute - local.minute % 15, second=0, microsecond=0)
        elif timeframe == Timeframe.THIRTY_MINUTE:
            local = local.replace(minute=local.minute - local.minute % 30, second=0, microsecond=0)
        elif timeframe == Timeframe.ONE_HOUR:
            local = local.replace(minute=0, second=0, microsecond=0)
        elif timeframe == Timeframe.FOUR_HOUR:
            local = local.replace(hour=local.hour - local.hour % 4, minute=0, second=0, microsecond=0)
        return local.astimezone(UTC)

    def _invalidate_output_caches(self, timeframe: Timeframe) -> None:
        """Invalidate REST snapshots immediately after the engine advances."""
        try:
            from backend.api.ttl_cache import _confluence_cache, _strategy_cache, _trend_cache

            _trend_cache.pop(f"{self.symbol.upper()}:{timeframe.value}", None)
            _strategy_cache.pop(self.symbol.upper(), None)
            prefix = f"{self.symbol.upper()}:"
            for key in list(_confluence_cache):
                if key.startswith(prefix):
                    _confluence_cache.pop(key, None)
        except Exception:  # pragma: no cover - cache is optional during isolated engine use
            pass

    def _update_from_bar(
        self,
        timeframe: Timeframe,
        *,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        timestamp: datetime,
        provider: str,
        data_status: object | None,
        session: str | None,
        aggregate_live_1m: bool,
    ) -> None:
        last = self._last_processed_bar_ts.get(timeframe)
        if last is not None and timestamp <= last:
            return

        point = {
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
        }
        indicators = self.indicators.get(timeframe, {})
        for name, indicator in indicators.items():
            try:
                indicator.update(point)
            except Exception as exc:
                logger.debug("Error updating %s for %s: %s", name, timeframe.value, exc)

        quality = self._status_value(data_status)
        if timeframe in _SHORT_HORIZON_TIMEFRAMES and quality == "ok":
            # Only completed, trustworthy source bars contribute. Forming,
            # delayed, duplicate, and gap bars must not manufacture a
            # persistence reading.
            self._short_momentum_bars[timeframe].append(point)
        # Keep signal emission on the engine's established generation path.
        # Besides avoiding two subtly different scoring implementations, this
        # preserves replay/backtest instrumentation that intentionally wraps
        # _generate_trend_signals to detect an unscorable individual bar.
        self._generate_trend_signals(timestamp, only_timeframe=timeframe)
        signal = self.get_current_trend(timeframe)
        if signal is not None and signal.timestamp == timestamp:
            signal.data_quality = quality

        self._last_processed_bar_ts[timeframe] = timestamp
        self._bar_counts[timeframe] = self._bar_counts.get(timeframe, 0) + 1
        if signal is not None:
            self._record_trend_state(timeframe, signal.direction.value, timestamp)
        self._bar_metadata[timeframe] = {
            "provider": provider or "unknown",
            "session": session or "unknown",
            "data_status": quality,
            "bar_closed": quality != "incomplete",
            "timestamp": timestamp,
        }
        self._last_update_time = datetime.now(UTC)
        self._last_price = close
        self._invalidate_output_caches(timeframe)

        if timeframe == Timeframe.ONE_MINUTE and aggregate_live_1m:
            self._aggregate_live_bar(point, timestamp, provider, quality, session)

    @staticmethod
    def _merged_provider(existing: str | None, incoming: str | None) -> str:
        """Keep aggregate provenance truthful when source providers change.

        A resampled candle represents more than one source bar. Once sources
        disagree (including a missing provider), returning the newest source
        would falsely imply the whole candle came from it, so expose ``mixed``.
        """
        current = (existing or "unknown").strip() or "unknown"
        candidate = (incoming or "unknown").strip() or "unknown"
        return current if current == candidate else "mixed"

    def _aggregate_live_bar(
        self,
        point: dict[str, float],
        timestamp: datetime,
        provider: str,
        data_status: str,
        session: str | None,
    ) -> None:
        """Aggregate exact closed 1m bars and feed each higher TF once at close."""
        targets = (
            Timeframe.TWO_MINUTE,
            Timeframe.THREE_MINUTE,
            Timeframe.FIVE_MINUTE,
            Timeframe.FIFTEEN_MINUTE,
            Timeframe.THIRTY_MINUTE,
            Timeframe.ONE_HOUR,
            Timeframe.FOUR_HOUR,
        )
        for target in targets:
            start = self._bucket_start(timestamp, target)
            current = self._live_aggregates.get(target)
            if current is not None and start > current["start"]:
                self._update_from_bar(
                    target,
                    open_price=current["open"],
                    high=current["high"],
                    low=current["low"],
                    close=current["close"],
                    volume=current["volume"],
                    timestamp=current["start"],
                    provider=current["provider"],
                    data_status=current["data_status"],
                    session=current["session"],
                    aggregate_live_1m=False,
                )
                current = None
            if current is None:
                # After a mid-bucket process restart, waiting for the next
                # aligned boundary is safer than manufacturing a partial
                # higher-timeframe candle and permanently feeding it to a
                # stateful indicator. The DB resampler still supplies the
                # exact completed bucket during this short hand-off window.
                if timestamp != start:
                    continue
                self._live_aggregates[target] = {
                    "start": start,
                    "open": point["open"],
                    "high": point["high"],
                    "low": point["low"],
                    "close": point["close"],
                    "volume": point["volume"],
                    "provider": provider or "unknown",
                    "data_status": data_status,
                    "session": session or "unknown",
                }
                continue
            if start < current["start"]:
                continue
            current["high"] = max(current["high"], point["high"])
            current["low"] = min(current["low"], point["low"])
            current["close"] = point["close"]
            current["volume"] += point["volume"]
            current["provider"] = self._merged_provider(current.get("provider"), provider)
            if session and current["session"] != session:
                current["session"] = "mixed"
            if current["data_status"] == "ok" and data_status != "ok":
                current["data_status"] = data_status

    def get_timeframe_metadata(self, timeframe: Timeframe) -> dict[str, Any]:
        return dict(self._bar_metadata.get(timeframe, {}))

    def get_bar_count(self, timeframe: Timeframe) -> int:
        return self._bar_counts.get(timeframe, 0)

    def _update_indicators_from_candles(self, timestamp: datetime):
        """Feed indicators from the timeframe engine's real OHLCV candles.

        Each timeframe is updated at its own cadence:
          - Short TFs (1m/2m/3m): update once per CLOSED candle, not on
            every tick. These feed the "1m" (etc.) cell on the
            Trend by Timeframe panel, which users compare directly
            against the 1m bar chart — that chart only ever shows closed
            candles. Feeding the in-progress candle instead meant two
            problems: the signal could visually contradict the chart
            (reacting to a still-forming candle the chart doesn't show
            yet), and — since each indicator's update() unconditionally
            appends a new data point (see EMAIndicator.update) — a single
            volatile minute with many ticks fed the *same* forming candle
            dozens of times, each treated as a distinct new bar and
            massively over-weighting that minute relative to real history.
            Confirmed live 2026-09-16: DVLT's 1m cell flipped between
            "downtrend" and "sideways" within two minutes while every
            closed 1m bar in that window was higher than the last.
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

            if timeframe in short_tfs:
                candle = self.timeframe_engine.get_latest_closed_candle(timeframe)
                if candle is None or candle.open is None:
                    continue
                # Already fed this candle on an earlier tick this same
                # (still-forming next) minute — skip so each closed candle
                # contributes exactly one indicator update.
                if self._last_fed_closed_ts.get(timeframe) == candle.close_time:
                    continue
                self._last_fed_closed_ts[timeframe] = candle.close_time
            else:
                closed_candles = self.timeframe_engine.get_closed_candles(timeframe)
                current_candle = self.timeframe_engine.current_candles.get(timeframe)
                # Prefer the in-progress candle (most recent data). Fall back
                # to the last closed candle only when there is no in-progress
                # candle (e.g. a very-long-period TF whose candle hasn't
                # opened yet).
                if current_candle is not None:
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
                    logger.debug(f"Error updating {name} indicator for {timeframe}: {e}")

    def _generate_trend_signals(self, timestamp: datetime, only_timeframe: Timeframe | None = None):
        """Generate trend signals for each timeframe.

        ``only_timeframe`` restricts signal generation to a single timeframe —
        used by per-timeframe seeding in ``backend.api.trend.registry`` so
        that 1h seeding doesn't pollute ``trend_history[1m]`` with 1h
        timestamps. Live ingestion passes ``None`` (all timeframes).
        """
        for timeframe, indicators in self.indicators.items():
            if timeframe == Timeframe.TICK:
                continue  # Skip tick timeframe for trend analysis
            if only_timeframe is not None and timeframe != only_timeframe:
                continue

            try:
                signal = self._analyze_timeframe_trend(timeframe, indicators, timestamp)
                if signal:
                    self.trend_history[timeframe].append(signal)
                    # Keep history manageable (last 100 signals)
                    if len(self.trend_history[timeframe]) > 100:
                        self.trend_history[timeframe] = self.trend_history[timeframe][-100:]
            except Exception as e:
                logger.error(f"Error generating trend signal for {timeframe}: {e}")

    def _analyze_timeframe_trend(
        self,
        timeframe: Timeframe,
        indicators: dict[str, Any],
        timestamp: datetime,
        data_quality: str = "ok",
    ) -> TrendSignal | None:
        """Analyze trend for a specific timeframe"""
        # Single pass: collect all indicator latest values AND
        # locate ATR/ADX/SuperTrend by name simultaneously.
        indicator_values: dict[str, float | None] = {}
        atr_ind: BaseIndicator | None = None
        adx_ind: BaseIndicator | None = None
        supertrend_ind: BaseIndicator | None = None
        bollinger_ind: BaseIndicator | None = None
        for name, ind in indicators.items():
            try:
                indicator_values[name] = ind.get_latest()
            except Exception as e:
                logger.debug(f"Error getting value for {name} indicator: {e}")
                indicator_values[name] = None
            n = getattr(ind, "name", "")
            if n == "ATR":
                atr_ind = ind
            elif n == "ADX":
                adx_ind = ind
            elif n == "SuperTrend":
                supertrend_ind = ind
            elif n == "Bollinger_Bands":
                bollinger_ind = ind

        # Skip if we don't have enough data
        if all(v is None for v in indicator_values.values()):
            return None

        # Analyze trend based on available indicators.
        # Legacy (v1): sub-5m MACD uses a binary ±1 sign because its scoring
        # stack intentionally omitted ATR — which saturates the fast cards
        # (AAPL 1m/2m/3m pinned at ±100). The Gated Hybrid (v2) ATR-normalizes
        # MACD on every timeframe so conviction is graded, not a rail. The ATR
        # indicator already exists on 1m/2m/3m (for the momentum measure), so
        # this only changes how MACD is scored, not the stack.
        if _settings.trend.signal_v2:
            scoring_atr = atr_ind
        else:
            scoring_atr = None if timeframe in _SHORT_HORIZON_TIMEFRAMES else atr_ind
        result = self._calculate_trend(
            timeframe, indicator_values, scoring_atr, adx_ind, supertrend_ind
        )
        direction, strength, confidence, raw_score, classification, attribution = result
        short_momentum, short_momentum_score = self._short_horizon_momentum(
            timeframe, atr_value=indicator_values.get("atr")
        )
        key_levels = self._extract_key_levels(supertrend_ind, bollinger_ind)

        # E5: maturity label + stop distance from SuperTrend band_distance_atr.
        band_dist_raw = (
            getattr(supertrend_ind, "band_distance_atr", None) if supertrend_ind else None
        )
        ch = self.get_trend_change_history(timeframe)
        held_bars = ch.get("bars_in_state") if ch else None
        maturity, stop_distance_atr = self._compute_maturity(band_dist_raw, held_bars)

        # E3: update the rolling ADX buffer and derive the slope label.
        adx_value_for_slope = indicator_values.get("adx")
        if adx_value_for_slope is not None:
            buf = self._adx_history.setdefault(timeframe, deque(maxlen=8))
            buf.append(float(adx_value_for_slope))
        else:
            buf = self._adx_history.get(timeframe, deque())
        adx_slope = self._compute_adx_slope(buf)

        # E4: higher-TF bias tag — read last completed signal from the anchor TF.
        # One-bar lag on the higher TF is acceptable; the engine holds all TFs.
        htf_bias: dict[str, Any] | None = None
        htf_tf = self._htf_anchor(timeframe)
        if htf_tf is not None:
            htf_history = self.trend_history.get(htf_tf)
            if htf_history:
                htf_sig = htf_history[-1]
                htf_bias = {
                    "timeframe": htf_tf.value,
                    "direction": htf_sig.direction.value,
                    "score": round(htf_sig.score, 1) if htf_sig.score is not None else None,
                    "classification": htf_sig.classification.value if htf_sig.classification else None,
                }

        return TrendSignal(
            symbol=self.symbol,
            timeframe=timeframe,
            direction=direction,
            strength=strength,
            confidence=confidence,
            timestamp=timestamp,
            score=raw_score,
            classification=classification,
            data_quality=data_quality,
            short_horizon_momentum=short_momentum,
            short_horizon_momentum_score=short_momentum_score,
            attribution=attribution,
            key_levels=key_levels,
            maturity=maturity,
            stop_distance_atr=stop_distance_atr,
            htf_bias=htf_bias,
            adx_slope=adx_slope,
        )

    @staticmethod
    def _extract_key_levels(
        supertrend_ind: BaseIndicator | None, bollinger_ind: BaseIndicator | None
    ) -> dict[str, Any]:
        """Collect the available indicator price levels for a card (TC-09).

        Only levels the timeframe actually calculates are returned; short
        timeframes that omit SuperTrend/Bollinger simply yield an empty dict.
        """
        levels: dict[str, Any] = {}
        if supertrend_ind is not None:
            flip = getattr(supertrend_ind, "prev_supertrend", None)
            is_up = getattr(supertrend_ind, "is_uptrend", None)
            if flip is not None and is_up is not None:
                levels["supertrend"] = {
                    "flip_price": round(float(flip), 4),
                    "direction": "up" if is_up else "down",
                    "band_distance_atr": (
                        round(float(supertrend_ind.band_distance_atr), 3)
                        if getattr(supertrend_ind, "band_distance_atr", None) is not None
                        else None
                    ),
                }
        if bollinger_ind is not None:
            upper = getattr(bollinger_ind, "upper_band", None)
            middle = getattr(bollinger_ind, "middle_band", None)
            lower = getattr(bollinger_ind, "lower_band", None)
            if upper and middle and lower:
                levels["bollinger"] = {
                    "upper": round(float(upper[-1]), 4),
                    "middle": round(float(middle[-1]), 4),
                    "lower": round(float(lower[-1]), 4),
                }
        return levels

    @staticmethod
    def _compute_maturity(
        band_distance_atr: float | None,
        held_bars: int | None,
    ) -> tuple[str | None, float | None]:
        """Derive trend maturity label and stop distance from SuperTrend band distance.

        Returns (maturity_label, stop_distance_atr).

        Maturity buckets (E5):
          band_dist < 0        → "just_flipped"  (price crossed ST this bar)
          0 ≤ dist < 0.5       → "fresh"          tight stop, high R:R entry
          0.5 ≤ dist < 2.0     → "developing"     move confirmed, normal stop
          2.0 ≤ dist < 4.0     → "healthy"        trend well established
          dist ≥ 4.0           → "extended"       caution — wide stop, late entry
        """
        if band_distance_atr is None:
            return None, None
        dist = float(band_distance_atr)
        stop_dist = round(dist, 3)
        if dist < 0:
            label = "just_flipped"
        elif dist < 0.5:
            label = "fresh"
        elif dist < 2.0:
            label = "developing"
        elif dist < 4.0:
            label = "healthy"
        else:
            label = "extended"
        # If held only 1-2 bars and dist is low, keep "fresh" even if dist
        # would say "developing" — the flip hasn't aged yet.
        if held_bars is not None and held_bars <= 2 and dist < 1.5:
            label = "fresh"
        return label, stop_dist

    @staticmethod
    def _compute_adx_slope(history: "deque[float]") -> str | None:
        """Derive ADX slope label from a rolling buffer (E3).

        Compares the mean of the 3 most-recent readings against the mean of
        the 3 readings before those. Returns:
          'strengthening'  — ADX rising  (delta >  2.0)
          'fading'         — ADX falling (delta < -2.0)
          'flat'           — delta within ±2.0
          None             — fewer than 6 readings (not enough history)
        """
        if len(history) < 6:
            return None
        vals = list(history)
        recent = sum(vals[-3:]) / 3.0
        older = sum(vals[-6:-3]) / 3.0
        delta = recent - older
        if delta > 2.0:
            return "strengthening"
        if delta < -2.0:
            return "fading"
        return "flat"

    # Higher-TF bias map (E4): each TF reads its designated anchor's last signal.
    _HTF_FOR_TIMEFRAME: dict["Timeframe", "Timeframe"] = {}  # populated lazily below

    @classmethod
    def _htf_anchor(cls, timeframe: "Timeframe") -> "Timeframe | None":
        """Return the designated higher-TF anchor for a given timeframe (E4)."""
        if not cls._HTF_FOR_TIMEFRAME:
            cls._HTF_FOR_TIMEFRAME = {
                Timeframe.ONE_MINUTE: Timeframe.FIFTEEN_MINUTE,
                Timeframe.TWO_MINUTE: Timeframe.FIFTEEN_MINUTE,
                Timeframe.THREE_MINUTE: Timeframe.FIFTEEN_MINUTE,
                Timeframe.FIVE_MINUTE: Timeframe.FIFTEEN_MINUTE,
                Timeframe.FIFTEEN_MINUTE: Timeframe.ONE_HOUR,
                Timeframe.THIRTY_MINUTE: Timeframe.ONE_HOUR,
                Timeframe.ONE_HOUR: Timeframe.FOUR_HOUR,
                Timeframe.TWO_HOUR: Timeframe.FOUR_HOUR,
                Timeframe.FOUR_HOUR: Timeframe.ONE_DAY,
                Timeframe.ONE_DAY: Timeframe.ONE_WEEK,
            }
        return cls._HTF_FOR_TIMEFRAME.get(timeframe)

    @staticmethod
    def _classify_short_horizon_momentum(
        bars: list[dict[str, float]], atr_value: float | None
    ) -> tuple[ShortHorizonMomentum | None, float | None]:
        """Classify closed-bar momentum without pretending it is ADX strength.

        The measure is deliberately compact and observable: four closed bars,
        net price displacement normalized by current ATR, and directional
        consistency (net movement divided by total movement).  A breakout
        needs both material displacement and persistence; alternating closes
        remain choppy even if their individual moves are large.
        """
        if len(bars) < _SHORT_MOMENTUM_BAR_COUNT or atr_value is None or atr_value <= 0:
            return None, None

        closes = [float(bar["close"]) for bar in bars[-_SHORT_MOMENTUM_BAR_COUNT:]]
        moves = [current - previous for previous, current in zip(closes, closes[1:])]
        gross_move = sum(abs(move) for move in moves)
        if gross_move <= 0:
            return ShortHorizonMomentum.CHOPPY, 0.0

        net_displacement_atr = abs(closes[-1] - closes[0]) / atr_value
        directional_consistency = abs(sum(moves)) / gross_move
        score = min(1.0, 0.6 * min(1.0, net_displacement_atr / 1.5) + 0.4 * directional_consistency)

        if net_displacement_atr >= 1.5 and directional_consistency >= 0.70:
            return ShortHorizonMomentum.PERSISTENT, score
        if net_displacement_atr >= 0.60 and directional_consistency >= 0.45:
            return ShortHorizonMomentum.DEVELOPING, score
        return ShortHorizonMomentum.CHOPPY, score

    def _short_horizon_momentum(
        self, timeframe: Timeframe, atr_value: float | None
    ) -> tuple[ShortHorizonMomentum | None, float | None]:
        if timeframe not in _SHORT_HORIZON_TIMEFRAMES:
            return None, None
        return self._classify_short_horizon_momentum(
            list(self._short_momentum_bars[timeframe]), atr_value
        )

    def _calculate_trend(
        self,
        timeframe: Timeframe,
        indicator_values: dict[str, float | None],
        atr_ind: BaseIndicator | None = None,
        adx_ind: BaseIndicator | None = None,
        supertrend_ind: BaseIndicator | None = None,
    ) -> tuple:
        """Calculate trend direction, strength, and confidence from indicators."""
        weights_cfg = _settings.trend.signal_weights

        # Pre-fetch key indicators (passed in; no second lookup)
        atr_value: float | None = None
        if atr_ind is not None:
            atr_value = atr_ind.get_latest()

        # (component name, directional signal in -1..1, weight)
        components: list[tuple[str, float, float]] = []
        trend_strength = TrendStrength.MODERATE
        adx_value: float | None = indicator_values.get("adx")
        di_directional_balance: float | None = None

        # --- Component 1: EMA crossover ---
        ema_fast = indicator_values.get("ema_fast")
        ema_slow = indicator_values.get("ema_slow")
        if ema_fast is not None and ema_slow is not None:
            spread = ema_fast - ema_slow
            spread_signal = max(-1.0, min(1.0, spread / (ema_slow * 0.01 + 1e-9)))
            components.append(("EMA", spread_signal, weights_cfg.ema))

        # --- Component 2: RSI continuous ---
        rsi = indicator_values.get("rsi")
        if rsi is not None:
            rsi_signal = max(-1.0, min(1.0, (rsi - 50) / 50))
            components.append(("RSI", rsi_signal, weights_cfg.rsi))

        # --- Component 3: MACD histogram continuous ---
        macd = indicator_values.get("macd")
        if macd is not None:
            if macd != 0.0 and atr_value is not None and atr_value > 0:
                norm = (macd / atr_value) / 10.0
                macd_signal = max(-1.0, min(1.0, norm))
            else:
                macd_signal = 1.0 if macd > 0 else -1.0
            components.append(("MACD", macd_signal, weights_cfg.macd))

        # --- Component 4: ADX strength + DI+/DI- direction ---
        if adx_ind is not None:
            di_plus_vals = getattr(adx_ind, "_di_plus", None)
            di_minus_vals = getattr(adx_ind, "_di_minus", None)
            if di_plus_vals is not None and di_minus_vals is not None:
                di_sum = di_plus_vals + di_minus_vals
                if di_sum > 0:
                    di_signal = (di_plus_vals - di_minus_vals) / di_sum
                    di_signal = max(-1.0, min(1.0, di_signal))
                    di_directional_balance = abs(di_signal)
                    components.append(("ADX/DI", di_signal, weights_cfg.adx))

            if adx_value is not None:
                if adx_value > 40:
                    trend_strength = TrendStrength.STRONG
                elif adx_value > 25:
                    trend_strength = TrendStrength.MODERATE
                elif adx_value > 15:
                    trend_strength = TrendStrength.WEAK
                else:
                    trend_strength = TrendStrength.WEAK

        # --- Component 5: SuperTrend direction ---
        # Unlike every other component here, this used to be a flat ±1 no
        # matter how fresh the flip was. Scale it by band_distance_atr (ATRs
        # of cushion over the active band) instead, so a signal that just
        # flipped counts as a weak vote and a well-established trend counts
        # as a strong one — 3+ ATRs of cushion is treated as full conviction.
        if supertrend_ind is not None:
            st_is_up = getattr(supertrend_ind, "is_uptrend", None)
            if st_is_up is not None:
                band_dist = getattr(supertrend_ind, "band_distance_atr", None)
                if band_dist is not None:
                    magnitude = max(0.2, min(1.0, band_dist / 3.0))
                else:
                    magnitude = 1.0
                components.append(
                    ("SuperTrend", magnitude if st_is_up else -magnitude, weights_cfg.supertrend)
                )

        # --- Component 6: Bollinger Bands market structure ---
        bb = indicator_values.get("bollinger_bands")
        if bb is not None:
            bb_signal = max(-1.0, min(1.0, (bb - 0.5) * 2))
            components.append(("Bollinger", bb_signal, weights_cfg.bollinger))

        # --- Component 7: Volume confirmation ---
        rel_vol = indicator_values.get("relative_volume")
        if rel_vol is not None and rel_vol > 0:
            vol_signal = max(-1.0, min(1.0, (rel_vol - 1.0) * 2))
            components.append(("Relative volume", vol_signal, weights_cfg.relative_volume))

        # --- Component 8: Momentum (ROC) continuous ---
        roc = indicator_values.get("roc")
        if roc is not None:
            if roc != 0.0 and atr_value is not None and atr_value > 0:
                roc_signal = max(-1.0, min(1.0, roc / 5.0))
            else:
                roc_signal = 1.0 if roc > 0 else -1.0
            components.append(("ROC", roc_signal, weights_cfg.momentum))

        # --- Combine ---
        attribution: list[dict[str, Any]] = []
        if components:
            active = [(name, s, w) for name, s, w in components if w > 0]
            if active:
                weighted_sum = sum(s * w for _, s, w in active)
                total_weight = sum(w for _, _, w in active)
                avg_signal = weighted_sum / total_weight
                # Each component's contribution is its share of the -100..+100
                # composite: signal * weight / total_weight * 100. They sum to
                # the raw score, so the card can show what actually drove it.
                attribution = sorted(
                    (
                        {
                            "component": name,
                            "signal": round(s, 4),
                            "weight": w,
                            "contribution": round(s * w / total_weight * 100.0, 2),
                        }
                        for name, s, w in active
                    ),
                    key=lambda entry: abs(entry["contribution"]),
                    reverse=True,
                )
            else:
                avg_signal = 0.0

            # --- Gated Hybrid (TREND_SIGNAL_V2): direction → regime → conviction ---
            # SuperTrend acts as the direction gate, ADX as the regime (trend-
            # existence) gate, and |avg_signal| as conviction magnitude so
            # the composite becomes a measure of *how strong* the move is in
            # SuperTrend's direction — not the direction arbiter itself.
            # Only activates when SuperTrend is warmed up (is_uptrend not None).
            # Falls through to the legacy path when SuperTrend isn't available
            # (e.g. still warming up or flag off).
            _ADX_RANGE_GATE = 20.0
            _v2_applied = False
            if _settings.trend.signal_v2 and supertrend_ind is not None:
                st_is_up = getattr(supertrend_ind, "is_uptrend", None)
                if st_is_up is not None:
                    _v2_applied = True
                    direction_sign = 1.0 if st_is_up else -1.0
                    # Regime factor: below the range gate collapse score toward 0.
                    # adx=0 → 0.0, adx=20+ → 1.0.
                    if adx_value is not None:
                        regime_factor = min(1.0, adx_value / _ADX_RANGE_GATE)
                    else:
                        regime_factor = 1.0  # no ADX data — don't suppress
                    conviction = abs(avg_signal)
                    raw_score = max(
                        -100.0, min(100.0, direction_sign * regime_factor * conviction * 100.0)
                    )
                    classification = classify_score(raw_score)
                    if adx_value is not None and adx_value < _ADX_RANGE_GATE:
                        direction = TrendDirection.SIDEWAYS  # "No trend / range"
                    else:
                        direction = TrendDirection.UPTREND if st_is_up else TrendDirection.DOWNTREND
                    abs_score = abs(raw_score) / 100.0
                    _conf_thr = 0.25
                    if abs_score >= _conf_thr:
                        confidence = 0.5 + 0.5 * (abs_score - _conf_thr) / (1.0 - _conf_thr)
                    else:
                        confidence = 0.5 * (abs_score / _conf_thr)
                    confidence = min(confidence, 1.0)

            if not _v2_applied:
                # --- Legacy direction / confidence (v1 path) ---
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

                # Rescale so crossing `threshold` (the same value that just
                # decided direction, above) maps to confidence == 0.5, and
                # the maximum possible |avg_signal| (1.0) maps to 1.0 —
                # instead of confidence == the raw signal magnitude.
                #
                # Before this fix, confidence was literally `abs(avg_signal)`,
                # so a stock at the exact moment its trend was confirmed
                # (avg_signal == threshold, 0.15-0.35) reported confidence
                # 0.15-0.35 — permanently below every `min_confidence >= 0.5`
                # default in the app (NL search, DailyBullish/DailyBearish,
                # MinTimeframeBullish/Bearish, MTFAlignment, the scanner's
                # own MULTI_TIMEFRAME_BULLISH/BEARISH signal at scanner.py's
                # `confidence > 0.6` check). Reaching 0.5 required an
                # unusually strong single-direction alignment across all 8
                # components — "confidently trending" and "clears the
                # app's default confidence filter" were effectively two
                # different, uncoordinated bars. Found live 2026-09-09:
                # AI Stock Search's "bearish stocks" returned zero matches
                # even with a real downtrend (DVLT, confidence 0.365)
                # sitting in the watchlist.
                abs_signal = abs(avg_signal)
                if abs_signal >= threshold:
                    confidence = 0.5 + 0.5 * (abs_signal - threshold) / (1.0 - threshold)
                else:
                    confidence = 0.5 * (abs_signal / threshold)
                confidence = min(confidence, 1.0)
                raw_score = max(-100.0, min(100.0, avg_signal * 100.0))
                classification = classify_score(raw_score)

            # --- Strength escalation (both paths) ---
            # ADX measures movement, not direction. An exceptional trend
            # needs a decisive DI imbalance and a confirming composite score
            # before it can be labeled Very Strong. The thresholds were set
            # from the enabled-watchlist distribution on 2026-09-24.
            if (
                adx_value is not None
                and adx_value > 50
                and di_directional_balance is not None
                and di_directional_balance >= 0.50
                and abs(raw_score) >= 60
            ):
                trend_strength = TrendStrength.VERY_STRONG

            if trend_strength == TrendStrength.VERY_STRONG:
                confidence = min(confidence * 1.3, 1.0)
            elif trend_strength == TrendStrength.STRONG:
                confidence = min(confidence * 1.2, 1.0)
            elif trend_strength == TrendStrength.WEAK:
                confidence = confidence * 0.8
        else:
            direction = TrendDirection.UNKNOWN
            confidence = 0.0
            raw_score = 0.0
            classification = TrendClassification.NO_SIGNAL

        return direction, trend_strength, confidence, raw_score, classification, attribution

    def get_current_trend(self, timeframe: Timeframe) -> TrendSignal | None:
        """Get the current trend signal for a timeframe"""
        if self.trend_history.get(timeframe):
            return self.trend_history[timeframe][-1]
        return None

    def get_trend_history(
        self, timeframe: Timeframe, limit: int | None = None
    ) -> list[TrendSignal]:
        """Get trend history for a timeframe"""
        history = self.trend_history.get(timeframe, [])
        if limit is not None:
            return history[-limit:] if len(history) > limit else history
        return history.copy()

    # Bound the transition log so a long-lived engine can't grow it without
    # limit. 100 direction changes is far more history than any card shows.
    _MAX_TREND_STATE_RUNS = 100

    def _record_trend_state(
        self, timeframe: Timeframe, direction: str, timestamp: datetime
    ) -> None:
        """Record a direction RUN keyed by the current closed-bar index (TC-09).

        Called once per closed bar from ``_update_from_bar`` (never from the
        tick path), so a new entry is appended only when the direction actually
        changes. ``_bar_counts[timeframe]`` is the closed-bar index and must
        already have been incremented for this bar.
        """
        runs = self._trend_state_runs.setdefault(timeframe, [])
        if runs and runs[-1]["direction"] == direction:
            return
        runs.append(
            {
                "bar_index": self._bar_counts.get(timeframe, len(runs) + 1),
                "direction": direction,
                "timestamp": timestamp,
            }
        )
        if len(runs) > self._MAX_TREND_STATE_RUNS:
            del runs[: len(runs) - self._MAX_TREND_STATE_RUNS]

    def get_trend_change_history(self, timeframe: Timeframe) -> dict[str, Any] | None:
        """Closed-bar change history for a timeframe, or ``None`` before any bar.

        ``bars_in_state`` counts closed bars the current direction has held,
        inclusive of the bar it started on. ``witnessed_change`` is ``True``
        only when this engine actually observed the transition into the current
        state (an earlier run exists); when the whole tracked log is a single
        run, ``bars_in_state`` is a lower bound and ``previous_direction`` /
        ``changed_at`` are ``None``.
        """
        runs = self._trend_state_runs.get(timeframe)
        if not runs:
            return None
        current = runs[-1]
        latest_index = self._bar_counts.get(timeframe, current["bar_index"])
        bars_in_state = max(1, latest_index - current["bar_index"] + 1)
        witnessed_change = len(runs) >= 2
        return {
            "direction": current["direction"],
            "bars_in_state": bars_in_state,
            "previous_direction": runs[-2]["direction"] if witnessed_change else None,
            "changed_at": current["timestamp"] if witnessed_change else None,
            "witnessed_change": witnessed_change,
        }

    def get_trend_by_timeframe(self) -> dict[Timeframe, TrendSignal]:
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
            timestamp=timestamp or datetime.now(UTC),
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

        trends = self.get_trend_by_timeframe()
        if not trends:
            return None

        # Calculate weighted average direction
        direction_scores = []
        total_weight = 0

        for timeframe, trend in trends.items():
            weight = timeframe_weights.get(
                timeframe, 0.1
            )  # 0.1 is a safe default; overridden by settings
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
                timestamp=datetime.now(UTC),
                score=avg_score * 100.0,
            )

        return None


# Global trend engine factory
def get_trend_engine(symbol: str) -> TrendEngine:
    """Get or create trend engine for a symbol"""
    # In a real implementation, you might cache these
    return TrendEngine(symbol)
