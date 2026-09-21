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
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy.orm import Session

from backend.market_data.services.ingestion_service import ingestion_service
from backend.repositories.signal_repository import SignalRepository
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


# --- Endpoints -------------------------------------------------------------


@router.get("/", response_model=list[SignalResponse])
def list_signals(
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = Query(100, le=1000),
    include_all: bool = Query(
        False, description="Include signals for symbols not in the active watchlist"
    ),
    completed_only: bool = Query(
        False, description="Only return signals with completed outcomes (return_5b IS NOT NULL)"
    ),
    db: Session = Depends(get_db),
):
    """List historical signals with optional filters.

    By default, only signals for symbols in the active watchlist are returned
    — this prevents stale rows for deleted symbols from polluting the
    dashboard. Pass ``include_all=true`` to query across all symbols (used
    by research endpoints).
    """
    from backend.repositories.watchlist_repository import WatchlistRepository

    watchlist_symbols: list[str] = []
    if not include_all:
        wl_repo = WatchlistRepository(db)
        for wl in wl_repo.get_watchlists(active_only=True):
            syms = wl_repo.get_watchlist_symbols(wl.id, enabled_only=True)
            if syms:
                watchlist_symbols = [s.symbol.upper() for s in syms]
                break

    # If no watchlist has symbols, return empty rather than all historical rows.
    if not include_all and not watchlist_symbols:
        return []

    repo = SignalRepository(db)
    return repo.get_history(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        symbols=watchlist_symbols if not include_all else None,
        completed_only=completed_only,
    )


@router.get("/research/regime-performance", response_model=list[RegimePerformance])
def get_regime_performance(
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
    watchlist_symbols: list[str] | None = None
    if not include_all:
        from backend.repositories.watchlist_repository import WatchlistRepository

        wl_repo = WatchlistRepository(db)
        for wl in wl_repo.get_watchlists(active_only=True):
            syms = wl_repo.get_watchlist_symbols(wl.id, enabled_only=True)
            if syms:
                watchlist_symbols = [s.symbol.upper() for s in syms]
                break
        if not watchlist_symbols:
            return []  # no watchlist symbols → nothing to report
    repo = SignalRepository(db)
    return repo.get_performance_by_regime(symbols=watchlist_symbols)


@router.get("/research/count-by-regime", response_model=list[RegimeCount])
def get_signal_count_by_regime(
    include_all: bool = Query(
        False, description="Include signals for symbols not in the active watchlist"
    ),
    db: Session = Depends(get_db),
):
    """Count of historical signals grouped by market regime.

    By default, only counts signals for symbols in the active watchlist.
    """
    watchlist_symbols: list[str] | None = None
    if not include_all:
        from backend.repositories.watchlist_repository import WatchlistRepository

        wl_repo = WatchlistRepository(db)
        for wl in wl_repo.get_watchlists(active_only=True):
            syms = wl_repo.get_watchlist_symbols(wl.id, enabled_only=True)
            if syms:
                watchlist_symbols = [s.symbol.upper() for s in syms]
                break
        if not watchlist_symbols:
            return []  # no watchlist symbols → nothing to report
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
    db: Session = Depends(get_db),
):
    """Delete signals older than N days."""
    repo = SignalRepository(db)
    deleted = repo.delete_older_than(older_than_days)
    return {"deleted": deleted, "older_than_days": older_than_days}
