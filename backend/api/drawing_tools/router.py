"""
API endpoints for drawing tools (Phase 2.3.5).

CRUD for chart drawing annotations (trend lines, Fibonacci, rectangles, etc.).
Drawings are scoped to a symbol+timeframe.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import DrawingTool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drawing-tools", tags=["drawing-tools"])

VALID_DRAWING_TYPES = {
    "trend_line", "horizontal_line", "fib_retracement",
    "rectangle", "arrow", "text", "channel", "pitchfork", "gann_fan",
}
VALID_LINE_STYLES = {"solid", "dashed", "dotted"}


# ── Pydantic schemas ──────────────────────────────────────────────────────

class DrawingToolCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    timeframe: str = Field(..., min_length=1, max_length=10)
    drawing_type: str
    label: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    line_width: float | None = Field(default=None, ge=0.5, le=10)
    line_style: str | None = None
    font_size: int | None = Field(default=None, ge=8, le=72)
    opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    start_timestamp: str  # ISO 8601
    start_price: float
    end_timestamp: str | None = None
    end_price: float | None = None
    fib_levels: str | None = None  # comma-separated: "0,0.236,0.382,1.0"
    top_price: float | None = None
    bottom_price: float | None = None
    is_visible: bool = True
    is_locked: bool = False
    extend_left: bool = False
    extend_right: bool = False
    watchlist_id: int | None = None


class DrawingToolUpdate(BaseModel):
    label: str | None = None
    color: str | None = None
    line_width: float | None = None
    line_style: str | None = None
    font_size: int | None = None
    opacity: float | None = None
    start_timestamp: str | None = None
    start_price: float | None = None
    end_timestamp: str | None = None
    end_price: float | None = None
    fib_levels: str | None = None
    top_price: float | None = None
    bottom_price: float | None = None
    is_visible: bool | None = None
    is_locked: bool | None = None
    extend_left: bool | None = None
    extend_right: bool | None = None


class DrawingToolResponse(BaseModel):
    id: int
    watchlist_id: int | None
    symbol: str
    timeframe: str
    drawing_type: str
    label: str | None
    color: str | None
    line_width: float | None
    line_style: str | None
    font_size: int | None
    opacity: float | None
    start_timestamp: str
    start_price: float
    end_timestamp: str | None
    end_price: float | None
    fib_levels: str | None
    top_price: float | None
    bottom_price: float | None
    is_visible: bool
    is_locked: bool
    extend_left: bool
    extend_right: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Helpers ───────────────────────────────────────────────────────────────

def _to_response(model: DrawingTool) -> DrawingToolResponse:
    return DrawingToolResponse.model_validate(model)


def _validate_drawing_type(drawing_type: str) -> None:
    if drawing_type not in VALID_DRAWING_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid drawing_type '{drawing_type}'. "
            f"Must be one of: {sorted(VALID_DRAWING_TYPES)}",
        )


def _validate_line_style(line_style: str | None) -> None:
    if line_style is not None and line_style not in VALID_LINE_STYLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid line_style '{line_style}'. "
            f"Must be one of: {sorted(VALID_LINE_STYLES)}",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("", response_model=list[DrawingToolResponse])
def list_drawings(
    symbol: str | None = Query(default=None, max_length=10),
    timeframe: str | None = Query(default=None, max_length=10),
    watchlist_id: int | None = Query(default=None),
    drawing_type: str | None = Query(default=None),
    visible_only: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """List drawing tools, optionally filtered."""
    q = db.query(DrawingTool)
    if symbol is not None:
        q = q.filter(DrawingTool.symbol == symbol.upper())
    if timeframe is not None:
        q = q.filter(DrawingTool.timeframe == timeframe.lower())
    if watchlist_id is not None:
        q = q.filter(DrawingTool.watchlist_id == watchlist_id)
    else:
        q = q.filter(DrawingTool.watchlist_id.is_(None))
    if drawing_type is not None:
        q = q.filter(DrawingTool.drawing_type == drawing_type)
    if visible_only:
        q = q.filter(DrawingTool.is_visible == True)  # noqa: E712
    items = q.order_by(DrawingTool.created_at.desc()).all()
    return [_to_response(i) for i in items]


@router.post("", response_model=DrawingToolResponse, status_code=201)
def create_drawing(payload: DrawingToolCreate, db: Session = Depends(get_db)):
    """Create a new drawing tool."""
    _validate_drawing_type(payload.drawing_type)
    _validate_line_style(payload.line_style)

    drawing = DrawingTool(
        symbol=payload.symbol.upper(),
        timeframe=payload.timeframe.lower(),
        drawing_type=payload.drawing_type,
        label=payload.label,
        color=payload.color or "#3b82f6",
        line_width=payload.line_width or 1.0,
        line_style=payload.line_style,
        font_size=payload.font_size,
        opacity=payload.opacity or 1.0,
        start_timestamp=payload.start_timestamp,
        start_price=payload.start_price,
        end_timestamp=payload.end_timestamp,
        end_price=payload.end_price,
        fib_levels=payload.fib_levels,
        top_price=payload.top_price,
        bottom_price=payload.bottom_price,
        is_visible=payload.is_visible,
        is_locked=payload.is_locked,
        extend_left=payload.extend_left,
        extend_right=payload.extend_right,
        watchlist_id=payload.watchlist_id,
    )
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    return _to_response(drawing)


@router.get("/{drawing_id}", response_model=DrawingToolResponse)
def get_drawing(drawing_id: int, db: Session = Depends(get_db)):
    """Get a single drawing tool by ID."""
    drawing = db.query(DrawingTool).filter(DrawingTool.id == drawing_id).first()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return _to_response(drawing)


@router.patch("/{drawing_id}", response_model=DrawingToolResponse)
def update_drawing(
    drawing_id: int,
    payload: DrawingToolUpdate,
    db: Session = Depends(get_db),
):
    """Update an existing drawing tool. Only provided fields are changed."""
    drawing = db.query(DrawingTool).filter(DrawingTool.id == drawing_id).first()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "line_style" in update_data and update_data["line_style"] is not None:
        _validate_line_style(update_data["line_style"])

    for field, value in update_data.items():
        setattr(drawing, field, value)
    db.commit()
    db.refresh(drawing)
    return _to_response(drawing)


@router.delete("/{drawing_id}", status_code=204)
def delete_drawing(drawing_id: int, db: Session = Depends(get_db)):
    """Permanently delete a drawing tool."""
    drawing = db.query(DrawingTool).filter(DrawingTool.id == drawing_id).first()
    if not drawing:
        raise HTTPException(status_code=404, detail="Drawing not found")
    db.delete(drawing)
    db.commit()
    return None


@router.delete("", status_code=204)
def delete_drawings(
    symbol: str = Query(...),
    timeframe: str = Query(...),
    db: Session = Depends(get_db),
):
    """Delete all drawing tools for a given symbol+timeframe (bulk)."""
    deleted = (
        db.query(DrawingTool)
        .filter(
            DrawingTool.symbol == symbol.upper(),
            DrawingTool.timeframe == timeframe.lower(),
        )
        .delete()
    )
    db.commit()
    logger.info(f"Bulk-deleted {deleted} drawings for {symbol}/{timeframe}")
    return None
