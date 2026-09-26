"""Repository for SavedTradePlan CRUD."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models.saved_trade_plan import SavedTradePlan


class SavedTradePlanRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_all(self) -> list[SavedTradePlan]:
        return self.db.query(SavedTradePlan).order_by(SavedTradePlan.created_at.desc()).all()

    def get_by_client_id(self, client_id: str) -> SavedTradePlan | None:
        return (
            self.db.query(SavedTradePlan)
            .filter(SavedTradePlan.client_id == client_id)
            .first()
        )

    def upsert(self, data: dict) -> SavedTradePlan:
        existing = self.get_by_client_id(data["client_id"])
        if existing:
            for key, value in data.items():
                if key != "client_id":
                    setattr(existing, key, value)
            self.db.commit()
            self.db.refresh(existing)
            return existing
        plan = SavedTradePlan(**data)
        self.db.add(plan)
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def delete_by_client_id(self, client_id: str) -> bool:
        existing = self.get_by_client_id(client_id)
        if not existing:
            return False
        self.db.delete(existing)
        self.db.commit()
        return True
