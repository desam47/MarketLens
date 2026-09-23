"""
Chat session/message models (Version 4, AI feature 4: conversational
AI chat panel).

Mirrors the Alert/AlertTrigger parent/child pattern already in this
codebase (backend/models/alert.py) rather than inventing a new shape.

A session has a ``scope`` (Universal AI Hub chat, 2026-09-10):
  - ``"universal"`` — not tied to any ticker; ``symbol`` holds the
    ``UNIVERSAL_SYMBOL`` sentinel (``symbol`` stays NOT NULL so no
    table rebuild was needed). Each turn resolves its own tickers
    from the message text.
  - ``"symbol"`` — one open thread for a specific ticker (the legacy
    default; still used by any symbol-scoped entry point).
  - ``"alert"`` — opened from a specific alert trigger row
    (``alert_trigger_id`` set).
Invariants are enforced in ChatRepository, not the DB (this codebase
declares no CHECK constraints anywhere).
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.database import Base
from backend.utils.timezone import now_ny

# Sentinel stored in ChatSession.symbol for a universal (no-ticker)
# session — keeps the column NOT NULL. Not a valid ticker, so it can
# never collide with a real symbol lookup.
UNIVERSAL_SYMBOL = "*"


class ChatSession(Base):
    """One open conversation thread, scoped to a symbol.

    ``alert_trigger_id`` is set when the chat was opened *from* a
    specific alert trigger row (e.g. "explain this alert") — nullable,
    since most sessions are opened from a symbol page with no
    particular trigger in mind. No FK constraint to alert_triggers is
    declared here (SQLite + this codebase's existing alert model
    don't enforce one either) — it's just an id to look the row up by
    when present.
    """

    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    # "universal" | "symbol" | "alert" — see module docstring. Legacy
    # rows (pre-2026-09-10) are back-filled to "symbol"/"alert".
    scope = Column(String(16), nullable=False, server_default="symbol", index=True)
    alert_trigger_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=now_ny)
    updated_at = Column(DateTime, default=now_ny, onupdate=now_ny)
    # JSON-encoded structured conversational state. Kept separate from the
    # prose transcript so follow-up references survive reloads without making
    # the model rediscover facts from clipped messages.
    planner_state = Column(Text, nullable=True)

    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )

    def __repr__(self):
        return f"<ChatSession(id={self.id}, scope={self.scope}, symbol={self.symbol})>"


class ChatMessage(Base):
    """One message in a ChatSession — either the user's question or
    the AI's reply."""

    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    role = Column(String(10), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    # Version 5.7 typed response blocks.  JSON is intentionally kept beside
    # the legacy prose so old rows/clients remain readable without migration
    # or tool reruns.
    response_blocks = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_ny, index=True)

    session = relationship("ChatSession", back_populates="messages")

    def __repr__(self):
        return f"<ChatMessage(id={self.id}, session_id={self.session_id}, role={self.role})>"


class ChatFeedback(Base):
    """Trader feedback on one assistant ChatMessage (Version 5, 5.7.8).

    One row per message — ``message_id`` is unique, so a later feedback
    call for the same message REPLACES the earlier one (upsert), matching
    a simple vote UI where the trader can change their mind rather than
    piling up a history of reactions. Never triggers automatic
    online model retraining; ``category``/``comment`` exist so a
    maintainer can manually turn an approved failure into a regression
    fixture (that export workflow is not built here — see
    ChatFeedbackRepository's docstring).
    """

    __tablename__ = "chat_feedback"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=False, unique=True, index=True)
    # "correct" | "incorrect" | "not_useful"
    rating = Column(String(16), nullable=False)
    # Optional, most meaningful for incorrect/not_useful: "wrong_data" |
    # "wrong_calculation" | "misunderstood_intent" | "stale_data" |
    # "poor_explanation" | "unsafe_action".
    category = Column(String(32), nullable=True)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_ny)
    updated_at = Column(DateTime, default=now_ny, onupdate=now_ny)

    message = relationship("ChatMessage")

    def __repr__(self):
        return f"<ChatFeedback(id={self.id}, message_id={self.message_id}, rating={self.rating})>"
