"""
Repository for ``tape_bars`` — 1-second Time & Sales aggregates written
by ``TapeEngine``. Short retention (``TAPE_RETENTION_DAYS``).
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.models.market_data_sql import TapeBarModel

logger = logging.getLogger(__name__)

_FIELDS = (
    "open", "high", "low", "close", "volume", "buy_volume", "sell_volume",
    "signed_volume", "trade_count", "block_count", "vwap",
)


def upsert_tape_bars(db: Session, rows: list[dict]) -> int:
    """Bulk upsert 1-second tape bars keyed on (symbol, timestamp)."""
    if not rows:
        return 0
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    values = [
        {
            "symbol": r["symbol"].upper(),
            "timestamp": r["timestamp"],
            "provider": r.get("provider", "webull_stream"),
            **{f: r.get(f) for f in _FIELDS},
        }
        for r in rows
    ]
    stmt = sqlite_insert(TapeBarModel).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "timestamp"],
        set_={f: getattr(stmt.excluded, f) for f in _FIELDS},
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def get_tape_bars(
    db: Session, symbol: str, since: datetime | None = None, limit: int = 600
) -> list[TapeBarModel]:
    q = db.query(TapeBarModel).filter(TapeBarModel.symbol == symbol.upper())
    if since is not None:
        q = q.filter(TapeBarModel.timestamp >= since)
    return list(
        q.order_by(TapeBarModel.timestamp.desc()).limit(limit).all()
    )[::-1]


def prune_tape_bars(db: Session, cutoff: datetime) -> int:
    """Delete tape bars older than ``cutoff``. Returns rows deleted."""
    result = db.execute(delete(TapeBarModel).where(TapeBarModel.timestamp < cutoff))
    db.commit()
    return result.rowcount or 0
