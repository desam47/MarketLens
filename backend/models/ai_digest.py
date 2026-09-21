"""
AI Digest model (Version 4, AI feature 2: daily/session AI digest).

One row per generated digest run (premarket or close). Mirrors
AIAnalysisJob's "one row per run, JSON blob for the rich part" shape.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AIDigest(Base):
    """A single generated digest — one row per premarket/close run.

    Attributes
    ----------
    session : str
        Which scheduled slot produced this digest, e.g. "premarket" or
        "close". Also the value passed to a manual
        ``POST /api/ai/digest/generate?session=...`` trigger.
    market_regime : str
        The regime string (RISK_ON/RISK_OFF/NEUTRAL/TRANSITION) at
        generation time — denormalized here so the latest-digest read
        endpoint doesn't need to re-derive it.
    narrative : str
        The AI-written 2-4 sentence summary.
    payload : str
        JSON-serialized structured digest payload (regime detail, top
        movers + per-mover blurbs, RSI extremes, MTF-alignment counts)
        — the full structured data the narrative was generated from.
    """

    __tablename__ = "ai_digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    market_regime: Mapped[str | None] = mapped_column(String(20), nullable=True)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    __table_args__ = (Index("ix_ai_digests_session_generated", "session", "generated_at"),)
