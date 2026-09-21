"""
Phase 16 — Structured context builder for the AI analysis prompt.

The context is a JSON-serialisable dict that captures everything the
quantitative engine already knows about a symbol. Per the spec:

> AI must NEVER directly calculate raw indicators if the application
> already has the calculation. Instead send structured context.

The builder reads from the existing engine singletons and bundles
the data the prompt template will turn into a natural-language
question. If any of the required components are unavailable (e.g.
a cold-start symbol with no bars), ``build_context()`` raises
``InsufficientDataError`` so the caller can return an
``UncertaintyResponse`` rather than asking the AI to hallucinate.

Performance
-----------
The ~12 independent sub-engines used to run strictly sequentially
(~14 sub-engine calls per symbol), so a slow HTTP aux-data call or a
200-bar SR/divergence pass added its full latency on top of every
other one. The sub-engine calls now run concurrently on a small thread
pool:

- the I/O-bound ones (news, fundamentals, tape, DB lookups) overlap
  with the CPU-bound ones (regime, RS, sector, SR, divergence), and
- the numpy-heavy engines release the GIL during their C-level
  computation, so that CPU work parallelises too.

Each sub-engine source is isolated in its own ``_xxx_context`` helper
with a broad ``except`` that degrades to an empty/{}/[] result —
exactly the "one broken sub-source degrades the whole context"
convention the original sequential code used — so moving them onto
worker threads doesn't weaken that resiliency boundary.

Only the single-symbol scan runs on the caller's thread: everything
else derives from it, and the scan is the cheapest, most-cacheable
call.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.scanner.scanner import ScanResult, market_scanner

logger = logging.getLogger(__name__)

# Sized for TWO things sharing this one pool (2026-09-16, be59abd made
# it process-wide instead of per-call — see _CONTEXT_EXECUTOR below):
# (1) a single build_context() call fans out 12 sub-engine tasks
# (regime/RS/sector/S-R/news/fundamentals/divergence/transition/signal-
# stats/tape/track-record/correlation) — the previous value of 4 meant
# even ONE call already oversubscribed 3:1; (2) several such calls can
# now run concurrently (multi-symbol chat turns, the digest batch, a
# live /analyze request) competing for the SAME pool, where each used
# to get its own dedicated 4 workers before be59abd's per-call-pool ->
# shared-pool change. The I/O-bound sources (news, fundamentals, tape,
# DB lookups) overlap well here; the numpy-heavy engines release the
# GIL too, so the CPU-bound ones parallelise as well — threads are
# cheap enough for this workload that erring higher costs little.
_CONTEXT_WORKERS = 16

# O9: short TTL for reusing a scanner's cached ScanResult instead of
# re-scanning. The digest batch-scans all watchlist symbols once, then
# calls build_context() per mover — without this, each mover triggers a
# redundant full scan_symbol() (quote + trend + indicators + scoring).
_SCAN_CACHE_TTL = 5.0

# Module-level executor reused across build_context() calls instead of
# creating (and tearing down) a ThreadPoolExecutor on every call. Thread
# creation is the avoided cost; idle workers are cheap. The executor is
# registered with Python's interpreter shutdown handler, which joins the
# pool on exit — no explicit shutdown needed.
_CONTEXT_EXECUTOR = ThreadPoolExecutor(
    max_workers=_CONTEXT_WORKERS,
    thread_name_prefix="context-build",
)


def _cached_scan(sym: str) -> ScanResult:
    """Return a fresh-enough cached scan for ``sym``, else rescan.

    Reuses the shared market_scanner result cache with a
    ``_SCAN_CACHE_TTL`` staleness window, so the digest batch-scan
    results are not duplicated by per-symbol build_context() calls —
    including peer scans in _correlation_context(), which previously
    always called scan_symbol() unconditionally and thus duplicated up
    to 8 full scans per context build.

    Mirrors the primary-symbol reuse block inside build_context(): a
    cached ScanResult within the TTL window is returned as-is, otherwise
    we fall back to a live scan_symbol(). Raises InsufficientDataError
    if the scan fails or returns nothing — callers that want per-peer
    resilience (e.g. _correlation_context) wrap the call in their own
    try/except.
    """
    scan = market_scanner.get_scan_result(sym)
    if isinstance(scan, ScanResult):
        scan_ts = scan.timestamp
        if scan_ts.tzinfo is None:
            scan_ts = scan_ts.replace(tzinfo=UTC)
        if (datetime.now(UTC) - scan_ts).total_seconds() < _SCAN_CACHE_TTL:
            return scan
    try:
        scan = market_scanner.scan_symbol(sym)
    except Exception as e:  # noqa: BLE001
        raise InsufficientDataError(f"scan failed for {sym}: {e}") from e
    if scan is None:
        raise InsufficientDataError(f"no scan result for {sym}")
    return scan


class InsufficientDataError(RuntimeError):
    """Raised when the quant engine doesn't have enough data to ask
    the AI a question. The caller should return an uncertainty
    response, never an AI answer."""


@dataclass
class AnalysisContext:
    """All structured inputs to the AI prompt.

    Field names map directly to the JSON keys the prompt template
    expects (see :mod:`backend.ai.prompt`).
    """

    symbol: str
    timeframe: str
    price: float | None
    timestamp: str | None
    data_status: str  # "live" | "stale" | "unknown"
    timeframe_scores: dict[str, Any] = field(default_factory=dict)
    trend_state: dict[str, Any] = field(default_factory=dict)
    market_structure: dict[str, Any] = field(default_factory=dict)
    market_regime: dict[str, Any] = field(default_factory=dict)
    relative_strength: dict[str, Any] = field(default_factory=dict)
    sector_alignment: dict[str, Any] = field(default_factory=dict)
    volume: dict[str, Any] = field(default_factory=dict)
    momentum: dict[str, Any] = field(default_factory=dict)
    support_resistance: dict[str, Any] = field(default_factory=dict)
    trend_transition: dict[str, Any] = field(default_factory=dict)
    historical_signal_stats: dict[str, Any] = field(default_factory=dict)
    news: list[dict[str, Any]] = field(default_factory=list)
    fundamentals: dict[str, Any] = field(default_factory=dict)
    divergence: dict[str, Any] = field(default_factory=dict)
    tape: dict[str, Any] = field(default_factory=dict)
    track_record: dict[str, Any] = field(default_factory=dict)
    correlation_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "price": self.price,
            "timestamp": self.timestamp,
            "data_status": self.data_status,
            "timeframe_scores": self.timeframe_scores,
            "trend_state": self.trend_state,
            "market_structure": self.market_structure,
            "market_regime": self.market_regime,
            "relative_strength": self.relative_strength,
            "sector_alignment": self.sector_alignment,
            "volume": self.volume,
            "momentum": self.momentum,
            "support_resistance": self.support_resistance,
            "trend_transition": self.trend_transition,
            "historical_signal_stats": self.historical_signal_stats,
            "news": self.news,
            "fundamentals": self.fundamentals,
            "divergence": self.divergence,
            "tape": self.tape,
            "track_record": self.track_record,
            "correlation_context": self.correlation_context,
        }

    def compact(self) -> dict[str, Any]:
        """Return a serialized context dict with empty fields stripped.

        Drops keys whose values are empty dicts/lists or None — fields
        like ``news: []`` or ``tape: {}`` carry no signal and bloat the
        prompt with ~30% noise on a typical symbol.  Scalar fields
        (``price``, ``data_status``, etc.) are always retained.
        """
        d = self.to_dict()
        return {
            k: v for k, v in d.items() if not (v is None or (isinstance(v, (dict, list)) and not v))
        }


def _safe_call(fn, *args, default=None, **kwargs):
    """Call a quant engine function and return its result, swallowing
    any exception. Used so that a single broken sub-engine doesn't
    take the whole context down — the AI just sees a partial picture."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return default


def _safe_result(fut: Future) -> Any:
    """Collect a thread-pool task's result, degrading to an empty
    default on any failure.

    Each sub-engine helper already wraps its own work in a broad
    try/except and returns an empty result on failure, so under normal
    operation ``fut.result()`` never raises. This extra guard preserves
    the original "one broken sub-source degrades to empty, never kills
    the whole context" contract even if a helper has an unexpected bug
    (e.g. an exception raised before its own try block) — a task-level
    failure must not take down build_context the way an unhandled
    exception in the sequential code would have raised straight out of
    the function.
    """
    try:
        return fut.result()
    except Exception:  # noqa: BLE001
        return None


def _regime_value(enum_or_str) -> str:
    """Coerce a regime enum or string to a plain string."""
    if hasattr(enum_or_str, "value"):
        return enum_or_str.value
    return str(enum_or_str)


def _sig_to_dict(sig) -> dict[str, Any]:
    """Serialize a signal dataclass to a dict, stripping private fields."""
    if sig is None:
        return {}
    result = {}
    for k, v in vars(sig).items():
        if k.startswith("_"):
            continue
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        elif hasattr(v, "value"):  # enum
            result[k] = v.value
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Sub-engine context helpers (run concurrently from build_context)
# ---------------------------------------------------------------------------
# Each mirrors one "step" of the original sequential builder, preserving
# the exact same engine API usage and the same degrade-to-empty semantics.
# Keeping them as standalone functions also makes each step individually
# unit-testable without spinning up the whole context.
# ---------------------------------------------------------------------------


def _regime_context(sym: str) -> dict[str, Any]:
    """Step 3 — Market regime (global, per-symbol engine state).

    Found live 2026-09-10: this instantiated a *new* MarketRegimeEngine
    per call instead of reusing any shared state. Left as-is for now (a
    registry for regime engines doesn't exist); it only reads, so running
    it on a worker thread is safe.
    """
    market_regime: dict[str, Any] = {}
    try:
        from backend.regime.market_regime_engine import MarketRegimeEngine

        engine = MarketRegimeEngine(sym)
        regime_sig = engine.get_current_regime()
        if regime_sig is not None:
            market_regime = _sig_to_dict(regime_sig)
    except Exception:  # noqa: BLE001
        logger.debug("AI context: regime_context unavailable; section omitted", exc_info=True)
    return market_regime


def _rs_context(sym: str) -> list[dict[str, Any]]:
    """Step 4 — Relative strength (per-symbol, one per benchmark).

    Returns the *full* RS signal list; build_context() picks the
    primary benchmark (SPY/QQQ) from it afterwards.

    Found live 2026-09-10: same as regime — new RelativeStrengthEngine
    per call. Read-only, thread-safe.
    """
    rs_list: list[dict[str, Any]] = []
    try:
        from backend.regime.relative_strength_engine import RelativeStrengthEngine

        rs_engine = RelativeStrengthEngine(sym)
        signals = rs_engine.get_signals()
        rs_list = [_sig_to_dict(s) for s in signals]
    except Exception:  # noqa: BLE001
        logger.debug("AI context: rs_context unavailable; section omitted", exc_info=True)
    return rs_list


def _sector_context(
    sym: str,
    *,
    get_trend_engine,
) -> dict[str, Any]:
    """Step 5 — Sector alignment.

    Found live 2026-09-10: SectorEngine(sym) with no injected engines
    builds three brand-new, never-fed TrendEngine instances (stock,
    sector ETF, SPY) from scratch — zero seed data, zero ticks — so
    get_overall_trend() is always None and every alignment came back
    "unknown"/"insufficient_data" regardless of how much real trend
    data actually existed. SectorEngine's own docstring says exactly
    this: "accept injected engines to share with other callers ...
    looked up via the shared registry so any other component that also
    needs SPY or XLK gets the same instance" — but neither real call
    site in the app (this one, and backend/api/regime/router.py) was
    actually doing that injection. Fixed by pulling the same shared,
    DB-seeded TrendEngine singletons every other feature already uses
    (backend.api.trend.registry.get_engine — the exact registry the
    trend_transition section also imports).

    ``get_trend_engine`` is injected (rather than imported) so tests can
    patch the registry once and have both this helper and the trend-
    transition helper share the same mock.
    """
    sector_alignment: dict[str, Any] = {}
    try:
        from backend.regime.sector_engine import SECTOR_ETFS, SECTOR_MAP, SectorEngine

        sector_name = SECTOR_MAP.get(sym, "Unknown")
        sector_etf = SECTOR_ETFS.get(sector_name)
        sector_engine = SectorEngine(
            sym,
            stock_engine=get_trend_engine(sym),
            sector_engine=get_trend_engine(sector_etf) if sector_etf else None,
            market_engine=get_trend_engine("SPY"),
        )
        sector_sig = sector_engine.get_current_signal()
        if sector_sig is not None:
            sector_alignment = _sig_to_dict(sector_sig)
    except Exception:  # noqa: BLE001
        logger.debug("AI context: sector_context unavailable; section omitted", exc_info=True)
    return sector_alignment


def _sr_context(sym: str, timeframe: str) -> dict[str, Any]:
    """Step 7 — Support / resistance.

    Found live 2026-09-10: this imported a `support_resistance_engine`
    singleton from a module that doesn't exist
    (backend.support_resistance.support_resistance_engine) and called a
    `.detect_levels(sym, timeframe)` method that doesn't exist either —
    the real module is backend.support_resistance.sr_engine (re-exported
    as backend.support_resistance.SupportResistanceEngine), a class with
    a `.detect(bars, symbol, timeframe)` method that returns a flat
    `.levels` list, not separate `.supports`/`.resistances` attributes.
    Same failure class as the trend_transition bug: a broad except
    silently swallowed the AttributeError/ImportError every time, so
    `support_resistance` had been an empty dict in every AI context
    ever built. Fixed by calling the engine the way the existing,
    working `/api/analysis/{symbol}/price-range` endpoint does
    (backend/api/analysis/router.py) — same bar source, same engine
    construction — then bucketing the flat level list into supports/
    resistances by price relative to the latest close (a level below
    current price is support, above is resistance; `.levels` is
    pre-sorted strongest-first so each bucket keeps that relative
    order).
    """
    sr: dict[str, Any] = {}
    try:
        from backend.analysis.series import load_bars as _load_bars_for_sr
        from backend.analysis.series import load_reference_bars as _load_reference_bars_for_sr
        from backend.support_resistance import SupportResistanceEngine

        sr_bars = _load_bars_for_sr(sym, timeframe, limit=200)
        if len(sr_bars) >= 20:
            # Calendar-anchored levels (today/prev-day/this-week/prev-week/
            # 52-week high & low) must come from a dedicated daily series,
            # not `sr_bars` (which is at `timeframe`'s own granularity) —
            # see SupportResistanceEngine.detect()'s `reference_bars` param
            # and backend/api/analysis/router.py's identical fetch. Without
            # this, AI chat/analysis reported different "today's high" etc.
            # depending on which timeframe last populated the context.
            reference_bars = _load_reference_bars_for_sr(sym)
            sr_engine = SupportResistanceEngine(lookback_period=5, lookback_bars=200)
            sr_result = sr_engine.detect(
                sr_bars, symbol=sym, timeframe=timeframe, reference_bars=reference_bars
            )
            latest_close = sr_result.latest_close
            if latest_close is not None:
                # Bucket by TYPE semantics, not by price vs close. Found
                # live 2026-09-16: a `swing_high` is resistance by
                # definition — price was rejected there — but NVDA's close
                # (213.90) sat just below its 213.75 swing high, so the
                # old `price <= latest_close → support` rule mislabeled
                # every swing high under the close as "support". The AI
                # then reported 213.75 as a support level, which is
                # backwards. The analysis router never had this problem
                # because it only emits pivots with an explicit
                # is_resistance flag; mirror that here by mapping each
                # SRType to its structural side. `consolidation_zone`
                # has no inherent side, so it falls back to the price-vs-
                # close rule (a zone straddling the close is ambiguous
                # either way).
                resistance_types = {
                    "today_high",
                    "prev_day_high",
                    "this_week_high",
                    "prev_week_high",
                    "week_52_high",
                    "pivot_r1",
                    "pivot_r2",
                    "pivot_r3",
                    "swing_high",
                }
                support_types = {
                    "today_low",
                    "prev_day_low",
                    "this_week_low",
                    "prev_week_low",
                    "week_52_low",
                    "pivot_pp",
                    "pivot_s1",
                    "pivot_s2",
                    "pivot_s3",
                    "swing_low",
                }
                supports: list[dict[str, Any]] = []
                resistances: list[dict[str, Any]] = []
                for level in sr_result.levels:
                    entry = {
                        "price": round(level.price, 2),
                        "type": level.type.value,
                        "strength": round(float(level.strength), 2),
                    }
                    ltype = level.type.value
                    if ltype in resistance_types:
                        bucket = resistances
                    elif ltype in support_types:
                        bucket = supports
                    else:
                        # consolidation_zone — no structural side
                        bucket = supports if level.price <= latest_close else resistances
                    bucket.append(entry)
                sr = {
                    "supports": supports[:3],
                    "resistances": resistances[:3],
                }
    except Exception:  # noqa: BLE001
        logger.debug("AI context: sr_context unavailable; section omitted", exc_info=True)
    return sr


def _transition_context(
    sym: str,
    timeframe: str,
    *,
    get_trend_engine,
) -> dict[str, Any]:
    """Step 8 — Most recent trend transition.

    Found live 2026-09-09: this section (and the identical pattern in
    nl_search/executor.py's JustTransitionedFilter) imported a
    `trend_transition_engine` singleton and called `.get_history(...)`
    on it — neither exists. `backend.transitions.trend_transition_engine`
    defines only the `TrendTransitionEngine` class (`.detect()`/`.latest()`,
    which take a raw scores sequence), no module-level instance and no
    `get_history` method. The broad except silently swallowed the
    resulting ImportError/AttributeError, so `trend_transition` has been
    an empty dict in every AI analysis ever produced. Fixed by using the
    engine the way it's actually designed to be used: pull the
    already-warmed TrendEngine's own score history (no new bar fetch)
    and detect the latest transition on it directly.

    ``get_trend_engine`` is injected (same registry the sector helper
    uses) so a test's single registry mock serves both helpers and the
    two don't diverge.
    """
    transition: dict[str, Any] = {}
    try:
        from backend.engines.timeframe import Timeframe
        from backend.transitions.trend_transition_engine import (
            TrendTransitionEngine,
        )

        tf_enum = Timeframe(timeframe.lower())
        hist = get_trend_engine(sym).trend_history.get(tf_enum, [])
        if len(hist) > 6:
            scores = [s.score for s in hist]
            timestamps = [s.timestamp for s in hist]
            t = TrendTransitionEngine(window=5, min_delta=10.0).latest(
                scores, timestamps=timestamps, symbol=sym, timeframe=timeframe
            )
            if t is not None:
                transition = t.to_dict()
    except Exception:  # noqa: BLE001
        logger.debug("AI context: transition_context unavailable; section omitted", exc_info=True)
    return transition


def _signal_stats_context(sym: str, timeframe: str) -> dict[str, Any]:
    """Step 9 — Historical signal statistics (Phase 13)."""
    signal_stats: dict[str, Any] = {}
    try:
        from backend.services.signal_recorder import signal_recorder

        stats = signal_recorder.get_stats(symbol=sym, timeframe=timeframe)
        if stats:
            signal_stats = {
                "total_signals": stats.get("total", 0),
                "avg_return_5b": stats.get("avg_return_5b"),
                "avg_return_10b": stats.get("avg_return_10b"),
                "win_rate": stats.get("win_rate"),
            }
    except Exception:  # noqa: BLE001
        logger.debug("AI context: signal_stats_context unavailable; section omitted", exc_info=True)
    return signal_stats


def _news_context(sym: str, include: bool) -> list[dict[str, Any]]:
    """Step 10 — News (Phase 18 aux-data, real provider, previously never
    reached the AI)."""
    news: list[dict[str, Any]] = []
    if not include:
        return news
    try:
        from backend.aux_data.services.manager import aux_data_manager
        from backend.utils.timezone import now_ny, to_ny

        news_resp = aux_data_manager.get_news(sym, limit=5)
        # Found live 2026-09-10: news_resp.timestamp comes from
        # now_ny(), which is naive-by-convention (this project's own
        # rule: naive datetimes are always NY local, UTC ones are
        # always aware — see backend/utils/timezone.py). But each
        # article's own item.timestamp is UTC-aware (straight from the
        # provider). Subtracting a naive datetime from an aware one
        # raises TypeError, silently swallowed by the except below — so
        # age_hours computation was throwing on the FIRST article every
        # single call, and since that happened before anything got
        # appended, `news` came back [] on every request regardless of
        # how much real news existed (confirmed live: /api/aux-data/news/
        # AAPL had 10 real headlines while build_context()['news'] was
        # always empty). Fixed by converting each item's timestamp to
        # naive NY before comparing, per the project's own convention,
        # instead of reusing the response wrapper's already-naive
        # timestamp's (irrelevant) tzinfo.
        now = now_ny()
        for item in news_resp.items[:5]:
            item_ny = to_ny(item.timestamp)
            age_hours = (
                round((now - item_ny).total_seconds() / 3600.0, 1) if item_ny is not None else None
            )
            news.append(
                {
                    "headline": item.headline,
                    "source": item.source,
                    "relevance": round(float(item.relevance), 2),
                    "age_hours": age_hours,
                }
            )
    except Exception:  # noqa: BLE001
        logger.debug("AI context: news_context unavailable; section omitted", exc_info=True)
    return news


def _fundamentals_context(sym: str, include: bool) -> dict[str, Any]:
    """Step 11 — Fundamentals (Phase 18 aux-data). A curated subset, not
    every field, to keep the prompt focused."""
    fundamentals: dict[str, Any] = {}
    if not include:
        return fundamentals
    try:
        from backend.aux_data.services.manager import aux_data_manager

        f = aux_data_manager.get_fundamentals(sym).data
        fundamentals = {
            k: v
            for k, v in {
                "sector": f.sector,
                "industry": f.industry,
                "market_cap": f.market_cap,
                "pe_ratio": f.pe_ratio,
                "eps_growth": f.eps_growth,
                "debt_to_equity": f.debt_to_equity,
                "analyst_target": f.analyst_target,
                "recommendation": f.recommendation,
                "beta": f.beta,
            }.items()
            if v is not None
        }
    except Exception:  # noqa: BLE001
        logger.debug("AI context: fundamentals_context unavailable; section omitted", exc_info=True)
    return fundamentals


def _divergence_context(sym: str, timeframe: str, include: bool) -> dict[str, Any]:
    """Step 12 — Divergence (Phase 9 engine, previously never reached the
    AI). Most recent divergence only, mirrors trend_transition's "latest
    one" convention."""
    divergence: dict[str, Any] = {}
    if not include:
        return divergence
    try:
        from backend.analysis.series import (
            bar_dicts_to_arrays,
            load_bars,
            macd_histogram_series,
            rsi_series,
        )
        from backend.divergence import DivergenceEngine

        bars = load_bars(sym, timeframe, limit=200)
        if len(bars) >= 30:
            arrays = bar_dicts_to_arrays(bars)
            # load_bars returns desc=True (newest→oldest) but the
            # DivergenceEngine assumes chronological (oldest→newest)
            # order: pivot `a` is the older bar, pivot `b` the newer, and
            # the divergence is stamped with timestamps[b]. Reverse every
            # array so the engine sees time in the order it expects; its
            # output is sorted by pivot_b_index ascending, so [-1] remains
            # the most recent (newest pivot) divergence.
            highs = list(reversed(arrays["highs"]))
            lows = list(reversed(arrays["lows"]))
            closes = list(reversed(arrays["closes"]))
            volumes = list(reversed(arrays["volumes"]))
            timestamps = list(reversed(arrays["timestamps"]))
            rsi = rsi_series(closes, period=14)
            macd = macd_histogram_series(closes, fast=12, slow=26, signal=9)
            found = DivergenceEngine(pivot_lookback=2, max_pivots_apart=80).detect(
                highs,
                lows,
                closes,
                volumes=volumes,
                rsi=rsi,
                macd=macd,
                timestamps=timestamps,
                symbol=sym,
                timeframe=timeframe,
            )
            if found:
                divergence = found[-1].to_dict()
    except Exception:  # noqa: BLE001
        logger.debug("AI context: divergence_context unavailable; section omitted", exc_info=True)
    return divergence


def _tape_context(sym: str) -> dict[str, Any]:
    """Step 13 — Tape / order flow. A compact view of the recent trade
    tape. Raw aggregation, not an engine-derived quant number, so the AI
    may cite it directly (framed as "recent tape")."""
    tape: dict[str, Any] = {}
    try:
        from backend.config.settings import settings as _settings

        if _settings.tape.enabled:
            from backend.api.tape.registry import get_tape_engine

            s = get_tape_engine(sym).get_snapshot()
            if s.get("trade_count"):
                tape = {
                    "pressure": s["pressure"],
                    "signed_volume_1m": s["signed_volume"],
                    "buy_ratio_1m": s["buy_ratio"],
                    "tape_speed_per_s": s["tape_speed"],
                    "tape_accel": s["tape_accel"],
                    "block_count_5m": s["block_count_5m"],
                }
    except Exception:  # noqa: BLE001
        logger.debug("AI context: tape_context unavailable; section omitted", exc_info=True)
    return tape


def _track_record_context(sym: str) -> dict[str, Any]:
    """Step 14 — Track record: the AI's OWN past buy/sell calls on this
    ticker, graded against what happened. Not engine-derived either, but
    it must be framed honestly (small samples early on) — see
    CHAT_SYSTEM_PROMPT / SYSTEM_PROMPT for the exact wording rule.
    """
    track_record: dict[str, Any] = {}
    try:
        from backend.config.settings import settings as _settings

        if _settings.ai_trade_plan_tracking.enabled:
            from backend.ai.trade_plan_tracker import get_track_record

            track_record = get_track_record(sym)
    except Exception:  # noqa: BLE001
        logger.debug("AI context: track_record_context unavailable; section omitted", exc_info=True)
    return track_record


def _correlation_context(
    sym: str,
    *,
    portfolio_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """O10 — Multi-symbol correlation context.

    Each ``analyze_symbol()`` call was previously completely isolated —
    the AI saw one ticker at a time with no awareness of how it relates
    to the rest of the portfolio, the sector, or correlated names. This
    helper scans a small set of peer symbols (the caller's portfolio or
    watchlist, capped at 8) and summarizes their trend direction so the
    AI can reason about confluence/divergence.

    The summary is intentionally compact — just direction + strength per
    peer, plus a count of how many are aligned vs opposed — so the model
    gets the cross-ticker signal without a 5K-token dump.
    """
    if not portfolio_symbols:
        return {}
    peers = [s for s in portfolio_symbols if s.upper() != sym.upper()][:8]
    if not peers:
        return {}

    summary: dict[str, Any] = {"peer_count": len(peers), "peers": []}
    aligned = 0
    opposed = 0
    try:
        from backend.regime.sector_engine import SECTOR_MAP

        primary_sector = SECTOR_MAP.get(sym.upper(), "Unknown")
        same_sector = 0
        for peer in peers:
            psym = peer.upper()
            try:
                # O9: prefer the scanner's shared cache (5s TTL) before
                # rescanning — peers often overlap the digest batch scan,
                # and even when they don't this matches the primary-symbol
                # reuse path instead of unconditionally running a full scan.
                scan = _cached_scan(psym)
                tsig = scan.trend_signals or {}
                primary = tsig.get("1d") or tsig.get("ONE_DAY") or next(iter(tsig.values()), None)
                if primary is None:
                    continue
                if hasattr(primary, "direction"):
                    direction = _regime_value(primary.direction)
                    strength = _regime_value(primary.strength)
                elif isinstance(primary, dict):
                    direction = _regime_value(primary.get("direction", "unknown"))
                    strength = _regime_value(primary.get("strength", "unknown"))
                else:
                    continue
                # Normalize the broad direction vocabulary ("strong_bullish",
                # "weak_bearish", etc.) to a compact "bullish"/"bearish"/"neutral"
                # for the correlation summary.
                norm = _normalize_direction(direction)
                summary["peers"].append(
                    {
                        "symbol": psym,
                        "direction": norm,
                        "strength": strength,
                    }
                )
                if norm == "bullish":
                    aligned += 1
                elif norm == "bearish":
                    opposed += 1
                if SECTOR_MAP.get(psym, "Unknown") == primary_sector:
                    same_sector += 1
            except Exception:  # noqa: BLE001
                continue
        summary["aligned"] = aligned
        summary["opposed"] = opposed
        summary["same_sector_count"] = same_sector
        if primary_sector != "Unknown":
            summary["primary_sector"] = primary_sector
    except Exception:  # noqa: BLE001
        logger.debug("AI context: correlation_context unavailable; section omitted", exc_info=True)
    return summary


def _normalize_direction(direction: str) -> str:
    """Map the broad trend-signal vocabulary to bullish/bearish/neutral."""
    d = direction.lower()
    if "bullish" in d or "up" in d or "strong_bull" in d:
        return "bullish"
    if "bearish" in d or "down" in d or "strong_bear" in d:
        return "bearish"
    if "neutral" in d or "sideways" in d or "mixed" in d:
        return "neutral"
    return "unknown"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_context(
    symbol: str,
    timeframe: str = "1d",
    *,
    include_news: bool = True,
    include_fundamentals: bool = True,
    include_divergence: bool = True,
    portfolio_symbols: list[str] | None = None,
) -> AnalysisContext:
    """Gather a structured context dict for ``symbol``.

    ``timeframe`` is the primary analysis window. Cross-timeframe
    scores come from the scanner's MTF result; everything else is
    taken from the most recent engine state.

    ``include_news``/``include_fundamentals``/``include_divergence``
    default to ``True`` for a single-symbol analysis call, but can be
    turned off by callers that build context for many symbols at once
    (e.g. a digest iterating the whole watchlist) to skip the extra
    aux-data I/O per symbol.

    ``portfolio_symbols`` (O10): an optional list of peer tickers the
    caller already knows about (e.g. the active watchlist). When
    provided, ``build_context`` scans up to 8 peers and summarizes
    their trend direction so the AI can reason about cross-ticker
    confluence/divergence instead of analyzing in isolation.

    Raises ``InsufficientDataError`` when there's no quote and no
    trend signal — the caller should return an uncertainty response.
    """
    sym = symbol.upper()
    # The bar table stores timeframes lowercase (1m/1h/1d/1wk — see
    # backend/repositories/bar_repository.py's _ALL_TIMEFRAMES), and
    # load_bars / get_bars match the column value exactly, so the
    # DB-facing helpers below (S/R, divergence, signal stats) need the
    # lowercase form. Upper-casing it here ("1D") silently returned 0
    # bars for every symbol — found live 2026-09-16: "Calculate S/R
    # for SPY" came back with an empty support_resistance dict and the
    # AI honestly replied it had no live levels, when the engine
    # produces 50 real levels the moment you pass "1d".
    tf = timeframe.lower()

    # --- 1. Single-symbol scan (complete snapshot including MTF scores) ---
    # Runs on the caller's thread: every other sub-engine derives from it,
    # and it's the cheapest, most-cacheable call.
    scan = _cached_scan(sym)

    quote = scan.quote
    # quote is a Pydantic model (or None)
    if quote is not None:
        price = quote.price
        ts = quote.timestamp
    else:
        price = None
        ts = scan.timestamp

    if price is None:
        raise InsufficientDataError(f"no price available for {sym}")

    # --- 2. MTF trend signals (cheap derivation from the scan) ---
    mtf = scan.trend_signals or {}
    timeframe_scores: dict[str, Any] = {}
    for tf_key, tsig in mtf.items():
        # trend_signals is a dict-of-dicts in the scanner API
        if hasattr(tsig, "direction"):
            timeframe_scores[tf_key] = {
                "direction": _regime_value(tsig.direction),
                "strength": _regime_value(tsig.strength),
                "confidence": round(float(tsig.confidence), 2),
            }
        elif isinstance(tsig, dict):
            timeframe_scores[tf_key] = {
                "direction": _regime_value(tsig.get("direction", "unknown")),
                "strength": _regime_value(tsig.get("strength", "unknown")),
                "confidence": round(float(tsig.get("confidence", 0)), 2),
            }

    # Primary signal for the requested timeframe
    primary_sig = mtf.get(tf)
    if primary_sig is None:
        primary_sig = next(iter(mtf.values()), None)

    trend_state: dict[str, Any] = {}
    if primary_sig is not None:
        if hasattr(primary_sig, "direction"):
            trend_state = {
                "direction": _regime_value(primary_sig.direction),
                "strength": _regime_value(primary_sig.strength),
                "confidence": round(float(primary_sig.confidence), 2),
            }
        elif isinstance(primary_sig, dict):
            trend_state = {
                "direction": _regime_value(primary_sig.get("direction", "unknown")),
                "strength": _regime_value(primary_sig.get("strength", "unknown")),
                "confidence": round(float(primary_sig.get("confidence", 0)), 2),
            }

    # --- 6. Volume / momentum (cheap derivation from the scan, done inline
    # rather than on a worker — not worth a thread hop) ---
    ind = scan.indicator_values or {}
    volume: dict[str, Any] = {
        "rvol": ind.get("rvol"),
        "volume_state": ind.get("volume_state"),
    }
    momentum: dict[str, Any] = {
        "rsi": ind.get("rsi"),
        "macd_hist": ind.get("macd_hist"),
    }

    # --- 3-14. Independent sub-engines, run concurrently ---
    # Previously 14 sequential calls; now the I/O-bound ones (news,
    # fundamentals, tape, DB lookups) overlap with the CPU-bound ones
    # (regime, RS, sector, SR, divergence), and numpy-heavy engines
    # release the GIL so the CPU work parallelises too. Each helper
    # degrades to an empty result on its own failure, matching the
    # original per-section resilience. Expected speedup: 40-60% of
    # context-build latency.
    #
    # The trend registry lookup is shared by both the sector and the
    # trend-transition helpers — resolve it once so a test's single
    # registry mock serves both, and so the two helpers don't race on
    # a cold-start import. Both helpers accept the resolver as a
    # parameter (dependency injection) purely to make this sharable.
    from backend.api.trend.registry import get_engine as _get_trend_engine

    # Reuse the module-level executor instead of constructing a
    # ThreadPoolExecutor on every build_context() call. The workers are
    # cheap to keep idle and join themselves on interpreter shutdown;
    # we intentionally do NOT shut it down here so the threads survive
    # across calls.
    ex = _CONTEXT_EXECUTOR
    f_regime = ex.submit(_regime_context, sym)
    f_rs = ex.submit(_rs_context, sym)
    f_sector = ex.submit(_sector_context, sym, get_trend_engine=_get_trend_engine)
    f_sr = ex.submit(_sr_context, sym, tf)
    f_news = ex.submit(_news_context, sym, include_news)
    f_fund = ex.submit(_fundamentals_context, sym, include_fundamentals)
    f_div = ex.submit(_divergence_context, sym, tf, include_divergence)
    f_trans = ex.submit(_transition_context, sym, tf, get_trend_engine=_get_trend_engine)
    f_stats = ex.submit(_signal_stats_context, sym, tf)
    f_tape = ex.submit(_tape_context, sym)
    f_track = ex.submit(_track_record_context, sym)
    f_corr = ex.submit(_correlation_context, sym, portfolio_symbols=portfolio_symbols)

    market_regime = _safe_result(f_regime)
    rs_list = _safe_result(f_rs) or []
    sector_alignment = _safe_result(f_sector)
    sr = _safe_result(f_sr)
    news = _safe_result(f_news) or []
    fundamentals = _safe_result(f_fund)
    divergence = _safe_result(f_div)
    transition = _safe_result(f_trans)
    signal_stats = _safe_result(f_stats)
    tape = _safe_result(f_tape)
    track_record = _safe_result(f_track)
    correlation_context = _safe_result(f_corr) or {}

    # Pick the primary benchmark (SPY) for the display
    primary_rs: dict[str, Any] = {}
    for rs in rs_list:
        if rs.get("benchmark") in ("SPY", "QQQ"):
            primary_rs = rs
            break
    if not primary_rs and rs_list:
        primary_rs = rs_list[0]

    return AnalysisContext(
        symbol=sym,
        timeframe=timeframe,
        price=price,
        timestamp=str(ts) if ts else None,
        data_status="live" if quote else "stale",
        timeframe_scores=timeframe_scores,
        trend_state=trend_state,
        market_structure={
            "total_score": round(scan.calculate_total_score(), 2),
            "dimension_scores": {k: round(v, 1) for k, v in (scan.scores or {}).items()},
            "signals": list(scan.signals or []),
        },
        market_regime=market_regime,
        relative_strength=primary_rs,
        sector_alignment=sector_alignment,
        volume=volume,
        momentum=momentum,
        support_resistance=sr,
        trend_transition=transition,
        historical_signal_stats=signal_stats,
        news=news,
        fundamentals=fundamentals,
        divergence=divergence,
        tape=tape,
        track_record=track_record,
        correlation_context=correlation_context,
    )
