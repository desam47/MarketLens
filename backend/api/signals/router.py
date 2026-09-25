"""
API endpoints for historical signal storage and research queries (Phase 13).

Routes:
  GET    /api/signals/                  - paginated list with optional filters
  GET    /api/signals/{id}              - single signal by id
  GET    /api/signals/symbol/{symbol}   - latest signal per timeframe for a symbol
  GET    /api/signals/research/regime-performance   - avg returns by market regime
  GET    /api/signals/research/count-by-regime      - signal count by regime
  GET    /api/signals/research/summary  - research metrics over the full filtered population
  POST   /api/signals/backfill          - trigger outcome backfill manually
  POST   /api/signals/record            - preview, then record signals for newly closed bars

All endpoints read/write through ``SignalRepository`` so the API and the
ingestion service share one path to the DB.
"""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from io import StringIO
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.market_data.services.ingestion_service import ingestion_service
from backend.models import BarModel
from backend.repositories.signal_repository import SignalRepository, directional_outcome
from backend.services.excursion_stats import DEFAULT_MIN_SAMPLE, excursion_distribution
from backend.services.signal_recorder import signal_recorder
from backend.utils.timezone import format_edt_iso

from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["signals"])


def _to_dashboard_tz(value: datetime | None) -> datetime | None:
    """Convert a datetime to America/New_York.

    Naive datetimes are treated as NY local time (the project's storage
    convention since 2026-09-02). Aware datetimes are converted to NY.
    Returns None unchanged.
    """
    from backend.utils.timezone import to_ny

    return to_ny(value)


class SignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    timestamp: datetime
    timeframe: str
    price: float | None
    trend_score: float | None
    trend_state: str | None
    strength: float | None
    market_regime: str | None
    relative_strength: str | None
    sector_alignment: float | None
    volume_state: str | None
    momentum: float | None
    structure: str | None
    confidence_inputs: str | None
    strategy_version: str | None
    data_quality: str | None
    return_5b: float | None
    return_10b: float | None
    return_20b: float | None
    mfe: float | None
    mae: float | None
    created_at: datetime | None

    @field_serializer("timestamp", "created_at")
    def _serialize_tz(self, value: datetime | None) -> str | None:
        # Always emit with explicit EDT/EST offset (e.g. "…-04:00") so
        # the browser parses the value as NY local time regardless of
        # the user's actual timezone. format_edt_iso handles naive-NY
        # (project convention) and aware datetimes uniformly.
        return format_edt_iso(value)


class RegimePerformance(BaseModel):
    regime: str
    count: int
    avg_return_5b: float | None
    avg_return_10b: float | None
    avg_return_20b: float | None
    avg_mfe: float | None
    avg_mae: float | None


class RegimeCount(BaseModel):
    regime: str
    count: int


class BackfillResponse(BaseModel):
    updated: int


class RecordSignalsRequest(BaseModel):
    """Bulk recording request. Unknown fields are rejected rather than ignored."""

    model_config = ConfigDict(extra="forbid")

    symbols: list[str] | None = None
    confirm: bool = False


# Manual bulk work (record, outcome backfill) runs one request at a time, so
# repeated clicks cannot stack up alongside the ingestion loop's own passes.
_manual_run_lock = threading.Lock()


@contextmanager
def _exclusive_manual_run() -> Iterator[None]:
    if not _manual_run_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="A manual signal recording or outcome backfill is already running; try again when it finishes.",
        )
    try:
        yield
    finally:
        _manual_run_lock.release()


class TimeframeCoverage(BaseModel):
    timeframe: str
    recorded: int
    complete: int


class RegimeCoverage(BaseModel):
    """Complete outcomes that carry a regime. Only rows recorded as their bar
    closed get one, because the regime engine only knows the present."""

    with_regime: int
    complete: int


class ResearchMetrics(BaseModel):
    label: str
    complete: int
    directional: int
    win_rate: float | None
    avg_signal_return_5b: float | None
    avg_signal_return_10b: float | None


class ResearchPerformance(ResearchMetrics):
    by_regime: list[ResearchMetrics]
    by_trend: list[ResearchMetrics]


SignalScopeMode = Literal["all_active", "watchlist", "all_stored"]


class ResolvedSignalScope(BaseModel):
    mode: SignalScopeMode
    watchlist_ids: list[int]
    watchlist_names: list[str]
    symbols: list[str]


class SignalResearchPage(BaseModel):
    records: list[SignalResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
    scope: ResolvedSignalScope
    timeframe: str | None
    start_date: date | None
    end_date: date | None


class SignalResearchSummary(BaseModel):
    """Metrics over the whole filtered population, not one page of it."""

    scope: ResolvedSignalScope
    timeframe: str | None
    start_date: date | None
    end_date: date | None
    recorded: int
    complete: int
    timeframe_coverage: list[TimeframeCoverage]
    regime_coverage: RegimeCoverage
    performance: ResearchPerformance | None
    performance_note: str | None


class ExcursionPercentiles(BaseModel):
    p25: float
    p50: float
    p75: float
    p90: float


class ExcursionFilters(BaseModel):
    symbol: str | None
    timeframe: str
    trend_state: str
    strength_min: float | None
    strength_max: float | None
    start_date: date | None
    end_date: date | None


class ExcursionMetrics(BaseModel):
    """Distribution of one conditioned slice of outcome-complete signals.

    ``adverse_excursion_pct`` is a positive magnitude, so ``p75`` is the
    distance a stop must clear to sit outside the heat three quarters of
    comparable signals took. ``favorable_excursion_pct`` is signed; its
    ``p50`` is the median run in favour. Both are percent of entry price.
    Every statistic is ``None`` when ``sufficient`` is false.
    """

    sample_size: int
    min_sample: int
    sufficient: bool
    confidence: str
    units: str
    win_rate: float | None
    avg_return_5b: float | None
    avg_return_10b: float | None
    avg_return_20b: float | None
    median_return_5b: float | None
    median_return_10b: float | None
    median_return_20b: float | None
    adverse_excursion_pct: ExcursionPercentiles | None
    favorable_excursion_pct: ExcursionPercentiles | None
    notes: list[str]


class ExcursionRelaxation(ExcursionMetrics):
    """A wider slice, offered only when the requested one was too thin."""

    level: str
    label: str
    filters: ExcursionFilters


class ExcursionResponse(ExcursionMetrics):
    symbol: str
    timeframe: str
    direction: str
    filters: ExcursionFilters
    relaxation: list[ExcursionRelaxation]


def _resolve_signal_scope(
    db: Session,
    *,
    scope: SignalScopeMode,
    watchlist_id: int | None,
    include_all: bool = False,
) -> tuple[ResolvedSignalScope, list[str] | None]:
    """Resolve a research request to explicit watchlists and enabled symbols.

    ``include_all`` is retained for existing callers. It maps to the explicit
    offline ``all_stored`` scope rather than changing default UI behaviour.
    """
    from backend.repositories.watchlist_repository import WatchlistRepository

    if include_all:
        scope = "all_stored"
    wl_repo = WatchlistRepository(db)
    if scope == "all_stored":
        return ResolvedSignalScope(
            mode="all_stored", watchlist_ids=[], watchlist_names=[], symbols=[]
        ), None

    if scope == "watchlist":
        if watchlist_id is None:
            raise HTTPException(status_code=422, detail="watchlist_id is required for scope=watchlist")
        watchlist = wl_repo.get_watchlist(watchlist_id)
        if watchlist is None or not watchlist.is_active:
            raise HTTPException(status_code=404, detail="Active watchlist not found")
        symbols = [s.symbol.upper() for s in wl_repo.get_watchlist_symbols(watchlist.id, enabled_only=True)]
        return ResolvedSignalScope(
            mode="watchlist",
            watchlist_ids=[watchlist.id],
            watchlist_names=[watchlist.name],
            symbols=symbols,
        ), symbols

    watchlists = wl_repo.get_watchlists(active_only=True)
    symbols: list[str] = []
    seen: set[str] = set()
    for watchlist in watchlists:
        for row in wl_repo.get_watchlist_symbols(watchlist.id, enabled_only=True):
            symbol = row.symbol.upper()
            if symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
    return ResolvedSignalScope(
        mode="all_active",
        watchlist_ids=[watchlist.id for watchlist in watchlists],
        watchlist_names=[watchlist.name for watchlist in watchlists],
        symbols=symbols,
    ), symbols


def _research_time_range(
    start_date: date | None, end_date: date | None
) -> tuple[datetime | None, datetime | None]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    start = datetime.combine(start_date, time.min) if start_date else None
    end = (
        datetime.combine(end_date + timedelta(days=1), time.min) - timedelta(microseconds=1)
        if end_date else None
    )
    return start, end


def _csv_value(value: object) -> object:
    """Avoid formula evaluation when a CSV is opened in a spreadsheet."""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


# --- Endpoints -------------------------------------------------------------


@router.get("/", response_model=list[SignalResponse])
def list_signals(
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = Query(100, le=1000),
    scope: SignalScopeMode = Query("all_active"),
    watchlist_id: int | None = Query(None),
    include_all: bool = Query(
        False, description="Include signals for symbols not in the active watchlist"
    ),
    completed_only: bool = Query(
        False, description="Only return signals with complete 5/10/20-bar outcomes and excursions"
    ),
    db: Session = Depends(get_db),
):
    """List historical signals with optional filters.

    By default, only signals for symbols in the active watchlist are returned
    — this prevents stale rows for deleted symbols from polluting the
    dashboard. Pass ``include_all=true`` to query across all symbols (used
    by research endpoints).
    """
    _, watchlist_symbols = _resolve_signal_scope(
        db, scope=scope, watchlist_id=watchlist_id, include_all=include_all
    )
    if watchlist_symbols == []:
        return []

    repo = SignalRepository(db)
    return repo.get_history(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        symbols=watchlist_symbols,
        completed_only=completed_only,
    )


@router.get("/research/signals", response_model=SignalResearchPage)
def get_signal_research_page(
    timeframe: str | None = None,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    scope: SignalScopeMode = Query("all_active"),
    watchlist_id: int | None = Query(None),
    completed_only: bool = Query(True),
    limit: int = Query(250, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """A page of research records with explicit population and coverage evidence."""
    resolved_scope, symbols = _resolve_signal_scope(
        db, scope=scope, watchlist_id=watchlist_id
    )
    start_time, end_time = _research_time_range(start_date, end_date)
    if symbols == []:
        rows, total = [], 0
    else:
        rows, total = SignalRepository(db).get_history_page(
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            symbols=symbols,
            completed_only=completed_only,
            limit=limit,
            offset=offset,
        )
    return SignalResearchPage(
        records=rows,
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(rows) < total,
        scope=resolved_scope,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/research/summary", response_model=SignalResearchSummary)
def get_signal_research_summary(
    timeframe: str | None = None,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    scope: SignalScopeMode = Query("all_active"),
    watchlist_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """Research metrics over every matching record.

    Performance is returned only for a single timeframe; without one, the
    response carries per-timeframe coverage and a note instead.
    """
    resolved_scope, symbols = _resolve_signal_scope(db, scope=scope, watchlist_id=watchlist_id)
    start_time, end_time = _research_time_range(start_date, end_date)
    if symbols == []:
        summary = {
            "recorded": 0,
            "complete": 0,
            "timeframe_coverage": [],
            "regime_coverage": {"with_regime": 0, "complete": 0},
            "performance": None,
        }
    else:
        summary = SignalRepository(db).research_summary(
            timeframe=timeframe, start_time=start_time, end_time=end_time, symbols=symbols
        )
    note = None if timeframe else (
        "Choose one timeframe to see performance: a 5-bar outcome is five minutes on 1m "
        "and five sessions on 1d, so returns across timeframes are not averaged together."
    )
    return SignalResearchSummary(
        scope=resolved_scope,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        performance_note=note,
        **summary,
    )


@router.get("/research/export")
def export_signal_research(
    timeframe: str | None = None,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    scope: SignalScopeMode = Query("all_active"),
    watchlist_id: int | None = Query(None),
    completed_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Export the full explicitly scoped research population as a CSV."""
    resolved_scope, symbols = _resolve_signal_scope(
        db, scope=scope, watchlist_id=watchlist_id
    )
    start_time, end_time = _research_time_range(start_date, end_date)
    import csv

    header = [
        "timestamp", "symbol", "timeframe", "trend_state", "market_regime",
        "raw_return_5b", "raw_return_10b", "raw_return_20b", "raw_mfe", "raw_mae",
        "signal_return_5b", "signal_return_10b", "signal_return_20b",
        "favorable_excursion", "adverse_excursion", "scope_mode", "scope_watchlists",
    ]

    def csv_line(values: list[object]) -> str:
        output = StringIO()
        csv.writer(output).writerow([_csv_value(value) for value in values])
        return output.getvalue()

    def generate():
        yield csv_line(header)
        if symbols == []:
            return
        rows = SignalRepository(db).iter_history(
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
            symbols=symbols,
            completed_only=completed_only,
        )
        for row in rows:
            signal = SignalResponse.model_validate(row)
            yield csv_line([
                format_edt_iso(signal.timestamp), signal.symbol, signal.timeframe,
                signal.trend_state or "", signal.market_regime or "", signal.return_5b,
                signal.return_10b, signal.return_20b, signal.mfe, signal.mae,
                directional_outcome(signal, "return_5b"), directional_outcome(signal, "return_10b"),
                directional_outcome(signal, "return_20b"), directional_outcome(signal, "mfe"),
                directional_outcome(signal, "mae"), resolved_scope.mode,
                "; ".join(resolved_scope.watchlist_names),
            ])

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=marketlens-signal-research.csv"},
    )


_DIRECTION_TREND_STATE = {"long": "bullish", "short": "bearish"}


@router.get("/research/excursions", response_model=ExcursionResponse)
def get_signal_excursions(
    symbol: str = Query(..., description="Ticker to condition on"),
    timeframe: str = Query(..., description="Signal timeframe; horizons differ across timeframes"),
    direction: Literal["long", "short"] = Query(..., description="Trade direction being planned"),
    strength_min: float | None = Query(None, ge=0.0, le=1.0),
    strength_max: float | None = Query(None, ge=0.0, le=1.0),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    min_sample: int = Query(DEFAULT_MIN_SAMPLE, ge=1, description="Below this, statistics are withheld"),
    db: Session = Depends(get_db),
):
    """Empirical excursion distribution for a planned trade.

    Answers "how far has this setup normally moved against the call before it
    worked, and how far did it run?" -- the adverse ``p75`` is an empirical stop
    distance and the favorable ``p50`` an empirical target, both grounded in
    what actually happened rather than in an indicator.

    Slices are often thin (many symbol/timeframe/direction combinations hold
    fewer than ``min_sample`` rows), so a too-thin request returns 200 with
    every statistic ``None`` plus a ``relaxation`` ladder -- strength band
    dropped, then dates, then pooled across symbols -- stopping at the first
    rung that clears ``min_sample``. Relaxed numbers never leak into the
    primary fields; the caller must read them from ``relaxation`` and say so.
    """
    if strength_min is not None and strength_max is not None and strength_min > strength_max:
        raise HTTPException(status_code=422, detail="strength_min must not exceed strength_max")
    sym = symbol.upper()
    trend_state = _DIRECTION_TREND_STATE[direction]
    start_time, end_time = _research_time_range(start_date, end_date)
    repo = SignalRepository(db)

    def measure(
        *,
        use_symbol: bool,
        use_strength: bool,
        use_dates: bool,
    ) -> tuple[dict, ExcursionFilters]:
        rows = repo.fetch_excursion_rows(
            symbol=sym if use_symbol else None,
            timeframe=timeframe,
            trend_state=trend_state,
            strength_min=strength_min if use_strength else None,
            strength_max=strength_max if use_strength else None,
            start_time=start_time if use_dates else None,
            end_time=end_time if use_dates else None,
        )
        filters = ExcursionFilters(
            symbol=sym if use_symbol else None,
            timeframe=timeframe,
            trend_state=trend_state,
            strength_min=strength_min if use_strength else None,
            strength_max=strength_max if use_strength else None,
            start_date=start_date if use_dates else None,
            end_date=end_date if use_dates else None,
        )
        return excursion_distribution(rows, min_sample=min_sample), filters

    primary, primary_filters = measure(use_symbol=True, use_strength=True, use_dates=True)

    relaxation: list[ExcursionRelaxation] = []
    if not primary["sufficient"]:
        has_strength = strength_min is not None or strength_max is not None
        has_dates = start_time is not None or end_time is not None
        # Widen one dimension at a time, cheapest signal loss first, and stop as
        # soon as a rung is trustworthy -- a rung that is still too thin is
        # reported so the caller can see the ladder was tried.
        rungs = [
            ("no_strength_band", f"{sym} {timeframe} {direction}, any strength", True, False, True, has_strength),
            ("no_date_range", f"{sym} {timeframe} {direction}, full history", True, False, False, has_dates or has_strength),
            ("all_symbols", f"all symbols, {timeframe} {direction}", False, False, False, True),
        ]
        for level, label, use_symbol, use_strength, use_dates, applicable in rungs:
            if not applicable:
                continue
            stats, filters = measure(
                use_symbol=use_symbol, use_strength=use_strength, use_dates=use_dates
            )
            relaxation.append(
                ExcursionRelaxation(level=level, label=label, filters=filters, **stats)
            )
            if stats["sufficient"]:
                break

    return ExcursionResponse(
        symbol=sym,
        timeframe=timeframe,
        direction=direction,
        filters=primary_filters,
        relaxation=relaxation,
        **primary,
    )


@router.get("/research/regime-performance", response_model=list[RegimePerformance])
def get_regime_performance(
    timeframe: str | None = Query(None, description="Limit to one timeframe; horizons differ across timeframes"),
    scope: SignalScopeMode = Query("all_active"),
    watchlist_id: int | None = Query(None),
    include_all: bool = Query(
        False, description="Include signals for symbols not in the active watchlist"
    ),
    db: Session = Depends(get_db),
):
    """Average forward returns by market regime.

    Only complete bullish/bearish outcomes are included, direction-adjusted.
    By default, only signals for symbols in the active
    watchlist are counted — pass ``include_all=true`` to include
    signals for symbols that have been removed from the watchlist
    (used by offline research).
    """
    _, watchlist_symbols = _resolve_signal_scope(
        db, scope=scope, watchlist_id=watchlist_id, include_all=include_all
    )
    if watchlist_symbols == []:
        return []
    repo = SignalRepository(db)
    return repo.get_performance_by_regime(symbols=watchlist_symbols, timeframe=timeframe)


@router.get("/research/count-by-regime", response_model=list[RegimeCount])
def get_signal_count_by_regime(
    timeframe: str | None = Query(None),
    scope: SignalScopeMode = Query("all_active"),
    watchlist_id: int | None = Query(None),
    include_all: bool = Query(
        False, description="Include signals for symbols not in the active watchlist"
    ),
    db: Session = Depends(get_db),
):
    """Count of historical signals grouped by market regime.

    By default, only counts signals for symbols in the active watchlist.
    """
    _, watchlist_symbols = _resolve_signal_scope(
        db, scope=scope, watchlist_id=watchlist_id, include_all=include_all
    )
    if watchlist_symbols == []:
        return []
    repo = SignalRepository(db)
    return repo.count_by_regime(symbols=watchlist_symbols, timeframe=timeframe)


@router.post("/backfill", response_model=BackfillResponse)
def trigger_backfill(
    batch_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Manually trigger outcome backfill.

    Records forward outcomes (5/10/20-bar returns, MFE, MAE) for signals
    that don't yet have them.
    """
    with _exclusive_manual_run():
        updated = signal_recorder.backfill_outcomes(batch_size=batch_size)
    logger.info("Manual outcome backfill: batch_size=%d updated=%d", batch_size, updated)
    return BackfillResponse(updated=updated)


@router.post("/record")
def record_signal(
    symbol: str | None = None,
    timeframe: str | None = None,
    trend_score: float | None = None,
    trend_state: str | None = None,
    strength: float | None = None,
    market_regime: str | None = None,
    price: float | None = None,
    timestamp: datetime | None = None,
    request: RecordSignalsRequest | None = Body(None),
    db: Session = Depends(get_db),
):
    """Record a signal for a symbol/timeframe manually.

    Two modes:
      * **Single record** — pass ``symbol`` and ``timeframe``: records one
        signal via ``signal_recorder.record_signal`` and returns
        ``{"status": "recorded", ...}`` or ``{"status": "duplicate_or_skipped"}``.
      * **Bulk from recent bars** — omit both: records a signal for every
        newly closed bar of every (symbol, timeframe) of the ingested
        symbols, or of the ``symbols`` in the JSON body (each must be
        ingested). Without ``confirm: true`` it only returns a preview of
        the resolved symbols and the number of (symbol, timeframe) pairs;
        with it, it records and returns ``{"status": "recorded",
        "recorded": N, ...}``. A bar still forming is left for a later call.

    The bulk mode is what the ingestion loop uses internally; the test
    suite validates it via ``record_from_recent_bars`` to ensure the
    endpoint round-trips through the same code path.
    """
    # Bulk mode: no explicit symbol/timeframe → record from recent bars
    # using the ingestion service's active symbol list.
    if symbol is None and timeframe is None:
        request = request or RecordSignalsRequest()
        ingested = [s.upper() for s in ingestion_service.symbols]
        if request.symbols is None:
            symbols = ingested
        else:
            symbols = list(dict.fromkeys(s.strip().upper() for s in request.symbols if s.strip()))
            unknown = [s for s in symbols if s not in ingested]
            if unknown or not symbols:
                raise HTTPException(
                    status_code=422,
                    detail=f"Only ingested symbols can be recorded; not ingested: {', '.join(unknown) or '(none given)'}",
                )
        pairs = (
            db.query(func.count())
            .select_from(
                db.query(BarModel.symbol, BarModel.timeframe)
                .filter(BarModel.symbol.in_(symbols))
                .distinct()
                .subquery()
            )
            .scalar()
        ) if symbols else 0
        scope = {"symbols": symbols, "pairs": int(pairs or 0)}
        if not request.confirm:
            return {"status": "preview", **scope}
        with _exclusive_manual_run():
            recorded = signal_recorder.record_from_recent_bars(symbols)
        logger.info("Manual signal recording: %d symbols, %d pairs, %d new rows", len(symbols), scope["pairs"], recorded)
        return {"status": "recorded", "recorded": recorded, **scope}

    # Single-record mode: explicit symbol/timeframe required.
    if symbol is None or timeframe is None:
        raise HTTPException(
            status_code=422,
            detail="Both 'symbol' and 'timeframe' must be provided for single-record mode",
        )
    sig = signal_recorder.record_signal(
        symbol=symbol,
        timeframe=timeframe,
        trend_score=trend_score,
        trend_state=trend_state,
        strength=strength,
        market_regime=market_regime,
        price=price,
        timestamp=timestamp,
    )
    if sig is None:
        return {"status": "duplicate_or_skipped"}
    return {
        "status": "recorded",
        "id": sig.id,
        "timestamp": _to_dashboard_tz(sig.timestamp).isoformat() if sig.timestamp else None,
    }


@router.get("/{signal_id}", response_model=SignalResponse)
def get_signal(signal_id: int, db: Session = Depends(get_db)):
    """Get a single signal by ID."""
    repo = SignalRepository(db)
    signal = repo.get_by_id(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


@router.get("/symbol/{symbol}/latest", response_model=dict[str, SignalResponse])
def get_latest_signals(symbol: str, db: Session = Depends(get_db)):
    """Get the most recent signal for each timeframe for a symbol."""
    repo = SignalRepository(db)
    latest = repo.get_latest_per_timeframe(symbol.upper(), ingestion_service.timeframes)
    if not latest:
        raise HTTPException(status_code=404, detail="No signals found for this symbol")
    return latest
