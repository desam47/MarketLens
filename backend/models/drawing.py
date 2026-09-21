"""
Drawing tools models for MarketLens (Phase 2.3.5).
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

if TYPE_CHECKING:
    from backend.models.watchlist import Watchlist  # noqa: F401


class DrawingTool(Base):
    """A single drawing annotation on a chart.

    Drawings are anchored to a symbol+timeframe and stored with their
    coordinates as ISO timestamp strings and price values so they are
    resolution-independent.
    """

    __tablename__ = "drawing_tools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Owner: null = global, or a watchlist_id.
    watchlist_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("watchlists.id"), nullable=True
    )

    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)

    # Drawing type: 'trend_line', 'horizontal_line', 'fib_retracement',
    # 'rectangle', 'arrow', 'text', 'channel', 'pitchfork', 'gann_fan'
    drawing_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # Display label
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True, default="#3b82f6")
    line_width: Mapped[float | None] = mapped_column(Float, nullable=True, default=1.0)
    line_style: Mapped[str | None] = mapped_column(String(20), nullable=True)
    font_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opacity: Mapped[float | None] = mapped_column(Float, nullable=True, default=1.0)

    # Start point (always required)
    start_timestamp: Mapped[str] = mapped_column(String(30), nullable=False)
    start_price: Mapped[float] = mapped_column(Float, nullable=False)

    # End point (required for lines, optional for arrows/text)
    end_timestamp: Mapped[str | None] = mapped_column(String(30), nullable=True)
    end_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # For Fibonacci retracements: comma-separated levels
    fib_levels: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # For rectangles: top and bottom prices (in addition to start/end)
    top_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    bottom_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Whether the drawing is visible (can be toggled off without deleting)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    # Locked drawings cannot be moved by the user in the UI
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    # Extending a line beyond its defined endpoints
    extend_left: Mapped[bool] = mapped_column(Boolean, default=False)
    extend_right: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationship
    watchlist: Mapped[Optional["Watchlist"]] = relationship(
        "Watchlist", foreign_keys=[watchlist_id]
    )

    def __repr__(self):
        return (
            f"<DrawingTool(id={self.id}, symbol='{self.symbol}', "
            f"type='{self.drawing_type}', label='{self.label}')>"
        )
