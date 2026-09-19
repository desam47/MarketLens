"""
Multi-timeframe analysis engine for detecting trend confluence and alignment.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from ..config.settings import settings
from ..engines.timeframe import Timeframe
from ..trend.trend_engine import (
    TrendClassification,
    TrendDirection,
    TrendEngine,
    TrendSignal,
    TrendStrength,
    strength_to_float,
)

logger = logging.getLogger(__name__)


class ConfluenceDirection(StrEnum):
    """Overall market direction based on multiple timeframes"""
    STRONG_UPTREND = "strong_uptrend"
    UPTREND = "uptrend"
    WEAK_UPTREND = "weak_uptrend"
    NEUTRAL = "neutral"
    WEAK_DOWNTREND = "weak_downtrend"
    DOWNTREND = "downtrend"
    STRONG_DOWNTREND = "strong_downtrend"


# Phase 7 spec: two named preset configurations.
# Day trading: 5m → 1d (no 1m, no 1w).
# Swing:      15m → 1w (no 1m, no 5m, no 30m).
# These are intentionally frozensets (not dicts) — order doesn't matter,
# only membership does.
PRESET_SCALPER: frozenset[Timeframe] = frozenset({
    Timeframe.ONE_MINUTE,
    Timeframe.TWO_MINUTE,
    Timeframe.THREE_MINUTE,
    Timeframe.FIVE_MINUTE,
    Timeframe.FIFTEEN_MINUTE,
})

PRESET_DAY_TRADING: frozenset[Timeframe] = frozenset({
    Timeframe.FIVE_MINUTE,
    Timeframe.FIFTEEN_MINUTE,
    Timeframe.THIRTY_MINUTE,
    Timeframe.ONE_HOUR,
    Timeframe.FOUR_HOUR,
})

PRESET_SWING: frozenset[Timeframe] = frozenset({
    Timeframe.FIFTEEN_MINUTE,
    Timeframe.ONE_HOUR,
    Timeframe.FOUR_HOUR,
    Timeframe.ONE_DAY,
    Timeframe.ONE_WEEK,
})

# All 8 timeframes the spec demands. The full set is what callers can
# opt into via the "all" preset name.
ALL_TIMEFRAMES: frozenset[Timeframe] = frozenset({
    Timeframe.ONE_MINUTE,
    Timeframe.FIVE_MINUTE,
    Timeframe.FIFTEEN_MINUTE,
    Timeframe.THIRTY_MINUTE,
    Timeframe.ONE_HOUR,
    Timeframe.FOUR_HOUR,
    Timeframe.ONE_DAY,
    Timeframe.ONE_WEEK,
})

# Order matters: the dashboard renders presets in this order.
_PRESET_MAP: dict[str, frozenset[Timeframe]] = {
    "scalper": PRESET_SCALPER,
    "day_trading": PRESET_DAY_TRADING,
    "swing": PRESET_SWING,
    "all": ALL_TIMEFRAMES,
}

PRESET_NAMES: tuple[str, ...] = ("scalper", "day_trading", "swing", "all")


class ConfluenceSignal:
    """Represents a multi-timeframe confluence signal.

    Carries the spec's alignment metrics in additive form so existing
    API consumers keep working. The new fields are:

    - ``bullish_alignment``  : fraction of TFs in UPTREND (0..1)
    - ``bearish_alignment``  : fraction of TFs in DOWNTREND (0..1)
    - ``conflicting``        : count of TFs disagreeing with the majority
    - ``short_term_direction``, ``intermediate_direction``,
      ``higher_direction`` : the directional bucket at each end of the
      preset's time horizon and the median
    - ``preset``            : which preset produced this signal
    """

    def __init__(self,
                 symbol: str,
                 direction: ConfluenceDirection,
                 strength: float,  # 0.0 to 1.0
                 alignment_score: float,  # How aligned timeframes are (0.0 to 1.0)
                 timeframe_signals: dict[Timeframe, TrendSignal],
                 timestamp: datetime,
                 bullish_alignment: float = 0.0,
                 bearish_alignment: float = 0.0,
                 conflicting: int = 0,
                 short_term_direction: TrendDirection = TrendDirection.UNKNOWN,
                 intermediate_direction: TrendDirection = TrendDirection.UNKNOWN,
                 higher_direction: TrendDirection = TrendDirection.UNKNOWN,
                 preset: str = "day_trading"):
        self.symbol = symbol
        self.direction = direction
        self.strength = strength
        self.alignment_score = alignment_score
        self.timeframe_signals = timeframe_signals
        self.timestamp = timestamp
        # Phase 7 additions:
        self.bullish_alignment = bullish_alignment
        self.bearish_alignment = bearish_alignment
        self.conflicting = conflicting
        self.short_term_direction = short_term_direction
        self.intermediate_direction = intermediate_direction
        self.higher_direction = higher_direction
        self.preset = preset

    def __repr__(self):
        return (f"ConfluenceSignal({self.symbol} {self.direction.value} "
                f"str:{self.strength:.2f} align:{self.alignment_score:.2f} "
                f"preset:{self.preset})")


# ---------------------------------------------------------------------------
# Phase 7 spec models: TimeframeTrendSnapshot + MultiTimeframeSnapshot
# ---------------------------------------------------------------------------


@dataclass
class TimeframeTrendSnapshot:
    """Snapshot of one timeframe's trend state.

    Mirrors the Phase 6 ``TrendSnapshot`` shape, trimmed to the fields
    that are meaningful for a single timeframe (no per-TF momentum vs
    structure — those are on the underlying ``TrendSnapshot`` and stay
    there).
    """
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    direction: TrendClassification     # Phase 6 8-class bucket
    score: float                        # -100..+100
    strength: float                     # 0.0..1.0 (numeric, from strength_to_float)
    confidence: float                   # 0.0..1.0
    data_quality: str
    strategy_version: str
    # Phase 7+: quality metrics for weighted aggregation
    data_age_seconds: float = 0.0
    bar_closed: bool = False
    is_warmed_up: bool = False
    valid: bool = False                 # has signal + warmed up + fresh enough
    quality_weight: float = 0.0         # confidence * freshness * warmup * (1 if closed else 0.5)


@dataclass
class MultiTimeframeSnapshot:
    """Multi-timeframe confluence snapshot for a symbol.

    Carries every field the Phase 7 spec explicitly demands:
    alignment, bullish alignment, bearish alignment, conflicting
    timeframes, short/intermediate/higher-timeframe direction. Plus
    the per-TF map for downstream callers that want to render the
    matrix.
    """
    symbol: str
    timestamp: datetime
    preset: str                         # "day_trading" | "swing" | "all"
    direction: ConfluenceDirection      # 7-class overall bucket
    strength: float                     # 0.0..1.0
    alignment_score: float              # 0.0..1.0
    bullish_alignment: float            # 0.0..1.0
    bearish_alignment: float            # 0.0..1.0
    conflicting: int                    # TFs disagreeing with majority
    short_term_direction: TrendClassification    # shortest active TF
    intermediate_direction: TrendClassification  # median active TF
    higher_direction: TrendClassification        # longest active TF
    timeframe_snapshots: dict[Timeframe, TimeframeTrendSnapshot] = field(
        default_factory=dict,
    )
    strategy_version: str = ""
    # Phase 7+: quality metrics
    valid_coverage: float = 0.0          # fraction of preset TFs with valid signals
    quality_weighted_score: float = 0.0  # aggregate score using quality weights


def _timeframe_seconds(tf: Timeframe) -> int:
    """Rough ordering key for short/intermediate/higher bucketing.

    Not a real "duration" — we don't care about exact seconds — just a
    monotonic sort key so the shortest TF lands at index 0 and the
    longest at the end. We only use this to pick the
    short/intermediate/higher representatives, so an approximation is
    fine.
    """
    return {
        Timeframe.ONE_MINUTE: 60,
        Timeframe.FIVE_MINUTE: 300,
        Timeframe.FIFTEEN_MINUTE: 900,
        Timeframe.THIRTY_MINUTE: 1800,
        Timeframe.ONE_HOUR: 3600,
        Timeframe.TWO_HOUR: 7200,
        Timeframe.FOUR_HOUR: 14400,
        Timeframe.ONE_DAY: 86400,
        Timeframe.ONE_WEEK: 604800,
    }.get(tf, 0)


class MultiTimeframeEngine:
    """Engine for analyzing trends across multiple timeframes.

    Per Phase 7 spec, builds one trend engine per *preset* timeframe.
    The full 8-TF list (``ALL_TIMEFRAMES``) is also available via the
    ``preset="all"`` arg. Timeframe weights for the overall confluence
    score come from ``settings.multitimeframe.weights`` (Principle 11)
    rather than a hard-coded dict.
    """

    def __init__(self, symbol: str, preset: str | None = None):
        self.symbol = symbol
        self.preset_name = preset or settings.multitimeframe.default_preset
        # Resolve the preset. Unknown names fall back to day_trading
        # for safety (the spec says "do not assume all timeframes are
        # equally important" — silently using a superset would
        # violate that intent).
        self.active_timeframes: frozenset[Timeframe] = (
            _PRESET_MAP.get(self.preset_name, PRESET_DAY_TRADING)
        )
        # The legacy class advertised a single ordered `analysis_timeframes`
        # list for the API/tests to inspect. Keep the same shape, filtered
        # by the preset, ordered short → long.
        self.analysis_timeframes: list[Timeframe] = sorted(
            self.active_timeframes, key=_timeframe_seconds,
        )

        self.trend_engines: dict[Timeframe, TrendEngine] = {}

        # Per-TF weight lookup. Replaces the hard-coded dict that used
        # to live inside _calculate_overall_direction.
        self.timeframe_weights: dict[str, float] = dict(
            settings.multitimeframe.weights,
        )

        # Confluence history (existing — unchanged shape)
        self.confluence_history: list[ConfluenceSignal] = []
        # Phase 7: parallel snapshot history (new dataclass)
        self.snapshot_history: list[MultiTimeframeSnapshot] = []

        self._initialize_trend_engines()

    def _initialize_trend_engines(self):
        """Initialize trend engines for each active timeframe."""
        for timeframe in self.analysis_timeframes:
            self.trend_engines[timeframe] = TrendEngine(self.symbol)

    def reset(self) -> None:
        """Reset all per-symbol state so this engine instance is fresh.

        This clears the shared timeframe engine's tick/candle tracking and the
        per-TF trend signal histories. Call this in test setUp when re-using
        the same ``symbol`` across multiple tests to prevent state from the
        previous test leaking into the next.
        """
        from ..engines.timeframe import multi_symbol_timeframe_engine
        tf_engine = multi_symbol_timeframe_engine.engines.get(self.symbol)
        if tf_engine is not None:
            tf_engine.reset()
        self.confluence_history.clear()
        self.snapshot_history.clear()
        for engine in self.trend_engines.values():
            engine.trend_history.clear()

    # ------------------------------------------------------------------
    # Confluence signal + snapshot
    # ------------------------------------------------------------------

    def _generate_confluence_signal(self, timestamp: datetime) -> None:
        """Generate a multi-timeframe confluence signal and snapshot."""
        # Get current trends from all active timeframes
        timeframe_signals: dict[Timeframe, TrendSignal] = {}
        for timeframe, engine in self.trend_engines.items():
            trend = engine.get_current_trend(timeframe)
            if trend:
                timeframe_signals[timeframe] = trend

        if not timeframe_signals:
            # No per-TF trend signals available for this bar. Fall back to
            # the last known confluence signal (with the new bar's timestamp)
            # so the MTF display always shows live data rather than going
            # silent when a bar arrives but doesn't cross any trend threshold.
            # Only fall back when we have a previous signal to base on.
            if self.confluence_history:
                prev = self.confluence_history[-1]
                signal = ConfluenceSignal(
                    symbol=self.symbol,
                    direction=prev.direction,
                    strength=prev.strength,
                    alignment_score=prev.alignment_score,
                    timeframe_signals=prev.timeframe_signals,
                    timestamp=timestamp,
                    bullish_alignment=prev.bullish_alignment,
                    bearish_alignment=prev.bearish_alignment,
                    conflicting=prev.conflicting,
                    short_term_direction=prev.short_term_direction,
                    intermediate_direction=prev.intermediate_direction,
                    higher_direction=prev.higher_direction,
                    preset=self.preset_name,
                )
                self.confluence_history.append(signal)
                if len(self.confluence_history) > 1000:
                    self.confluence_history = self.confluence_history[-1000:]
            return

        # Existing alignment score (dominant-direction fraction)
        alignment_score = self._calculate_alignment(timeframe_signals)

        # New (Phase 7) alignment metrics
        bullish_align, bearish_align, conflicting_count = (
            self._calculate_directional_alignment(timeframe_signals)
        )

        # Short / intermediate / higher-timeframe direction picks
        short_dir, inter_dir, higher_dir = self._calculate_horizon_directions(
            timeframe_signals,
        )

        # Existing overall direction + strength (unchanged logic)
        direction, strength = self._calculate_overall_direction(timeframe_signals)

        # Build the ConfluenceSignal with the new fields layered on.
        signal = ConfluenceSignal(
            symbol=self.symbol,
            direction=direction,
            strength=strength,
            alignment_score=alignment_score,
            timeframe_signals=timeframe_signals,
            timestamp=timestamp,
            bullish_alignment=bullish_align,
            bearish_alignment=bearish_align,
            conflicting=conflicting_count,
            short_term_direction=short_dir,
            intermediate_direction=inter_dir,
            higher_direction=higher_dir,
            preset=self.preset_name,
        )

        # Store in history (capped at 1000)
        self.confluence_history.append(signal)
        if len(self.confluence_history) > 1000:
            self.confluence_history = self.confluence_history[-1000:]

        # Build the snapshot (Phase 7 dataclass) and append to history.
        snapshot = self.build_snapshot(timestamp)
        if snapshot is not None:
            self.snapshot_history.append(snapshot)
            if len(self.snapshot_history) > 1000:
                self.snapshot_history = self.snapshot_history[-1000:]

    # ------------------------------------------------------------------
    # Alignment calculations
    # ------------------------------------------------------------------

    def _calculate_alignment(self, signals: dict[Timeframe, TrendSignal],
                          tf_snapshots: dict[Timeframe, TimeframeTrendSnapshot] | None = None) -> float:
        """How aligned the timeframes are (0.0 = no alignment, 1.0 = perfect).

        If tf_snapshots provided, uses quality-weighted alignment based on valid TFs only.
        """
        if not signals:
            return 0.0
        if len(signals) < 2:
            return 1.0

        # Quality-weighted alignment (new)
        if tf_snapshots is not None:
            valid_snaps = [s for s in tf_snapshots.values() if s.valid]
            if not valid_snaps:
                return 0.0
            
            bullish_weight = sum(s.quality_weight for s in valid_snaps
                                if s.direction in (TrendClassification.STRONG_BULLISH,
                                                  TrendClassification.BULLISH,
                                                  TrendClassification.WEAK_BULLISH))
            bearish_weight = sum(s.quality_weight for s in valid_snaps
                                  if s.direction in (TrendClassification.STRONG_BEARISH,
                                                    TrendClassification.BEARISH,
                                                    TrendClassification.WEAK_BEARISH))
            neutral_weight = sum(s.quality_weight for s in valid_snaps
                                 if s.direction == TrendClassification.NEUTRAL)
            
            total_weight = bullish_weight + bearish_weight + neutral_weight
            if total_weight == 0:
                return 0.0
            max_weight = max(bullish_weight, bearish_weight, neutral_weight)
            return max_weight / total_weight

        # Legacy equal-count alignment
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
        return max_count / total if total > 0 else 0.0

    def _calculate_directional_alignment(
        self, signals: dict[Timeframe, TrendSignal],
    ) -> tuple[float, float, int]:
        """Return (bullish_align, bearish_align, conflicting_count).

        - ``bullish_align`` : fraction of TFs in UPTREND (0..1)
        - ``bearish_align`` : fraction of TFs in DOWNTREND (0..1)
        - ``conflicting``   : number of TFs not in the majority direction

        SIDEWAYS counts as neither bullish nor bearish; if SIDEWAYS is
        the majority, then *both* bullish and bearish counts are 0 and
        ``conflicting`` equals the total (everything disagrees with the
        majority).
        """
        if not signals:
            return 0.0, 0.0, 0
        uptrend_count = sum(
            1 for s in signals.values() if s.direction == TrendDirection.UPTREND
        )
        downtrend_count = sum(
            1 for s in signals.values() if s.direction == TrendDirection.DOWNTREND
        )
        sideways_count = sum(
            1 for s in signals.values()
            if s.direction not in (TrendDirection.UPTREND, TrendDirection.DOWNTREND)
        )
        total = len(signals)
        majority = max(uptrend_count, downtrend_count, sideways_count)
        return (
            uptrend_count / total,
            downtrend_count / total,
            total - majority,
        )

    def _calculate_horizon_directions(
        self, signals: dict[Timeframe, TrendSignal],
    ) -> tuple[TrendDirection, TrendDirection, TrendDirection]:
        """Pick the directional bucket for short/intermediate/higher TFs.

        - short : shortest active TF that has a signal
        - higher: longest active TF that has a signal
        - intermediate: median (by ``_timeframe_seconds``) active TF

        Returns ``(UNKNOWN, UNKNOWN, UNKNOWN)`` if there are no signals.
        For a single-TF preset, all three return the same direction.
        """
        if not signals:
            return (TrendDirection.UNKNOWN,
                    TrendDirection.UNKNOWN,
                    TrendDirection.UNKNOWN)
        ordered = sorted(signals.keys(), key=_timeframe_seconds)
        short = ordered[0]
        higher = ordered[-1]
        mid_idx = len(ordered) // 2
        intermediate = ordered[mid_idx]
        return (
            signals[short].direction,
            signals[intermediate].direction,
            signals[higher].direction,
        )

    # ------------------------------------------------------------------
    # Existing overall-direction calculation (preserved verbatim logic
    # but now reading weights from settings instead of a hard-coded
    # dict — the only change is the source of the weight value).
    # ------------------------------------------------------------------

    def _calculate_overall_direction(
        self, signals: dict[Timeframe, TrendSignal],
        tf_snapshots: dict[Timeframe, TimeframeTrendSnapshot] | None = None,
    ) -> tuple[ConfluenceDirection, float]:
        """Calculate overall direction and strength from timeframe signals.

        If tf_snapshots is provided, uses quality-weighted hierarchical aggregation
        with raw scores (-100..+100). Otherwise falls back to legacy ternary scoring.
        """
        if not signals:
            return ConfluenceDirection.NEUTRAL, 0.0

        # Quality-weighted hierarchical aggregation (new)
        if tf_snapshots is not None:
            quality_score = self._calculate_quality_weighted_score(tf_snapshots)
            avg_score = quality_score / 100.0  # normalize -100..+100 to -1..+1

            # Strength from average quality-weighted strength
            valid_snaps = [s for s in tf_snapshots.values() if s.valid]
            avg_strength_raw = (
                sum(s.strength for s in valid_snaps) / len(valid_snaps)
                if valid_snaps else 0.5
            )
            avg_strength = avg_strength_raw  # already 0..1

            # Alignment based on valid TFs only
            valid_signals = {tf: sig for tf, sig in signals.items()
                           if tf in tf_snapshots and tf_snapshots[tf].valid}
            alignment = self._calculate_alignment(valid_signals) if valid_signals else 0.0

            # Direction from quality-weighted score with hysteresis thresholds
            if alignment > 0.6:
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
                if avg_score > 0.2:
                    direction = ConfluenceDirection.WEAK_UPTREND
                elif avg_score < -0.2:
                    direction = ConfluenceDirection.WEAK_DOWNTREND
                else:
                    direction = ConfluenceDirection.NEUTRAL

            final_strength = (alignment * 0.5) + (avg_strength * 0.5)
            final_strength = max(0.0, min(1.0, final_strength))
            return direction, final_strength

        # Legacy ternary scoring (fallback)
        weighted_score = 0.0
        total_weight = 0.0
        strength_values: list[int] = []

        for timeframe, signal in signals.items():
            weight = self.timeframe_weights.get(timeframe.value, 0.05)

            if signal.direction == TrendDirection.UPTREND:
                score = 1
            elif signal.direction == TrendDirection.DOWNTREND:
                score = -1
            else:
                score = 0

            weighted_score += score * weight * signal.confidence
            total_weight += weight

            strength_map = {
                TrendStrength.WEAK: 1,
                TrendStrength.MODERATE: 2,
                TrendStrength.STRONG: 3,
                TrendStrength.VERY_STRONG: 4,
            }
            strength_values.append(strength_map.get(signal.strength, 2))

        avg_score = weighted_score / total_weight if total_weight > 0 else 0.0
        avg_strength_raw = (
            sum(strength_values) / len(strength_values) if strength_values else 2
        )
        avg_strength = (avg_strength_raw - 1) / 3  # 1..4 → 0..1

        alignment = self._calculate_alignment(signals)

        if alignment > 0.6:
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
            if avg_score > 0.2:
                direction = ConfluenceDirection.WEAK_UPTREND
            elif avg_score < -0.2:
                direction = ConfluenceDirection.WEAK_DOWNTREND
            else:
                direction = ConfluenceDirection.NEUTRAL

        final_strength = (alignment * 0.5) + (avg_strength * 0.5)
        final_strength = max(0.0, min(1.0, final_strength))
        return direction, final_strength

    def _calculate_quality_weighted_score(
        self, tf_snapshots: dict[Timeframe, TimeframeTrendSnapshot],
    ) -> float:
        """Calculate quality-weighted aggregate score using raw scores and hierarchical weights.

        Uses quality_weight per TF (confidence * freshness * warmup * bar_closed_factor)
        and hierarchical timeframe weights (higher TFs = bias, intermediate = structure,
        lower = timing).
        """
        if not tf_snapshots:
            return 0.0

        # Hierarchical weights by timeframe (higher = more weight for bias)
        hierarchy_weights = {
            Timeframe.ONE_WEEK: 0.25,
            Timeframe.ONE_DAY: 0.20,
            Timeframe.FOUR_HOUR: 0.15,
            Timeframe.ONE_HOUR: 0.12,
            Timeframe.THIRTY_MINUTE: 0.08,
            Timeframe.FIFTEEN_MINUTE: 0.08,
            Timeframe.FIVE_MINUTE: 0.06,
            Timeframe.THREE_MINUTE: 0.03,
            Timeframe.TWO_MINUTE: 0.02,
            Timeframe.ONE_MINUTE: 0.01,
        }

        total_weighted_score = 0.0
        total_quality_weight = 0.0

        for tf, snap in tf_snapshots.items():
            # Only use valid TFs for the aggregate
            if not snap.valid:
                continue
            
            preset_weight = self.timeframe_weights.get(tf.value, 0.05)
            hierarchy_weight = hierarchy_weights.get(tf, 0.05)
            # Combine preset weight with hierarchy weight (equal mix)
            combined_weight = (preset_weight + hierarchy_weight) / 2.0
            
            # Quality weight already incorporates confidence, freshness, warmup, bar_closed
            effective_weight = combined_weight * snap.quality_weight
            
            total_weighted_score += snap.score * effective_weight
            total_quality_weight += effective_weight

        return total_weighted_score / total_quality_weight if total_quality_weight > 0 else 0.0

    # ------------------------------------------------------------------
    # Snapshot builder (Phase 7 dataclass)
    # ------------------------------------------------------------------

    def build_snapshot(
        self, timestamp: datetime | None = None,
    ) -> MultiTimeframeSnapshot | None:
        """Build a MultiTimeframeSnapshot from the current per-TF state.

        Returns ``None`` if no per-TF trend signal has been produced yet
        (matches the Phase 6 ``TrendEngine.build_snapshot`` contract).
        The snapshot's per-TF map uses the 8-class ``TrendClassification``
        (Phase 6) rather than the 4-class ``TrendDirection`` — the spec
        calls for the more granular bucket.
        """
        ts = timestamp or datetime.now(timezone.utc)
        timeframe_signals = self.get_all_timeframe_trends()
        if not timeframe_signals:
            return None

        # Get timeframe engine for freshness/bar status
        from ..engines.timeframe import multi_symbol_timeframe_engine
        tf_engine = multi_symbol_timeframe_engine.get_engine_for_symbol(self.symbol)

        # Per-TF snapshots with quality metrics
        tf_snapshots: dict[Timeframe, TimeframeTrendSnapshot] = {}
        for tf, sig in timeframe_signals.items():
            # Compute quality metrics
            data_age_seconds = (ts - sig.timestamp).total_seconds() if sig.timestamp else 0.0
            
            # Check bar closed status and warmup from timeframe engine
            bar_closed = False
            is_warmed_up = False
            if tf_engine is not None:
                latest_closed = tf_engine.get_latest_closed_candle(tf)
                if latest_closed:
                    # Bar is closed if latest closed candle matches signal timestamp
                    bar_closed = latest_closed.close_time >= sig.timestamp
                    # Consider warmed up if we have at least 50 closed bars (arbitrary threshold)
                    closed_count = len(tf_engine.get_closed_candles(tf))
                    is_warmed_up = closed_count >= 50
            
            # Valid if: has signal + data_quality ok + warmed up + fresh (< 2x timeframe period)
            tf_seconds = _timeframe_seconds(tf)
            fresh_enough = data_age_seconds <= (tf_seconds * 2) if tf_seconds > 0 else True
            valid = (sig.data_quality == "ok" and is_warmed_up and fresh_enough)
            
            # Quality weight: confidence * freshness * warmup * (1.0 if closed else 0.5)
            freshness_factor = max(0.1, 1.0 - (data_age_seconds / (tf_seconds * 4))) if tf_seconds > 0 else 1.0
            freshness_factor = min(1.0, freshness_factor)
            warmup_factor = 1.0 if is_warmed_up else 0.3
            closed_factor = 1.0 if bar_closed else 0.5
            quality_weight = sig.confidence * freshness_factor * warmup_factor * closed_factor
            
            tf_snapshots[tf] = TimeframeTrendSnapshot(
                symbol=self.symbol,
                timeframe=tf,
                timestamp=sig.timestamp,
                direction=sig.classification,
                score=sig.score,
                strength=strength_to_float(sig.strength),
                confidence=sig.confidence,
                data_quality=sig.data_quality,
                strategy_version=settings.trend.strategy_version,
                data_age_seconds=data_age_seconds,
                bar_closed=bar_closed,
                is_warmed_up=is_warmed_up,
                valid=valid,
                quality_weight=quality_weight,
            )

        # Aggregate metrics
        bullish_align, bearish_align, conflicting = (
            self._calculate_directional_alignment(timeframe_signals)
        )
        alignment_score = self._calculate_alignment(timeframe_signals, tf_snapshots)
        short_dir, inter_dir, higher_dir = self._calculate_horizon_directions(
            timeframe_signals,
        )
        direction, strength = self._calculate_overall_direction(timeframe_signals, tf_snapshots)

        # Compute valid coverage (fraction of preset TFs with valid signals)
        valid_count = sum(1 for s in tf_snapshots.values() if s.valid)
        valid_coverage = valid_count / len(self.analysis_timeframes) if self.analysis_timeframes else 0.0

        # Compute quality-weighted aggregate score
        quality_weighted_score = self._calculate_quality_weighted_score(tf_snapshots)

        return MultiTimeframeSnapshot(
            symbol=self.symbol,
            timestamp=ts,
            preset=self.preset_name,
            direction=direction,
            strength=strength,
            alignment_score=alignment_score,
            bullish_alignment=bullish_align,
            bearish_alignment=bearish_align,
            conflicting=conflicting,
            short_term_direction=short_dir,
            intermediate_direction=inter_dir,
            higher_direction=higher_dir,
            timeframe_snapshots=tf_snapshots,
            strategy_version=settings.trend.strategy_version,
            valid_coverage=valid_coverage,
            quality_weighted_score=quality_weighted_score,
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_current_confluence(self) -> ConfluenceSignal | None:
        """Get the current confluence signal - generates fresh on demand"""
        # Generate fresh confluence signal from current shared engine states
        self._generate_confluence_signal(datetime.now(timezone.utc))
        if self.confluence_history:
            return self.confluence_history[-1]
        return None

    def get_confluence_history(self, limit: int | None = None) -> list[ConfluenceSignal]:
        """Get confluence signal history"""
        if limit is None:
            return self.confluence_history.copy()
        return (self.confluence_history[-limit:]
                if len(self.confluence_history) > limit
                else self.confluence_history.copy())

    def get_current_snapshot(self) -> MultiTimeframeSnapshot | None:
        """Get the current MultiTimeframeSnapshot, or None if no data yet."""
        if self.snapshot_history:
            return self.snapshot_history[-1]
        return None

    def get_snapshot_history(
        self, limit: int | None = None,
    ) -> list[MultiTimeframeSnapshot]:
        """Get MultiTimeframeSnapshot history."""
        if limit is None:
            return self.snapshot_history.copy()
        return (self.snapshot_history[-limit:]
                if len(self.snapshot_history) > limit
                else self.snapshot_history.copy())

    def get_timeframe_trend(self, timeframe: Timeframe) -> TrendSignal | None:
        """Get current trend for a specific timeframe"""
        engine = self.trend_engines.get(timeframe)
        if engine:
            return engine.get_current_trend(timeframe)
        return None

    def get_all_timeframe_trends(self) -> dict[Timeframe, TrendSignal]:
        """Get current trends for all active timeframes"""
        trends: dict[Timeframe, TrendSignal] = {}
        for timeframe, engine in self.trend_engines.items():
            trend = engine.get_current_trend(timeframe)
            if trend:
                trends[timeframe] = trend
        return trends
