"""
Chat repository for data access operations (Version 4, AI feature 4).

Same thin-repository convention as AlertRepository/AIDigestRepository
— owns its own SessionLocal when a session isn't passed in.
"""

import json

from backend.database import SessionLocal
from backend.models import (
    ChatFeedback,
    ChatMessage,
    ChatRegressionFixture,
    ChatSession,
    ResearchNotebook,
    ResearchNotebookItem,
)
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

    def set_planner_state(self, session_id: int, state_json: str) -> None:
        session = self.get_session(session_id)
        if session is None:
            return
        session.planner_state = state_json
        session.updated_at = now_ny()
        self.db.commit()

    def delete_sessions(
        self,
        *,
        scope: str | None = None,
        alert_trigger_id: int | None = None,
    ) -> tuple[int, int]:
        """Delete chat sessions and their messages.

        Filters are ANDed; no filter deletes every chat session. Returns
        ``(sessions_deleted, messages_deleted)``. Feedback on those
        messages goes with them, then the messages, then the sessions
        (SQLite declares no ON DELETE CASCADE). Regression fixtures and
        notebook items are self-contained copies and are kept; message ids
        are never reused (AUTOINCREMENT), so their ``message_id`` can't
        attach to a later message.
        """
        q = self.db.query(ChatSession.id)
        if scope is not None:
            q = q.filter(ChatSession.scope == scope)
        if alert_trigger_id is not None:
            q = q.filter(ChatSession.alert_trigger_id == alert_trigger_id)
        ids = [row[0] for row in q.all()]
        if not ids:
            return (0, 0)
        message_ids = self.db.query(ChatMessage.id).filter(ChatMessage.session_id.in_(ids))
        self.db.query(ChatFeedback).filter(ChatFeedback.message_id.in_(message_ids)).delete(
            synchronize_session=False
        )
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

    def add_message(
        self,
        session_id: int,
        role: str,
        content: str,
        response_blocks: list[dict] | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            response_blocks=(json.dumps(response_blocks, separators=(",", ":")) if response_blocks else None),
        )
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

    def get_message(self, message_id: int) -> ChatMessage | None:
        return self.db.query(ChatMessage).filter(ChatMessage.id == message_id).first()

    # --- Feedback (5.7.8) --------------------------------------------

    def set_feedback(
        self,
        message_id: int,
        rating: str,
        category: str | None = None,
        comment: str | None = None,
    ) -> ChatFeedback:
        """Upsert feedback for one message. A later call for the same
        message_id REPLACES the earlier row (see ChatFeedback's
        docstring) — a simple vote UI where the trader can change their
        mind, not a growing reaction history."""
        existing = self.db.query(ChatFeedback).filter(ChatFeedback.message_id == message_id).first()
        if existing is not None:
            existing.rating = rating
            existing.category = category
            existing.comment = comment
            existing.updated_at = now_ny()
            feedback = existing
        else:
            feedback = ChatFeedback(message_id=message_id, rating=rating, category=category, comment=comment)
            self.db.add(feedback)
        self.db.commit()
        self.db.refresh(feedback)
        return feedback

    def get_feedback_for_messages(self, message_ids: list[int]) -> dict[int, ChatFeedback]:
        """Batch-fetch feedback for several messages, keyed by
        message_id — one query for a whole transcript instead of N+1."""
        if not message_ids:
            return {}
        rows = self.db.query(ChatFeedback).filter(ChatFeedback.message_id.in_(message_ids)).all()
        return {row.message_id: row for row in rows}

    # --- Approved regression fixtures (5.7.8) ------------------------

    def create_regression_fixture(self, message_id: int) -> ChatRegressionFixture | None:
        message = self.get_message(message_id)
        if message is None or message.role != "assistant":
            return None
        existing = self.db.query(ChatRegressionFixture).filter(ChatRegressionFixture.message_id == message_id).first()
        if existing is not None:
            return existing
        feedback = self.db.query(ChatFeedback).filter(ChatFeedback.message_id == message_id).first()
        if feedback is None or feedback.rating not in {"incorrect", "not_useful"}:
            return None
        previous = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_id == message.session_id, ChatMessage.id < message.id, ChatMessage.role == "user")
            .order_by(ChatMessage.id.desc())
            .first()
        )
        if previous is None:
            return None
        fixture = ChatRegressionFixture(
            message_id=message_id,
            prompt=previous.content,
            response=message.content,
            response_blocks=message.response_blocks,
            rating=feedback.rating,
            category=feedback.category,
            comment=feedback.comment,
            status="approved",
        )
        self.db.add(fixture)
        self.db.commit()
        self.db.refresh(fixture)
        return fixture

    def get_regression_fixtures(self, limit: int = 100) -> list[ChatRegressionFixture]:
        return (
            self.db.query(ChatRegressionFixture)
            .order_by(ChatRegressionFixture.created_at.desc())
            .limit(limit)
            .all()
        )

    # --- Server-backed research notebooks (5.7.10) -------------------

    def list_notebooks(self, client_key: str) -> list[ResearchNotebook]:
        return (
            self.db.query(ResearchNotebook)
            .filter(ResearchNotebook.client_key == client_key)
            .order_by(ResearchNotebook.updated_at.desc())
            .all()
        )

    def create_notebook(self, client_key: str, name: str) -> ResearchNotebook:
        notebook = ResearchNotebook(client_key=client_key, name=name.strip()[:120] or "Market research")
        self.db.add(notebook)
        self.db.commit()
        self.db.refresh(notebook)
        return notebook

    def rename_notebook(self, notebook_id: int, client_key: str, name: str) -> ResearchNotebook | None:
        """Rename a notebook only when it belongs to the requesting browser."""
        notebook = self.get_notebook(notebook_id, client_key)
        if notebook is None:
            return None
        notebook.name = name.strip()[:120] or "Market research"
        notebook.updated_at = now_ny()
        self.db.commit()
        self.db.refresh(notebook)
        return notebook

    def delete_notebook(self, notebook_id: int, client_key: str) -> bool:
        """Delete an owned notebook and its saved-answer snapshots."""
        notebook = self.get_notebook(notebook_id, client_key)
        if notebook is None:
            return False
        self.db.delete(notebook)
        self.db.commit()
        return True

    def get_notebook(self, notebook_id: int, client_key: str | None = None) -> ResearchNotebook | None:
        query = self.db.query(ResearchNotebook).filter(ResearchNotebook.id == notebook_id)
        if client_key is not None:
            query = query.filter(ResearchNotebook.client_key == client_key)
        return query.first()

    def save_notebook_item(
        self,
        notebook_id: int,
        *,
        message_id: int,
        question: str,
        answer: str,
        response_blocks: list[dict] | None,
        symbols: list[str],
        content_types: list[str],
        evidence_timestamps: list[str],
        stale: bool,
        material_change_detected: bool = False,
    ) -> ResearchNotebookItem | None:
        notebook = self.get_notebook(notebook_id)
        if notebook is None:
            return None
        item = (
            self.db.query(ResearchNotebookItem)
            .filter(ResearchNotebookItem.notebook_id == notebook_id, ResearchNotebookItem.message_id == message_id)
            .first()
        )
        if item is None:
            item = ResearchNotebookItem(notebook_id=notebook_id, message_id=message_id)
            self.db.add(item)
        item.question = question[:2000]
        item.answer = answer
        item.response_blocks = json.dumps(response_blocks or [], separators=(",", ":"))
        item.symbols = json.dumps(symbols[:20], separators=(",", ":"))
        item.content_types = json.dumps(content_types[:20], separators=(",", ":"))
        item.evidence_timestamps = json.dumps(evidence_timestamps[:50], separators=(",", ":"))
        item.stale = int(stale)
        item.material_change_detected = int(material_change_detected)
        notebook.updated_at = now_ny()
        self.db.commit()
        self.db.refresh(item)
        return item

    def delete_notebook_item(self, notebook_id: int, item_id: int, client_key: str) -> bool:
        """Remove one saved answer, scoped through the owning notebook."""
        notebook = self.get_notebook(notebook_id, client_key)
        if notebook is None:
            return False
        item = (
            self.db.query(ResearchNotebookItem)
            .filter(
                ResearchNotebookItem.id == item_id,
                ResearchNotebookItem.notebook_id == notebook.id,
            )
            .first()
        )
        if item is None:
            return False
        self.db.delete(item)
        self.db.commit()
        return True
