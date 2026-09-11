"""
Chat repository for data access operations (Version 4, AI feature 4).

Same thin-repository convention as AlertRepository/AIDigestRepository
— owns its own SessionLocal when a session isn't passed in.
"""
from backend.database import SessionLocal
from backend.models import ChatMessage, ChatSession
from backend.models.chat import UNIVERSAL_SYMBOL
from backend.utils.timezone import now_ny


def _derive_scope(symbol: str | None, alert_trigger_id: int | None) -> str:
    """Classify a session from its inputs (see backend/models/chat.py)."""
    if alert_trigger_id is not None:
        return "alert"
    if symbol and symbol != UNIVERSAL_SYMBOL:
        return "symbol"
    return "universal"


class ChatRepository:
    """Repository for ChatSession/ChatMessage CRUD operations."""

    def __init__(self, db=None):
        self._owns_session = db is None
        self.db = db or SessionLocal()

    def close(self) -> None:
        if self._owns_session:
            self.db.close()

    # --- Sessions ---------------------------------------------------

    def create_session(
        self,
        symbol: str | None = None,
        alert_trigger_id: int | None = None,
        scope: str | None = None,
    ) -> ChatSession:
        """Create a chat session.

        ``symbol=None`` (or ``"*"``) creates a *universal* session —
        not tied to any ticker; each turn resolves its own symbols from
        the message text. ``scope`` is derived from the inputs when not
        given (see :func:`_derive_scope`). ``symbol`` stays NOT NULL in
        the DB, so a universal session stores the ``UNIVERSAL_SYMBOL``
        sentinel.
        """
        scope = scope or _derive_scope(symbol, alert_trigger_id)
        sym = (symbol or UNIVERSAL_SYMBOL).upper()
        session = ChatSession(symbol=sym, alert_trigger_id=alert_trigger_id, scope=scope)
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_or_create_open_session(
        self,
        symbol: str | None = None,
        alert_trigger_id: int | None = None,
        scope: str | None = None,
    ) -> ChatSession:
        """Return the most recent open session for this scope, or create one.

        - ``scope="universal"`` (``symbol`` omitted / ``"*"``): one open
          universal thread, reused across every no-ticker / multi-ticker
          turn.
        - ``scope="symbol"``: one open thread per ticker (the legacy
          shape). The ``scope`` filter means a symbol lookup can never
          return the universal thread.
        - ``alert_trigger_id`` given and no existing session for this
          symbol already carries it: a NEW session is created rather
          than reusing an unrelated one — a chat opened from a specific
          alert should see that alert's context from the first message.
        """
        scope = scope or _derive_scope(symbol, alert_trigger_id)

        if scope == "universal":
            existing = (
                self.db.query(ChatSession)
                .filter(ChatSession.scope == "universal")
                .order_by(ChatSession.updated_at.desc())
                .first()
            )
            return existing or self.create_session(scope="universal")

        sym = (symbol or "").upper()
        existing = (
            self.db.query(ChatSession)
            .filter(ChatSession.symbol == sym, ChatSession.scope == scope)
            .order_by(ChatSession.updated_at.desc())
            .first()
        )
        if existing is not None:
            if alert_trigger_id is None or existing.alert_trigger_id == alert_trigger_id:
                return existing
        return self.create_session(sym, alert_trigger_id=alert_trigger_id, scope=scope)

    def get_session(self, session_id: int) -> ChatSession | None:
        return self.db.query(ChatSession).filter(ChatSession.id == session_id).first()

    def delete_sessions(
        self,
        *,
        scope: str | None = None,
        alert_trigger_id: int | None = None,
    ) -> tuple[int, int]:
        """Delete chat sessions and their messages.

        Filters are ANDed; no filter deletes every chat session. Returns
        ``(sessions_deleted, messages_deleted)``. Messages are removed
        explicitly (SQLite declares no ON DELETE CASCADE) before the
        sessions.
        """
        q = self.db.query(ChatSession.id)
        if scope is not None:
            q = q.filter(ChatSession.scope == scope)
        if alert_trigger_id is not None:
            q = q.filter(ChatSession.alert_trigger_id == alert_trigger_id)
        ids = [row[0] for row in q.all()]
        if not ids:
            return (0, 0)
        msgs = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_id.in_(ids))
            .delete(synchronize_session=False)
        )
        sess = (
            self.db.query(ChatSession)
            .filter(ChatSession.id.in_(ids))
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return (int(sess), int(msgs))

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
