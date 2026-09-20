"""
Phase 17 — Controlled query & response schema for the NL search layer.

``NLFilters`` is what the AI is told to emit. Every field is constrained
by a ``Literal[...]`` or a typed range so the model can only produce
one of the allowed values; Pydantic rejects anything else. The
``_cross_check`` validator additionally enforces cross-field rules
(trend_min <= trend_max, etc.) and sanitises the ``signals`` list to a
known allowlist.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# --- Vocabulary -----------------------------------------------------

Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]
Direction = Literal["bullish", "bearish", "neutral"]
Ranking = Literal[
    "strongest_bullish",
    "strongest_bearish",
    "strongest_momentum",
    "biggest_improvement",
    "biggest_deterioration",
    "best_mtf_alignment",
    "strongest_relative_strength",
]
Transition = Literal["just_became_bullish", "just_became_bearish"]
Macd = Literal["bullish", "bearish"]
Scope = Literal["watchlist", "market"]


# Signal allowlist — the scanner generates exactly these names today
# (see ``Scanner._generate_signals``). Adding a new scanner signal
# requires adding it here in lock-step.
_KNOWN_SIGNALS: frozenset[str] = frozenset(
    {
        "RSI_OVERSOLD",
        "RSI_OVERBOUGHT",
        "MACD_BULLISH",
        "MACD_BEARISH",
        "MULTI_TIMEFRAME_BULLISH",
        "MULTI_TIMEFRAME_BEARISH",
        "HIGH_VOLUME",
        "VOLUME_SPIKE",
        "RSI_OVERSOLD_REVERSAL",
        "BREAKOUT",
        "BREAKDOWN",
        "VOLATILITY_CONTRACTION",
        "VOLATILITY_EXPANSION",
        "RELATIVE_STRENGTH_OUTPERFORMER",
        "RELATIVE_STRENGTH_UNDERPERFORMER",
    }
)


# --- Query schema ---------------------------------------------------


class NLFilters(BaseModel):
    """The controlled schema. The AI is told to produce ONLY this shape.

    All fields are optional. The router applies caller overrides
    (``top_n``, ``ranking``) AFTER the model is built so the AI never
    has to guess endpoint-level metadata.
    """

    # Timeframe-conditional direction filters
    timeframe: Timeframe | None = None
    direction: Direction | None = None
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # Score / volume / indicator thresholds
    trend_min: float | None = Field(default=None, ge=0, le=100)
    trend_max: float | None = Field(default=None, ge=0, le=100)
    relative_strength_min: float | None = Field(default=None, ge=0, le=100)
    volume_min: int | None = Field(default=None, ge=0)
    rsi_oversold_below: float | None = Field(default=None, ge=0, le=100)
    rsi_overbought_above: float | None = Field(default=None, ge=0, le=100)
    adx_strong_above: float | None = Field(default=None, ge=0)
    macd: Macd | None = None
    signals: list[str] = Field(default_factory=list, max_length=10)

    # Higher-level / pattern queries
    transition: Transition | None = None
    min_bullish_timeframes: int | None = Field(default=None, ge=0, le=12)
    mtf_conflict: bool | None = None  # e.g. bullish daily but bearish 5m
    outperforms: str | None = None  # e.g. "QQQ"
    spy_bearish_while_stock_bullish: bool | None = None

    # Result selection
    ranking: Ranking = "strongest_bullish"
    top_n: int = Field(default=10, ge=1, le=50)
    match_all: bool = False  # graceful fallback when no rule fired
    scope: Scope = "watchlist"

    @model_validator(mode="after")
    def _cross_check(self) -> NLFilters:
        if (
            self.trend_min is not None
            and self.trend_max is not None
            and self.trend_min > self.trend_max
        ):
            raise ValueError("trend_min must be <= trend_max")
        if (
            self.rsi_oversold_below is not None
            and self.rsi_overbought_above is not None
            and self.rsi_oversold_below >= self.rsi_overbought_above
        ):
            raise ValueError(
                "rsi_oversold_below must be < rsi_overbought_above"
            )
        # Sanitise signals: upper-case, dedupe, cap to the known set.
        if self.signals:
            cleaned: list[str] = []
            seen: set[str] = set()
            for s in self.signals:
                u = s.strip().upper()
                if u and u in _KNOWN_SIGNALS and u not in seen:
                    seen.add(u)
                    cleaned.append(u)
            self.signals = cleaned
        # Upper-case outperforms benchmark.
        if self.outperforms is not None:
            self.outperforms = self.outperforms.strip().upper()
        return self


# --- Response models -----------------------------------------------


class ScannedResultItem(BaseModel):
    """A single symbol returned by an NL search, serialised for the UI.

    A trimmed view of :class:`backend.scanner.scanner.ScanResult` —
    the fields a trader needs to scan a result list, not a full scan
    payload.
    """

    symbol: str
    total_score: float
    rank: int | None
    signals: list[str]
    # brief snapshot
    trend_directions: dict[str, str] = Field(default_factory=dict)
    rsi: float | None = None
    macd: float | None = None
    adx: float | None = None
    price: float | None = None


class NLSearchResponse(BaseModel):
    """The full response from ``POST /api/nl-search``."""

    query: str
    filter_schema: NLFilters = Field(alias="schema")
    filter_description: str
    results: list[ScannedResultItem]
    ranking: str
    explanation: str | None = None
    ai_explanation_used: bool
    ai_translation_used: bool
    reason: str | None = None
    parser_used: str = "rules"  # "ai" | "rules" | "default"
    timestamp: str


__all__ = [
    "Direction",
    "Macd",
    "NLFilters",
    "NLSearchResponse",
    "Ranking",
    "ScannedResultItem",
    "Scope",
    "Timeframe",
    "Transition",
]


# Suppress an unused-import warning on Any — kept here for the type
# hints to render correctly in IDEs.
_ = Any
