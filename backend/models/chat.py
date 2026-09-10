"""
Chat session/message models (Version 4, AI feature 4: conversational
AI chat panel).

Mirrors the Alert/AlertTrigger parent/child pattern already in this
codebase (backend/models/alert.py) rather than inventing a new shape.
Symbol-scoped, not user-scoped — this is a single-tenant app with no
auth system, so one open chat thread per symbol is enough for v1.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.database import Base
from backend.utils.timezone import now_ny


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
    alert_trigger_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=now_ny)
    updated_at = Column(DateTime, default=now_ny, onupdate=now_ny)

    messages = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )

    def __repr__(self):
        return f"<ChatSession(id={self.id}, symbol={self.symbol})>"


class ChatMessage(Base):
    """One message in a ChatSession — either the user's question or
    the AI's reply."""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    role = Column(String(10), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=now_ny, index=True)

    session = relationship("ChatSession", back_populates="messages")

    def __repr__(self):
        return f"<ChatMessage(id={self.id}, session_id={self.session_id}, role={self.role})>"
