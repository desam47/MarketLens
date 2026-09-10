"""
Chat repository for data access operations (Version 4, AI feature 4).

Same thin-repository convention as AlertRepository/AIDigestRepository
— owns its own SessionLocal when a session isn't passed in.
"""
from backend.database import SessionLocal
from backend.models import ChatMessage, ChatSession
from backend.utils.timezone import now_ny


class ChatRepository:
    """Repository for ChatSession/ChatMessage CRUD operations."""

    def __init__(self, db=None):
        self._owns_session = db is None
        self.db = db or SessionLocal()

    def close(self) -> None:
        if self._owns_session:
            self.db.close()

    # --- Sessions ---------------------------------------------------

    def create_session(self, symbol: str, alert_trigger_id: int | None = None) -> ChatSession:
        session = ChatSession(symbol=symbol.upper(), alert_trigger_id=alert_trigger_id)
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_or_create_open_session(
        self, symbol: str, alert_trigger_id: int | None = None
    ) -> ChatSession:
        """Return the most recent session for ``symbol``, or create one.

        One open chat thread per symbol is enough for v1 (no
        multi-session-per-symbol UI, no "New chat" button) — see the
        Version 4 plan's Feature 4 scope. If ``alert_trigger_id`` is
        given and no existing session for this symbol already has it
        set, a NEW session is created rather than reusing an unrelated
        one — a chat opened from a specific alert should see that
        alert's context from the first message.
        """
        sym = symbol.upper()
        existing = (
            self.db.query(ChatSession)
            .filter(ChatSession.symbol == sym)
            .order_by(ChatSession.updated_at.desc())
            .first()
        )
        if existing is not None:
            if alert_trigger_id is None or existing.alert_trigger_id == alert_trigger_id:
                return existing
        return self.create_session(sym, alert_trigger_id=alert_trigger_id)

    def get_session(self, session_id: int) -> ChatSession | None:
        return self.db.query(ChatSession).filter(ChatSession.id == session_id).first()

    # --- Messages -----------------------------------------------------

    def add_message(self, session_id: int, role: str, content: str) -> ChatMessage:
        message = ChatMessage(session_id=session_id, role=role, content=content)
        self.db.add(message)
        session = self.get_session(session_id)
        if session is not None:
            session.updated_at = now_ny()
        self.db.commit()
        self.db.refresh(message)
        return message

    def get_messages(self, session_id: int, limit: int = 50) -> list[ChatMessage]:
        """Most recent ``limit`` messages, oldest first (transcript order)."""
        rows = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))
