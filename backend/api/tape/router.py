"""
Tape (Time & Sales) analytics endpoints.

``GET /api/tape/{symbol}``        — current rolling snapshot
``GET /api/tape/{symbol}/bars``   — recent 1-second aggregate bars

503 when ``TAPE_ENABLED`` is off (the frontend card renders a
"tape streaming off" state on that).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from backend.config.settings import settings
from backend.utils.timezone import now_ny

router = APIRouter(prefix="/api/tape", tags=["tape"])


def _require_enabled() -> None:
    if not settings.tape.enabled:
        raise HTTPException(
            status_code=503,
            detail="Tape analytics are disabled (set TAPE_ENABLED=true).",
        )


@router.get("/{symbol}")
async def get_tape_snapshot(symbol: str):
    _require_enabled()
    from backend.api.tape.registry import get_tape_engine

    snap = get_tape_engine(symbol).get_snapshot()
    return {"symbol": symbol.upper(), "snapshot": snap, "as_of": now_ny().isoformat()}


@router.get("/{symbol}/bars")
async def get_tape_bars_endpoint(
    symbol: str,
    minutes: int = Query(default=15, ge=1, le=120),
    limit: int = Query(default=600, ge=1, le=3600),
):
    _require_enabled()
    from backend.database import SessionLocal
    from backend.repositories.tape_repository import get_tape_bars

    since = now_ny() - timedelta(minutes=minutes)
    db = SessionLocal()
    try:
        rows = get_tape_bars(db, symbol, since=since, limit=limit)
        return {
            "symbol": symbol.upper(),
            "bars": [
                {
                    "timestamp": r.timestamp.isoformat() if isinstance(r.timestamp, datetime) else str(r.timestamp),
                    "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                    "volume": r.volume, "buy_volume": r.buy_volume,
                    "sell_volume": r.sell_volume, "signed_volume": r.signed_volume,
                    "trade_count": r.trade_count, "block_count": r.block_count,
                    "vwap": r.vwap,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


__all__ = ["router"]
