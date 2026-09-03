"""
Watchlist data models for MarketLens
"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.database import Base
from backend.utils.timezone import now_ny


class Watchlist(Base):
    """Watchlist model"""
    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_ny)
    updated_at = Column(DateTime, default=now_ny, onupdate=now_ny)

    # Relationship to watchlist symbols
    symbols = relationship("WatchlistSymbol", back_populates="watchlist", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Watchlist(id={self.id}, name='{self.name}')>"

class WatchlistSymbol(Base):
    """WatchlistSymbol model - represents a symbol within a watchlist"""
    __tablename__ = "watchlist_symbols"

    id = Column(Integer, primary_key=True, index=True)
    watchlist_id = Column(Integer, ForeignKey("watchlists.id"), nullable=False)
    symbol = Column(String(10), nullable=False, index=True)  # Stock symbols are typically short
    is_enabled = Column(Boolean, default=True)
    added_at = Column(DateTime, default=now_ny)
    # Position for ordering/reordering symbols within the watchlist
    position = Column(Integer, default=0)
    # Optional notes about this symbol (e.g. "watching for breakout", "entry at $150")
    notes = Column(Text, nullable=True)
    # Entity classification for the symbol. Currently "stock" or "etf".
    # Nullable for backward-compat with rows that pre-date this column;
    # the API defaults to "stock" when null.
    entity_type = Column(String(16), nullable=True)

    # Relationship to watchlist
    watchlist = relationship("Watchlist", back_populates="symbols")

    def __repr__(self):
        return f"<WatchlistSymbol(id={self.id}, watchlist_id={self.watchlist_id}, symbol='{self.symbol}')>"
