"""
Trend transition detection engine.

Detects meaningful *transitions* in a trend score series, not the
direction of any single signal. A transition is the delta between two
points in a score time-series, classified into one of six categories
per the spec:

- BULLISH_ACCELERATION  : bullish score rising faster than expected
- BULLISH_WEAKENING     : bullish score falling but still positive
- BEARISH_ACCELERATION  : bearish score falling faster than expected
- BEARISH_WEAKENING     : bearish score rising (toward zero) but still negative
- BULLISH_REVERSAL      : score crosses from negative to positive
- BEARISH_REVERSAL      : score crosses from positive to negative

The engine is purely a *consumer* of a numeric score series; it does
not touch the trend engine or indicators. It can be fed by a
``TrendEngine.trend_history`` list of ``TrendSignal`` objects (the
``score`` field is used), or any other ordered list of numeric
scores paired with timestamps.

Historical-only: at every index ``i`` the engine uses only data points
``<= i``. The ``window`` and ``min_delta`` parameters are configurable
so callers can tune the sensitivity per timeframe or use case.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TransitionType(StrEnum):
    """The six spec-defined transition types."""
    BULLISH_ACCELERATION = "bullish_acceleration"
    BULLISH_WEAKENING = "bullish_weakening"
    BEARISH_ACCELERATION = "bearish_acceleration"
    BEARISH_WEAKENING = "bearish_weakening"
    BULLISH_REVERSAL = "bullish_reversal"
    BEARISH_REVERSAL = "bearish_reversal"


class TransitionDirection(StrEnum):
    """Coarse-grained direction bucket; useful for ranking/filtering."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


# Map each TransitionType to its direction bucket
_TYPE_TO_DIRECTION: dict[TransitionType, TransitionDirection] = {
    TransitionType.BULLISH_ACCELERATION: TransitionDirection.BULLISH,
    TransitionType.BULLISH_WEAKENING: TransitionDirection.BULLISH,
    TransitionType.BEARISH_ACCELERATION: TransitionDirection.BEARISH,
    TransitionType.BEARISH_WEAKENING: TransitionDirection.BEARISH,
    TransitionType.BULLISH_REVERSAL: TransitionDirection.BULLISH,
    TransitionType.BEARISH_REVERSAL: TransitionDirection.BEARISH,
}


@dataclass(frozen=True)
class TrendTransition:
    """A single detected transition.

    ``previous_score`` is the score at ``index - window``; ``current_score``
    is the score at ``index``. ``magnitude`` is the absolute score delta
    in score units (i.e. the difference of two values in the -100..+100
    range, so magnitudes can exceed 100 in theory but in practice are
    bounded by the score range).
    """
    type: TransitionType
    direction: TransitionDirection
    symbol: str
    timeframe: str
    index: int
    timestamp: datetime | None
    previous_score: float
    current_score: float
    delta: float
    magnitude: float

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "direction": self.direction.value,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "index": self.index,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "previous_score": self.previous_score,
            "current_score": self.current_score,
            "delta": self.delta,
            "magnitude": self.magnitude,
        }


class TrendTransitionEngine:
    """Detects trend transitions from a numeric score series.

    Parameters
    ----------
    window:
        How many points of history to look back when computing the
        delta. A larger window produces fewer, more meaningful
        transitions; a smaller window fires more often but noisier.
    min_delta:
        Minimum absolute score delta required to register a
        transition. Filters out the small wiggles that would
        otherwise fire on every bar.
    reversal_threshold:
        Score must cross zero by at least this much on both sides
        before a reversal is reported. The default 0 means any
        sign change counts. Setting it to 10, for example, requires
        the score to dip to at most -10 then rise to at least +10.
    """

    def __init__(
        self,
        window: int = 5,
        min_delta: float = 10.0,
        reversal_threshold: float = 0.0,
    ) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        if min_delta < 0:
            raise ValueError("min_delta must be >= 0")
        self.window = window
        self.min_delta = min_delta
        self.reversal_threshold = reversal_threshold

    # --- public API ---

    def detect(
        self,
        scores: Sequence[float],
        timestamps: Sequence[datetime] | None = None,
        symbol: str = "",
        timeframe: str = "",
    ) -> list[TrendTransition]:
        """Detect transitions in ``scores``.

        ``scores`` is an ordered series; ``timestamps`` (optional) must
        be the same length and index-aligned. ``symbol`` and
        ``timeframe`` are simply stamped onto the produced transitions.
        """
        n = len(scores)
        if n <= self.window:
            return []
        if timestamps is not None and len(timestamps) != n:
            raise ValueError("timestamps must align 1:1 with scores")

        transitions: list[TrendTransition] = []
        for i in range(self.window, n):
            prev = float(scores[i - self.window])
            curr = float(scores[i])
            delta = curr - prev
            if abs(delta) < self.min_delta:
                continue
            ttype = self._classify(prev, curr, delta)
            if ttype is None:
                continue
            transitions.append(
                TrendTransition(
                    type=ttype,
                    direction=_TYPE_TO_DIRECTION[ttype],
                    symbol=symbol,
                    timeframe=timeframe,
                    index=i,
                    timestamp=timestamps[i] if timestamps is not None else None,
                    previous_score=prev,
                    current_score=curr,
                    delta=delta,
                    magnitude=abs(delta),
                )
            )
        return transitions

    def latest(
        self,
        scores: Sequence[float],
        timestamps: Sequence[datetime] | None = None,
        symbol: str = "",
        timeframe: str = "",
    ) -> TrendTransition | None:
        """Return the most recent transition, or ``None``.

        "Most recent" is resolved by timestamp when ``timestamps`` are
        supplied (max timestamp wins). Without timestamps the engine has
        no notion of time, so the chronologically-last emitted transition
        (highest index, which the router presents newest-first) is used.
        """
        all_t = self.detect(scores, timestamps, symbol, timeframe)
        if not all_t:
            return None
        if timestamps is not None:
            return max(all_t, key=lambda t: t.timestamp or datetime.min)
        return all_t[-1]

    # --- internals ---

    def _classify(
        self, prev: float, curr: float, delta: float
    ) -> TransitionType | None:
        """Classify a (prev, curr) score pair into a transition type.

        Returns ``None`` if the pair does not represent a meaningful
        transition under current thresholds.
        """
        # Reversals: the score crosses zero with a strict sign change on both
        # sides (below -reversal_threshold then above +reversal_threshold, or
        # vice versa). Zero is *neutral*: a series that merely touches zero
        # (prev=0 -> curr>0, or prev>0 -> curr=0) is NOT a reversal, so we
        # use strict inequalities and leave exactly-zero as non-reversal.
        if prev < -self.reversal_threshold and curr > self.reversal_threshold:
            # Differentiate bullish vs bearish reversal by the sign of the
            # *delta* (a bullish reversal is rising into positive; a bearish
            # reversal is falling into negative).
            return (
                TransitionType.BULLISH_REVERSAL
                if delta > 0
                else TransitionType.BEARISH_REVERSAL
            )
        if prev > self.reversal_threshold and curr < -self.reversal_threshold:
            return TransitionType.BEARISH_REVERSAL

        # Acceleration / weakening: same-sign pair, magnitude delta large enough.
        if prev > 0 and curr > 0:
            return (
                TransitionType.BULLISH_ACCELERATION
                if delta > 0
                else TransitionType.BULLISH_WEAKENING
            )
        if prev < 0 and curr < 0:
            return (
                TransitionType.BEARISH_ACCELERATION
                if delta < 0
                else TransitionType.BEARISH_WEAKENING
            )
        # Mixed sign without crossing threshold: not a clean transition.
        return None


__all__ = [
    "TransitionType",
    "TransitionDirection",
    "TrendTransition",
    "TrendTransitionEngine",
]
