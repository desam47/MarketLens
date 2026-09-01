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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy.orm import Session

from backend.repositories.signal_repository import SignalRepository
from backend.services.signal_recorder import signal_recorder
from backend.market_data.services.ingestion_service import ingestion_service

from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["signals"])

# All timestamps are persisted in UTC. The dashboard lives in New York
# time, so every response converts UTC → America/New_York (which auto-
# handles EST/EDT). The browser receives an ISO string with the offset
# baked in (e.g. "2026-08-31T11:18:08.206223-04:00") so it never
# has to guess.
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> datetime | None:
    """Convert a UTC datetime to the dashboard's local time.

    Naive datetimes are assumed to be UTC (the canonical store). Aware
    datetimes in other zones are first converted to UTC, then to the
    dashboard zone. Returns None unchanged.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.astimezone(_DASHBOARD_TZ)


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
        converted = _to_dashboard_tz(value)
        return converted.isoformat() if converted else None


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
    repo = SignalRepository(db)
    return repo.count_by_regime()


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
    symbol: str,
    timeframe: str,
    trend_score: float | None = None,
    trend_state: str | None = None,
    strength: float | None = None,
    market_regime: str | None = None,
    price: float | None = None,
    timestamp: datetime | None = None,
):
    """Record a signal for a symbol/timeframe manually."""
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
    return {"status": "recorded", "id": sig.id, "timestamp": _to_dashboard_tz(sig.timestamp).isoformat() if sig.timestamp else None}


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
    return {"deleted": deleted}
