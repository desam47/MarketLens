"""
Trade journal entry — one row per manually recorded trade.

Screenshots are NOT stored here (too large for SQLite; they stay in
localStorage on the client and are re-attached after a sync fetch).
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.timezone import now_ny


class TradeJournalEntry(Base):
    __tablename__ = "trade_journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="planned")
    entry_date: Mapped[str] = mapped_column(String(32), nullable=False)
    exit_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Signal and market context stored as JSON strings.
    signal_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    market_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_ny)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_ny, onupdate=now_ny
    )
