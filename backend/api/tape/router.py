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
from backend.utils.timezone import format_edt_iso, now_ny

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
    # format_edt_iso attaches an explicit EDT/EST offset (project
    # convention for values crossing the frontend boundary) — a naive
    # isoformat() string would be parsed as browser-local time by
    # `new Date(...)`, silently shifting the displayed time for any
    # viewer outside America/New_York.
    return {"symbol": symbol.upper(), "snapshot": snap, "as_of": format_edt_iso(now_ny())}


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
                    "timestamp": r.timestamp.isoformat()
                    if isinstance(r.timestamp, datetime)
                    else str(r.timestamp),
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                    "buy_volume": r.buy_volume,
                    "sell_volume": r.sell_volume,
                    "signed_volume": r.signed_volume,
                    "trade_count": r.trade_count,
                    "block_count": r.block_count,
                    "vwap": r.vwap,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.get("/{symbol}/replay")
async def get_tick_replay(
    symbol: str,
    limit: int = Query(default=2_000, ge=1, le=10_000),
    start: str | None = Query(default=None, max_length=64),
    end: str | None = Query(default=None, max_length=64),
):
    """Return locally retained live events, oldest first, with no provider fetch."""
    _require_enabled()
    from backend.services.tick_replay import tick_replay_store

    events = tick_replay_store.get(symbol, limit, start=start, end=end)
    return {
        "symbol": symbol.upper(),
        "events": events,
        "retained": len(events),
        "start": start,
        "end": end,
        "retention_seconds": tick_replay_store.retention_seconds,
        "max_events_per_symbol": tick_replay_store.max_events_per_symbol,
    }


@router.get("/{symbol}/replay/signals")
async def get_tick_signal_replay(
    symbol: str,
    limit: int = Query(default=2_000, ge=1, le=10_000),
    warmup: int = Query(default=0, ge=0, le=500),
    start: str | None = Query(default=None, max_length=64),
    end: str | None = Query(default=None, max_length=64),
):
    """Reconstruct causal signal state from locally retained ticks."""
    _require_enabled()
    from backend.services.tick_replay import reconstruct_tick_signals, tick_replay_store

    events = tick_replay_store.get(symbol, limit, start=start, end=end)
    candles = reconstruct_tick_signals(symbol, events, warmup=warmup)
    return {
        "symbol": symbol.upper(),
        "timeframe": "1m",
        "retained_events": len(events),
        "start": start,
        "end": end,
        "reconstructed_candles": len(candles),
        "candles": candles,
    }


__all__ = ["router"]
