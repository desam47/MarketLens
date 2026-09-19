"""
AI Digest repository for data access operations.

Same thin-repository convention as AlertRepository — owns its own
SessionLocal when a session isn't passed in, closes it on ``close()``.
"""
from datetime import datetime

from sqlalchemy import desc

from backend.database import SessionLocal
from backend.models import AIDigest
from backend.utils.timezone import now_ny


class AIDigestRepository:
    """Repository for AIDigest read/write operations."""

    def __init__(self, db=None):
        self._owns_session = db is None
        self.db = db or SessionLocal()

    def close(self) -> None:
        if self._owns_session:
            self.db.close()

    def create(
        self,
        *,
        session: str,
        market_regime: str | None,
        narrative: str | None,
        payload: str | None,
        generated_at: datetime | None = None,
    ) -> AIDigest:
        digest = AIDigest(
            session=session,
            market_regime=market_regime,
            narrative=narrative,
            payload=payload,
            generated_at=generated_at or now_ny(),
        )
        self.db.add(digest)
        self.db.commit()
        self.db.refresh(digest)
        return digest

    def get_latest(self, session: str) -> AIDigest | None:
        return (
            self.db.query(AIDigest)
            .filter(AIDigest.session == session)
            .order_by(desc(AIDigest.generated_at))
            .first()
        )

    def exists_since(self, session: str, since: datetime) -> bool:
        """True if a ``session`` digest was generated at or after ``since`` (naive NY time)."""
        return (
            self.db.query(AIDigest.id)
            .filter(AIDigest.session == session, AIDigest.generated_at >= since)
            .first()
            is not None
        )

    def get_history(self, session: str | None = None, limit: int = 10) -> list[AIDigest]:
        q = self.db.query(AIDigest)
        if session is not None:
            q = q.filter(AIDigest.session == session)
        return q.order_by(desc(AIDigest.generated_at)).limit(limit).all()
