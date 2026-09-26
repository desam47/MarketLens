"""Repository for TradeJournalEntry CRUD."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models.trade_journal_entry import TradeJournalEntry


class TradeJournalRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_all(self) -> list[TradeJournalEntry]:
        return self.db.query(TradeJournalEntry).order_by(TradeJournalEntry.created_at.desc()).all()

    def get_by_client_id(self, client_id: str) -> TradeJournalEntry | None:
        return (
            self.db.query(TradeJournalEntry)
            .filter(TradeJournalEntry.client_id == client_id)
            .first()
        )

    def upsert(self, data: dict) -> TradeJournalEntry:
        """Insert or replace by client_id."""
        existing = self.get_by_client_id(data["client_id"])
        if existing:
            for key, value in data.items():
                if key != "client_id":
                    setattr(existing, key, value)
            self.db.commit()
            self.db.refresh(existing)
            return existing
        entry = TradeJournalEntry(**data)
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def delete_by_client_id(self, client_id: str) -> bool:
        existing = self.get_by_client_id(client_id)
        if not existing:
            return False
        self.db.delete(existing)
        self.db.commit()
        return True
