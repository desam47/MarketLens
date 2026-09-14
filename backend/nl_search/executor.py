"""
Phase 17 — Execute a validated ``NLFilters`` against the scanner cache.

``execute_query`` is the single public entry point. It:

1. Resolves the universe (watchlist symbols or existing cache).
2. Builds a ``Filter`` expression from the schema.
3. Applies the filter to the scanner cache.
4. Ranks the surviving results via the ``RankingEngine``.
5. Serialises each surviving result into a ``ScannedResultItem``.
6. Returns the full matched list (for counting) and the ranked slice.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.ai.sync_bridge import run_sync
from backend.repositories.watchlist_repository import WatchlistRepository
from backend.scanner.filters import (
    ADXStrong,
    AndFilter,
    Filter,
    HighVolume,
    MACDBearish,
    MACDBullish,
    MinTimeframeBullish,
    RSIOverbought,
    RSIOversold,
    SignalPresent,
    TimeframeDirection,
    TrendScoreGt,
    TrendScoreLt,
    TrueFilter,
)
from backend.scanner.ranking import RankedEntry, RankingEngine, default_ranking_engine
from backend.scanner.scanner import ScanResult, market_scanner

if TYPE_CHECKING:
    pass

from .schema import NLFilters, ScannedResultItem

logger = logging.getLogger(__name__)


# --- Custom filters not in the registry ------------------------------


class OutperformsBenchmark(Filter):
    """True when the symbol's relative strength vs benchmark exceeds min_pct."""

    name = "outperforms_benchmark"

    def __init__(self, benchmark: str, min_pct: float = 0.0):
        self.benchmark = benchmark.upper()
        self.min_pct = min_pct

    def matches(self, result: ScanResult) -> bool:
        rs = result.indicator_values.get(f"rs_pct_{self.benchmark}")
        if rs is None:
            return False
        return rs > self.min_pct

    def describe(self) -> str:
        return f"RS({self.benchmark}) > {self.min_pct:.1f}%"


class JustTransitionedFilter(Filter):
    """True when the most recent trend transition matches ``expected``."""

    name = "just_transitioned"

    def __init__(
        self,
        expected: str,  # Literal["just_became_bullish","just_became_bearish"]
    ):
        self.expected = expected

    def matches(self, result: ScanResult) -> bool:
        # Fixed 2026-09-09: this used to import a `trend_transition_engine`
        # singleton and call `.get_history(symbol=...)` on it — neither
        # exists (see backend.ai.context.build_context's identical fix,
        # same root cause). The broad except silently made this filter
        # always return False. Now uses the engine as designed: pull the
        # already-warmed TrendEngine's own score history and detect the
        # latest transition on it directly.
        try:
            from backend.api.trend.registry import get_engine as get_trend_engine
            from backend.engines.timeframe import Timeframe
            from backend.transitions.trend_transition_engine import (
                TrendTransitionEngine,
            )

            hist = get_trend_engine(result.symbol).trend_history.get(Timeframe.ONE_DAY, [])
            if len(hist) <= 6:
                return False
            scores = [s.score for s in hist]
            timestamps = [s.timestamp for s in hist]
            t = TrendTransitionEngine(window=5, min_delta=10.0).latest(
                scores, timestamps=timestamps, symbol=result.symbol, timeframe="1d"
            )
        except Exception:  # noqa: BLE001
            return False
        if t is None:
            return False
        if self.expected == "just_became_bullish":
            return t.direction.value in ("bullish",) and t.type.value in (
                "bullish_reversal",
                "bullish_acceleration",
            )
        return t.direction.value == "bearish" and t.type.value in (
            "bearish_reversal",
            "bearish_acceleration",
        )

    def describe(self) -> str:
        return f"recently {self.expected.replace('_', ' ')}"


# --- Execution result ------------------------------------------------


@dataclass
class ExecutionResult:
    """The result of executing an NL query."""

    matched_all: list[ScanResult]  # all symbols that passed the filter
    top_n: list[ScannedResultItem]  # ranked slice
    filter_description: str
    universe_size: int
    matched_count: int


# --- Helpers --------------------------------------------------------


def _resolve_watchlist_symbols(
    watchlist_id: int | None,
    db,  # Session | None handled below
) -> list[str]:
    """Return the list of enabled symbols for the active watchlist."""

    if db is None:
        from backend.database import SessionLocal

        db = SessionLocal()
        close = True
    else:
        close = False

    try:
        repo = WatchlistRepository(db)
        if watchlist_id is not None:
            watchlists = [repo.get_watchlist(watchlist_id)]
        else:
            watchlists = repo.get_watchlists(active_only=True)

        if not watchlists:
            return []

        wl = next((w for w in watchlists if w is not None), None)
        if wl is None:
            return []

        symbols = [
            ws.symbol
            for ws in repo.get_watchlist_symbols(wl.id, enabled_only=True)
        ]
        return symbols
    finally:
        if close:
            db.close()


def _build_filter(
    f: NLFilters,
    *,
    extras: dict | None = None,
) -> tuple[Filter, str]:
    """Translate ``NLFilters`` into a ``Filter`` expression.

    Returns ``(filter, human_readable_description)``.
    """
    parts: list[Filter] = []
    descriptions: list[str] = []

    def add(filt: Filter, desc: str) -> None:
        parts.append(filt)
        descriptions.append(desc)

    # Normalise NLFilters direction ("bullish"/"bearish") to scanner values
    # ("uptrend"/"downtrend") for TimeframeDirection.
    _DIR_MAP = {"bullish": "uptrend", "bearish": "downtrend"}
    _SCANNER_DIR = _DIR_MAP.get(f.direction, f.direction) if f.direction else None

    # --- Timeframe + direction ---
    if f.timeframe is not None and f.direction is not None:
        tf_dir = TimeframeDirection(
            f.timeframe, _SCANNER_DIR, min_confidence=f.min_confidence
        )
        add(tf_dir, f"{f.timeframe}={f.direction} (conf≥{f.min_confidence})")
    elif f.direction is not None:
        # Direction without a specific timeframe — default to daily.
        tf_dir = TimeframeDirection(
            "1d", _SCANNER_DIR, min_confidence=f.min_confidence
        )
        add(tf_dir, f"1d={f.direction} (conf≥{f.min_confidence})")

    # --- Trend score ---
    if f.trend_min is not None and f.trend_max is not None:
        add(
            AndFilter([TrendScoreGt(f.trend_min - 1e-9), TrendScoreLt(f.trend_max + 1e-9)]),
            f"trend ∈ [{f.trend_min}, {f.trend_max}]",
        )
    elif f.trend_min is not None:
        add(TrendScoreGt(f.trend_min - 1e-9), f"trend > {f.trend_min}")
    elif f.trend_max is not None:
        add(TrendScoreLt(f.trend_max + 1e-9), f"trend < {f.trend_max}")

    # --- Relative strength ---
    if f.relative_strength_min is not None and f.outperforms:
        add(
            OutperformsBenchmark(f.outperforms, min_pct=f.relative_strength_min),
            f"RS({f.outperforms}) > {f.relative_strength_min}%",
        )
    elif f.outperforms:
        add(
            OutperformsBenchmark(f.outperforms),
            f"outperforms {f.outperforms}",
        )

    # --- Volume ---
    if f.volume_min is not None:
        add(HighVolume(f.volume_min), f"volume ≥ {f.volume_min:,}")

    # --- RSI ---
    if f.rsi_oversold_below is not None:
        add(RSIOversold(f.rsi_oversold_below), f"RSI < {f.rsi_oversold_below}")
    if f.rsi_overbought_above is not None:
        add(RSIOverbought(f.rsi_overbought_above), f"RSI > {f.rsi_overbought_above}")

    # --- ADX ---
    if f.adx_strong_above is not None:
        add(ADXStrong(f.adx_strong_above), f"ADX > {f.adx_strong_above}")

    # --- MACD ---
    if f.macd == "bullish":
        add(MACDBullish(), "MACD bullish")
    elif f.macd == "bearish":
        add(MACDBearish(), "MACD bearish")

    # --- Signals ---
    if f.signals:
        add(SignalPresent(f.signals), f"signals in {f.signals}")

    # --- Multi-TF ---
    if f.min_bullish_timeframes is not None:
        add(
            MinTimeframeBullish(f.min_bullish_timeframes, f.min_confidence),
            f"≥{f.min_bullish_timeframes} bullish TFs",
        )

    # --- Transition ---
    if f.transition is not None:
        add(JustTransitionedFilter(f.transition), f"recently {f.transition}")

    # --- Cross-TF conflict ---
    if f.mtf_conflict and extras and "conflict" in extras:
        c = extras["conflict"]
        tf_conflict = c.get("timeframe", "1h")
        dir_conflict_raw = c.get("direction", "bearish")
        dir_conflict = _DIR_MAP.get(dir_conflict_raw, dir_conflict_raw)
        add(
            TimeframeDirection(tf_conflict, dir_conflict, min_confidence=0.5),
            f"{tf_conflict}={dir_conflict_raw}",
        )

    # --- SPY bearish while stock bullish ---
    if f.spy_bearish_while_stock_bullish:
        # The scanner cache is expected to contain a SPY scan result from
        # previous calls. If it doesn't, this filter always returns False.
        add(
            _SPYFilter(),
            "stock bullish while SPY bearish on daily",
        )

    # --- match_all fallback ---
    if not parts:
        # Found live earlier this session: DailyBullish(min_confidence=-1.0)
        # disables the confidence floor but TimeframeDirection.matches()
        # still hardcodes direction == "uptrend" — so "match all" was
        # silently excluding every non-uptrending-daily symbol instead of
        # actually matching everything. TrueFilter has no direction check
        # at all — a genuine match-all.
        add(TrueFilter(), "(match all)")

    description = " AND ".join(f"({d})" for d in descriptions)
    if not description:
        description = "(match all)"

    return AndFilter(parts), description


class _SPYFilter(Filter):
    """True when SPY's daily trend is bearish (for SPY-vs-stock queries)."""

    def matches(self, result: ScanResult) -> bool:
        spy = market_scanner.get_scan_result("SPY")
        if spy is None:
            return False
        daily = spy.trend_signals.get("ONE_DAY", {})
        return daily.get("direction", "").lower() == "downtrend"

    def describe(self) -> str:
        return "SPY daily = downtrend"


def _scan_result_to_item(r: ScanResult) -> ScannedResultItem:
    """Convert a ScanResult to a ScannedResultItem."""
    # Pull the per-TF direction snapshot.
    trend_dirs: dict[str, str] = {}
    for tf_key, sig in r.trend_signals.items():
        trend_dirs[tf_key] = sig.get("direction", "unknown")

    return ScannedResultItem(
        symbol=r.symbol,
        total_score=round(r.calculate_total_score(), 4),
        rank=r.rank,
        signals=list(r.signals or []),
        trend_directions=trend_dirs,
        rsi=r.indicator_values.get("rsi"),
        macd=r.indicator_values.get("macd"),
        adx=r.indicator_values.get("adx"),
        price=(
            r.indicator_values.get("price")
            or (r.quote.price if r.quote else None)
        ),
    )


# --- Public API -----------------------------------------------------


def execute_query(
    f: NLFilters,
    *,
    extras: dict | None = None,
    watchlist_id: int | None = None,
    db=None,  # Session | None
    ranking_engine: RankingEngine | None = None,
) -> ExecutionResult:
    """Execute ``NLFilters`` against the scanner and return ranked results.

    This function never raises. Expected failures (empty universe,
    empty cache) are surfaced in the returned ``ExecutionResult`` so the
    endpoint can return a clean 200 response with an explanatory
    ``reason`` field.
    """
    engine = ranking_engine or default_ranking_engine

    # --- Resolve universe ---
    if f.scope == "watchlist":
        symbols = _resolve_watchlist_symbols(watchlist_id, db)
    else:
        # "market" scope: operate on whatever is already in the cache.
        symbols = []

    # --- Warm the scanner cache ---
    # scan_symbols is async; this runs in a loop-less worker thread
    # (the nl-search router pushes execute_query through
    # asyncio.to_thread) — bridge with run_sync.
    if symbols:
        run_sync(market_scanner.scan_symbols(symbols))

    cache = list(market_scanner.scan_results.values())

    # --- Build filter ---
    filt, description = _build_filter(f, extras=extras)

    # --- Apply ---
    matched = [r for r in cache if filt.matches(r)]

    # --- Rank ---
    if not matched:
        return ExecutionResult(
            matched_all=[],
            top_n=[],
            filter_description=description,
            universe_size=len(cache),
            matched_count=0,
        )

    ranked = engine.rank_one(f.ranking, matched, top_n=f.top_n, filter=None)
    if ranked is None:
        # Ranking category not found — sort by total score as fallback.
        ranked = engine.rank_one("strongest_bullish", matched, top_n=f.top_n, filter=None)

    # Build a symbol → ScanResult lookup once and reuse it.
    symbol_to_result: dict[str, ScanResult] = {r.symbol: r for r in cache}

    if ranked is None:
        ordered: list[RankedEntry] = []
    else:
        ordered = list(ranked.entries)

    top_items: list[ScannedResultItem] = []
    for entry in ordered[: f.top_n]:
        sr = symbol_to_result.get(entry.symbol)
        if sr is not None:
            top_items.append(_scan_result_to_item(sr))

    return ExecutionResult(
        matched_all=matched,
        top_n=top_items,
        filter_description=description,
        universe_size=len(cache),
        matched_count=len(matched),
    )


__all__ = [
    "execute_query",
    "ExecutionResult",
    "JustTransitionedFilter",
    "OutperformsBenchmark",
]
