"""
Ranking engine — produces named rankings of scanner results.

The original ``Scanner.rank_symbols()`` only emits a single
``[(symbol, score), ...]`` list. The phase 10 spec wants a richer
output: instead of a single leaderboard, the scanner should produce
**named categories** that surface what the numbers actually mean.

The categories defined here are:

- ``strongest_bullish`` — top N by total_score
- ``strongest_bearish`` — bottom N by total_score
- ``strongest_momentum`` — top N by |MACD histogram| * trend confidence
- ``biggest_improvement`` — top N by trend_strength score (high-ADX uptrends)
- ``biggest_deterioration`` — bottom N by trend_strength score
- ``best_mtf_alignment`` — top N by multi-timeframe bullish agreement
- ``strongest_relative_strength`` — top N by ADX-confirmed uptrend confidence

Each category can optionally be filtered before ranking so a caller
can answer questions like "strongest bullish among daily-bullish
symbols only".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.scanner.filters import Filter
    from backend.scanner.scanner import ScanResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RankedEntry:
    """A single entry in a named ranking."""

    symbol: str
    score: float
    rank: int
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class NamedRanking:
    """A named ranking category: a list of entries plus a description."""

    name: str
    label: str
    description: str
    entries: list[RankedEntry]
    total_eligible: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "total_eligible": self.total_eligible,
            "entries": [
                {
                    "symbol": e.symbol,
                    "score": round(e.score, 4),
                    "rank": e.rank,
                    "metrics": {k: round(v, 4) if isinstance(v, float) else v
                                for k, v in e.metrics.items()},
                }
                for e in self.entries
            ],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _top_n(entries: list[RankedEntry], n: int) -> list[RankedEntry]:
    """Re-assign ranks 1..min(n, len) and return the top N."""
    if n <= 0:
        return []
    top = entries[:n]
    for i, entry in enumerate(top, start=1):
        entry.rank = i
    return top


def _mtf_bullish_count(result: ScanResult) -> int:
    """Count how many timeframes are bullish with confidence ≥ 0.5."""
    count = 0
    for sig in result.trend_signals.values():
        if (
            sig.get("direction", "").lower() == "uptrend"
            and sig.get("confidence", 0.0) >= 0.5
        ):
            count += 1
    return count


def _mtf_bearish_count(result: ScanResult) -> int:
    count = 0
    for sig in result.trend_signals.values():
        if (
            sig.get("direction", "").lower() == "downtrend"
            and sig.get("confidence", 0.0) >= 0.5
        ):
            count += 1
    return count


def _total_trend_confidence(result: ScanResult) -> float:
    """Sum of confidence across all bullish timeframes."""
    return sum(
        sig.get("confidence", 0.0)
        for sig in result.trend_signals.values()
        if sig.get("direction", "").lower() == "uptrend"
    )


def _total_bearish_confidence(result: ScanResult) -> float:
    return sum(
        sig.get("confidence", 0.0)
        for sig in result.trend_signals.values()
        if sig.get("direction", "").lower() == "downtrend"
    )


# ---------------------------------------------------------------------------
# Ranking engine
# ---------------------------------------------------------------------------

class RankingEngine:
    """
    Produces named rankings of :class:`ScanResult` objects.

    Construction is parameterless; the public entry point is
    :meth:`rank`. Results can be filtered before ranking via the
    ``filter`` argument (any :class:`Filter` instance from
    ``backend.scanner.filters``).
    """

    CATEGORIES: list[dict[str, str]] = [
        {
            "name": "strongest_bullish",
            "label": "Strongest Bullish",
            "description": "Symbols with the highest composite bullish score across all factors.",
        },
        {
            "name": "strongest_bearish",
            "label": "Strongest Bearish",
            "description": "Symbols with the lowest composite score — the most bearish set-up.",
        },
        {
            "name": "strongest_momentum",
            "label": "Strongest Momentum",
            "description": "Symbols with the strongest MACD-based momentum signal.",
        },
        {
            "name": "biggest_improvement",
            "label": "Biggest Improvement",
            "description": "Highest trend-strength score — strong ADX with bullish bias.",
        },
        {
            "name": "biggest_deterioration",
            "label": "Biggest Deterioration",
            "description": "Lowest trend-strength score — weak or breakdown-prone trends.",
        },
        {
            "name": "best_mtf_alignment",
            "label": "Best Multi-Timeframe Alignment",
            "description": "Symbols where the most timeframes agree on direction.",
        },
        {
            "name": "strongest_relative_strength",
            "label": "Strongest Relative Strength",
            "description": "Symbols with the most total bullish-trend confidence (proxy for relative strength).",
        },
    ]

    def __init__(self) -> None:
        self.last_result: dict[str, NamedRanking] = {}

    # ---- Internal per-category builders --------------------------------

    # Weight map used only for the directional rankings (strongest_bullish /
    # strongest_bearish). Magnitude-only scores (trend_strength, adx, volatility,
    # volume) are zeroed out because they don't carry direction — a strong
    # downtrend and a strong uptrend both have high trend_strength, so including
    # them washes out the directional signal from momentum/macd/rsi.
    _DIRECTIONAL_WEIGHTS: dict[str, float] = {
        "momentum": 1.0,
        "macd": 1.0,
        "rsi": 1.0,
        # zero out magnitude-only scores:
        "trend_strength": 0.0,
        "adx": 0.0,
        "volatility": 0.0,
        "volume": 0.0,
    }

    def _directional_score(self, r: ScanResult) -> float:
        """Average of direction-significant scores only.

        Positive = bullish (rising momentum, oversold RSI), negative = bearish.
        Magnitude-only scores (trend_strength, adx, volatility, volume) are
        excluded to avoid diluting the directional signal.
        """
        w = self._DIRECTIONAL_WEIGHTS
        active = {k: v for k, v in r.scores.items() if k in w}
        if not active:
            return 0.0
        total_w = sum(w.get(k, 0.0) for k in active)
        if total_w == 0:
            return 0.0
        return sum(active[k] * w.get(k, 0.0) for k in active) / total_w

    def _build_strongest_bullish(
        self, results: list[ScanResult], top_n: int
    ) -> tuple[list[RankedEntry], int]:
        scored = []
        for r in results:
            total = self._directional_score(r)
            scored.append(
                RankedEntry(
                    symbol=r.symbol,
                    score=total,
                    rank=0,
                    metrics={
                        "directional_score": total,
                        "trend_strength": r.scores.get("trend_strength", 0.0),
                        "momentum": r.scores.get("momentum", 0.0),
                        "macd": r.scores.get("macd", 0.0),
                        "rsi": r.scores.get("rsi", 0.0),
                    },
                )
            )
        scored.sort(key=lambda e: e.score, reverse=True)
        return _top_n(scored, top_n), len(results)

    def _build_strongest_bearish(
        self, results: list[ScanResult], top_n: int
    ) -> tuple[list[RankedEntry], int]:
        scored = []
        eligible_count = 0
        for r in results:
            total = self._directional_score(r)
            # Only include genuinely bearish (negative) entries.
            if total >= 0:
                continue
            eligible_count += 1
            scored.append(
                RankedEntry(
                    symbol=r.symbol,
                    score=total,
                    rank=0,
                    metrics={"directional_score": total},
                )
            )
        scored.sort(key=lambda e: e.score)
        return _top_n(scored, top_n), eligible_count

    def _build_strongest_momentum(
        self, results: list[ScanResult], top_n: int
    ) -> tuple[list[RankedEntry], int]:
        scored = []
        for r in results:
            macd = r.indicator_values.get("macd")
            momentum = r.scores.get("momentum", 0.0)
            score = momentum if macd is not None else 0.0
            scored.append(
                RankedEntry(
                    symbol=r.symbol,
                    score=score,
                    rank=0,
                    metrics={"momentum": momentum, "macd": macd or 0.0},
                )
            )
        scored.sort(key=lambda e: e.score, reverse=True)
        return _top_n(scored, top_n), len(results)

    def _build_biggest_improvement(
        self, results: list[ScanResult], top_n: int
    ) -> tuple[list[RankedEntry], int]:
        scored = []
        eligible_count = 0
        for r in results:
            # Improvement = Strong trend (high trend_strength) AND bullish bias.
            # Directional score > 0 confirms bullishness.
            strength = r.scores.get("trend_strength", 0.0)
            directional = self._directional_score(r)

            if directional <= 0:
                continue
            eligible_count += 1

            score = strength * (1 + directional)
            scored.append(
                RankedEntry(
                    symbol=r.symbol,
                    score=score,
                    rank=0,
                    metrics={
                        "trend_strength": strength,
                        "directional_score": directional,
                        "adx": r.indicator_values.get("adx") or 0.0
                    },
                )
            )
        scored.sort(key=lambda e: e.score, reverse=True)
        return _top_n(scored, top_n), eligible_count

    def _build_biggest_deterioration(
        self, results: list[ScanResult], top_n: int
    ) -> tuple[list[RankedEntry], int]:
        scored = []
        eligible_count = 0
        for r in results:
            # Deterioration = Strong trend (high trend_strength) AND bearish bias.
            # Directional score < 0 confirms bearishness.
            strength = r.scores.get("trend_strength", 0.0)
            directional = self._directional_score(r)

            if directional >= 0:
                continue
            eligible_count += 1

            # Use absolute value of directional score to scale the strength of deterioration.
            score = strength * (1 + abs(directional))
            scored.append(
                RankedEntry(
                    symbol=r.symbol,
                    score=score,
                    rank=0,
                    metrics={
                        "trend_strength": strength,
                        "directional_score": directional
                    },
                )
            )
        scored.sort(key=lambda e: e.score, reverse=True)
        return _top_n(scored, top_n), eligible_count

    def _build_best_mtf_alignment(
        self, results: list[ScanResult], top_n: int
    ) -> tuple[list[RankedEntry], int]:
        scored = []
        for r in results:
            bull = _mtf_bullish_count(r)
            bear = _mtf_bearish_count(r)
            # Use signed alignment: positive = bullish, negative = bearish.
            signed = bull - bear
            scored.append(
                RankedEntry(
                    symbol=r.symbol,
                    score=float(signed),
                    rank=0,
                    metrics={"bullish_timeframes": bull, "bearish_timeframes": bear},
                )
            )
        scored.sort(key=lambda e: e.score, reverse=True)
        return _top_n(scored, top_n), len(results)

    def _build_strongest_relative_strength(
        self, results: list[ScanResult], top_n: int
    ) -> tuple[list[RankedEntry], int]:
        scored = []
        for r in results:
            score = _total_trend_confidence(r)
            scored.append(
                RankedEntry(
                    symbol=r.symbol,
                    score=score,
                    rank=0,
                    metrics={"bullish_confidence_total": score},
                )
            )
        scored.sort(key=lambda e: e.score, reverse=True)
        return _top_n(scored, top_n), len(results)

    # ---- Public entry point -------------------------------------------

    def rank(
        self,
        results: list[ScanResult],
        top_n: int = 10,
        filter: Filter | None = None,
    ) -> dict[str, NamedRanking]:
        """
        Build all named rankings from a list of scan results.

        ``filter``, if provided, restricts the candidate set before
        ranking (e.g. only daily-bullish symbols).

        Returns a dict keyed by category name (``strongest_bullish``,
        ``strongest_momentum``, …).
        """
        if filter is not None:
            candidates = [r for r in results if filter.matches(r)]
        else:
            candidates = list(results)

        out: dict[str, NamedRanking] = {}
        builders = {
            "strongest_bullish": self._build_strongest_bullish,
            "strongest_bearish": self._build_strongest_bearish,
            "strongest_momentum": self._build_strongest_momentum,
            "biggest_improvement": self._build_biggest_improvement,
            "biggest_deterioration": self._build_biggest_deterioration,
            "best_mtf_alignment": self._build_best_mtf_alignment,
            "strongest_relative_strength": self._build_strongest_relative_strength,
        }

        for meta in self.CATEGORIES:
            name = meta["name"]
            builder = builders.get(name)
            if builder is None:
                continue

            # Some builders now return (entries, eligible_count) to support
            # per-category filtering (e.g. bearish guard).
            result = builder(candidates, top_n)
            if isinstance(result, tuple):
                entries, eligible_count = result
            else:
                entries, eligible_count = result, len(candidates)

            out[name] = NamedRanking(
                name=name,
                label=meta["label"],
                description=meta["description"],
                entries=entries,
                total_eligible=eligible_count,
            )

        self.last_result = out
        return out

    def rank_one(
        self,
        category: str,
        results: list[ScanResult],
        top_n: int = 10,
        filter: Filter | None = None,
    ) -> NamedRanking | None:
        """Build a single named ranking by name (returns ``None`` if unknown)."""
        full = self.rank(results, top_n=top_n, filter=filter)
        return full.get(category)


# Default instance used by the scanner router
default_ranking_engine = RankingEngine()
