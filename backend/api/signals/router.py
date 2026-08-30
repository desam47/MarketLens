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
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.repositories.signal_repository import SignalRepository
from backend.services.signal_recorder import signal_recorder

from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["signals"])


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
    db: Session = Depends(get_db),
):
    """List historical signals with optional filters."""
    repo = SignalRepository(db)
    return repo.get_history(
        symbol=symbol, timeframe=timeframe, limit=limit
    )


@router.get("/research/regime-performance", response_model=list[RegimePerformance])
def get_regime_performance(db: Session = Depends(get_db)):
    """Average forward returns by market regime.

    Only signals that have outcomes computed (return_5b IS NOT NULL)
    are included.
    """
    repo = SignalRepository(db)
    return repo.get_performance_by_regime()


@router.get("/research/count-by-regime", response_model=list[RegimeCount])
def get_signal_count_by_regime(db: Session = Depends(get_db)):
    """Count of signals by market regime."""
    repo = SignalRepository(db)
    return repo.count_by_regime()


@router.post("/backfill", response_model=BackfillResponse)
def trigger_backfill(batch_size: int = Query(50, le=500)):
    """Manually trigger outcome backfill.

    Useful for ad-hoc research after seeding historical bars. The
    ingestion service also calls this on a 90-second schedule.
    """
    updated = signal_recorder.backfill_outcomes(batch_size=batch_size)
    return BackfillResponse(updated=updated)


@router.post("/record")
def record_now(symbols: list[str] | None = None, timeframes: list[str] | None = None):
    """Manually trigger a recording pass for the given symbols/timeframes.

    If not provided, uses the ingestion service's tracked symbol/timeframe
    list. Useful for backfilling signals when new bars land.
    """
    if not symbols or not timeframes:
        from ...market_data.services.ingestion_service import ingestion_service
        symbols = symbols or ingestion_service.symbols
        timeframes = timeframes or ingestion_service.timeframes
    recorded = signal_recorder.record_from_recent_bars(symbols, timeframes)
    return {"recorded": recorded}


@router.get("/{signal_id}", response_model=SignalResponse)
def get_signal(signal_id: int, db: Session = Depends(get_db)):
    repo = SignalRepository(db)
    signal = repo.get_by_id(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


@router.get("/symbol/{symbol}/latest", response_model=dict[str, SignalResponse])
def get_latest_signals_for_symbol(
    symbol: str, db: Session = Depends(get_db)
):
    """Latest signal per timeframe for a given symbol."""
    sym = symbol.upper()
    # Use a union: fetch the most recent signal for each timeframe the
    # caller cares about. We don't have an explicit list, so we lean on
    # the ingestion service's configured timeframes.
    from ...market_data.services.ingestion_service import ingestion_service
    repo = SignalRepository(db)
    out: dict[str, SignalResponse] = {}
    for tf in ingestion_service.timeframes:
        sig = repo.get_latest(sym, tf)
        if sig is not None:
            out[tf] = SignalResponse.model_validate(sig)
    if not out:
        raise HTTPException(
            status_code=404, detail=f"No signals found for {sym}"
        )
    return out


@router.delete("/old", response_model=dict)
def delete_old_signals(days: int = Query(90, ge=1), db: Session = Depends(get_db)):
    """Delete signals older than N days. Returns count removed."""
    repo = SignalRepository(db)
    count = repo.delete_older_than(days)
    return {"deleted": count, "older_than_days": days}
