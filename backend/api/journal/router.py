"""
Trade Journal API — persistent backend storage for journal entries.

Screenshots are excluded (too large for SQLite) and remain localStorage-only
on the client. All other fields are synced here.

Routes:
  GET    /api/journal/entries           list all entries
  POST   /api/journal/entries           upsert by client_id
  DELETE /api/journal/entries/{id}      remove one entry by client_id
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.repositories.trade_journal_repository import TradeJournalRepository

from ..dependencies import get_db

router = APIRouter(prefix="/api/journal", tags=["journal"])


class JournalEntryPayload(BaseModel):
    client_id: str
    symbol: str
    side: str
    status: str = "planned"
    entry_date: str
    exit_date: str | None = None
    quantity: float
    entry_price: float
    exit_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    thesis: str = ""
    review_notes: str = ""
    signal_context: dict[str, Any] | None = None
    market_context: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _serialize(entry) -> dict:
    return {
        "id": entry.client_id,
        "symbol": entry.symbol,
        "side": entry.side,
        "status": entry.status,
        "entryDate": entry.entry_date,
        "exitDate": entry.exit_date,
        "quantity": entry.quantity,
        "entryPrice": entry.entry_price,
        "exitPrice": entry.exit_price,
        "stopPrice": entry.stop_price,
        "targetPrice": entry.target_price,
        "thesis": entry.thesis,
        "reviewNotes": entry.review_notes,
        "screenshotDataUrl": None,
        "signalContext": json.loads(entry.signal_context_json) if entry.signal_context_json else None,
        "marketContext": json.loads(entry.market_context_json) if entry.market_context_json else None,
        "createdAt": entry.created_at.isoformat() if entry.created_at else None,
        "updatedAt": entry.updated_at.isoformat() if entry.updated_at else None,
    }


@router.get("/entries")
def list_entries(db: Session = Depends(get_db)):
    repo = TradeJournalRepository(db)
    return [_serialize(e) for e in repo.list_all()]


@router.post("/entries")
def upsert_entry(payload: JournalEntryPayload, db: Session = Depends(get_db)):
    repo = TradeJournalRepository(db)
    now = datetime.utcnow()
    data = {
        "client_id": payload.client_id,
        "symbol": payload.symbol.upper(),
        "side": payload.side,
        "status": payload.status,
        "entry_date": payload.entry_date,
        "exit_date": payload.exit_date,
        "quantity": payload.quantity,
        "entry_price": payload.entry_price,
        "exit_price": payload.exit_price,
        "stop_price": payload.stop_price,
        "target_price": payload.target_price,
        "thesis": payload.thesis,
        "review_notes": payload.review_notes,
        "signal_context_json": json.dumps(payload.signal_context) if payload.signal_context else None,
        "market_context_json": json.dumps(payload.market_context) if payload.market_context else None,
        "created_at": now,
        "updated_at": now,
    }
    entry = repo.upsert(data)
    return _serialize(entry)


@router.delete("/entries/{client_id}")
def delete_entry(client_id: str, db: Session = Depends(get_db)):
    repo = TradeJournalRepository(db)
    if not repo.delete_by_client_id(client_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"deleted": True}
