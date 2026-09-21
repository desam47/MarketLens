"""
Custom indicator models for MarketLens (Phase 2.3.4).
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

if TYPE_CHECKING:
    from backend.models.watchlist import Watchlist  # noqa: F401


class CustomIndicator(Base):
    """A user-defined indicator with a name, formula/expression, and display config.

    Indicators are stored per user (via watchlist_id or globally) and can be
    applied to any symbol+timeframe at chart render time.
    """
    __tablename__ = "custom_indicators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Owner: null = global (available to all), or a watchlist_id.
    watchlist_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("watchlists.id"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Short slug used in URL params or chart legend.
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Formula type: 'sma', 'ema', 'rsi', 'macd', 'bollinger', 'atr', 'custom'
    formula_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="custom"
    )
    # JSON-encoded parameters: e.g. {"period": 20, "field": "close"}
    parameters: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Display styling
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    line_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    line_style: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Whether to show as a separate pane below the main chart
    separate_pane: Mapped[bool] = mapped_column(Boolean, default=False)
    pane_height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Indicator overlays vs separate panes
    is_overlay: Mapped[bool] = mapped_column(Boolean, default=True)

    # Display ordering within a chart
    z_index: Mapped[int] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationship
    watchlist: Mapped[Optional["Watchlist"]] = relationship(
        "Watchlist", foreign_keys=[watchlist_id]
    )

    def __repr__(self):
        return f"<CustomIndicator(id={self.id}, name='{self.name}', type='{self.formula_type}')>"
