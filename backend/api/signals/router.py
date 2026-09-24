"""
API endpoints for historical signal storage and research queries (Phase 13).

Routes:
  GET    /api/signals/                  - paginated list with optional filters
  GET    /api/signals/{id}              - single signal by id
  GET    /api/signals/symbol/{symbol}   - latest signal per timeframe for a symbol
  GET    /api/signals/research/regime-performance   - avg returns by market regime
  GET    /api/signals/research/count-by-regime      - signal count by regime
  POST   /api/signals/backfill          - trigger outcome backfill manually
  DELETE /api/signals/old               - delete signals older than N days

All endpoints read/write through ``SignalRepository`` so the API and the
ingestion service share one path to the DB.
"""

import logging
from datetime import date, datetime, time, timedelta
from io import StringIO
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy.orm import Session

from backend.market_data.services.ingestion_service import ingestion_service
from backend.repositories.signal_repository import SignalRepository, directional_outcome
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


@router.get("/research/regime-performance", response_model=list[RegimePerformance])
def get_regime_performance(
    scope: SignalScopeMode = Query("all_active"),
    watchlist_id: int | None = Query(None),
    include_all: bool = Query(
        False, description="Include signals for symbols not in the active watchlist"
    ),
    db: Session = Depends(get_db),
):
    """Average forward returns by market regime.

    Only signals that have outcomes computed (return_5b IS NOT NULL)
    are included. By default, only signals for symbols in the active
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
    return repo.get_performance_by_regime(symbols=watchlist_symbols)


@router.get("/research/count-by-regime", response_model=list[RegimeCount])
def get_signal_count_by_regime(
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
    return repo.count_by_regime(symbols=watchlist_symbols)


@router.post("/backfill", response_model=BackfillResponse)
def trigger_backfill(
    batch_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Manually trigger outcome backfill.

    Records forward outcomes (5/10/20-bar returns, MFE, MAE) for signals
    that don't yet have them.
    """
    updated = signal_recorder.backfill_outcomes(batch_size=batch_size)
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
):
    """Record a signal for a symbol/timeframe manually.

    Two modes:
      * **Single record** — pass ``symbol`` and ``timeframe``: records one
        signal via ``signal_recorder.record_signal`` and returns
        ``{"status": "recorded", ...}`` or ``{"status": "duplicate_or_skipped"}``.
      * **Bulk from recent bars** — omit both: records a signal for every
        newly closed bar of every (symbol, timeframe) in the ingestion
        service (a bar still forming is left for a later call). Returns
        ``{"recorded": N}`` where ``N`` is the count of new rows written.

    The bulk mode is what the ingestion loop uses internally; the test
    suite validates it via ``record_from_recent_bars`` to ensure the
    endpoint round-trips through the same code path.
    """
    # Bulk mode: no explicit symbol/timeframe → record from recent bars
    # using the ingestion service's active symbol list.
    if symbol is None and timeframe is None:
        symbols = list(ingestion_service.symbols)
        recorded = signal_recorder.record_from_recent_bars(symbols)
        return {"recorded": recorded}

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


@router.delete("/old", response_model=dict)
def delete_old_signals(
    older_than_days: int = Query(30, ge=1),
    confirm: bool = Query(False, description="Must be true to delete retained research data"),
    db: Session = Depends(get_db),
):
    """Delete signals older than N days."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true after reviewing the deletion scope.",
        )
    repo = SignalRepository(db)
    deleted = repo.delete_older_than(older_than_days)
    return {"deleted": deleted, "older_than_days": older_than_days}
