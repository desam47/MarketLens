"""
API endpoints for custom indicators (Phase 2.3.4).

CRUD for user-defined indicators. Indicators are scoped either globally
(watchlist_id is null) or to a single watchlist.
"""

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import CustomIndicator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/custom-indicators", tags=["custom-indicators"])

VALID_FORMULA_TYPES = {
    "sma",
    "ema",
    "rsi",
    "macd",
    "bollinger",
    "atr",
    "vwap",
    "stdev",
    "obv",
    "mfi",
    "stochastic",
    "williams_r",
    "cci",
    "adx",
    "aroon",
    "custom",
}


# ── Pydantic schemas ──────────────────────────────────────────────────────


class CustomIndicatorCreate(BaseModel):
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=50, pattern=r"^[a-z0-9_-]+$")
    description: str | None = None
    formula_type: str = Field(default="custom")
    parameters: dict[str, Any] = Field(default_factory=dict)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    line_width: float | None = None
    line_style: str | None = None
    separate_pane: bool = False
    pane_height: int | None = None
    is_overlay: bool = True
    z_index: int = 0
    watchlist_id: int | None = None


class CustomIndicatorUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    formula_type: str | None = None
    parameters: dict[str, Any] | None = None
    color: str | None = None
    line_width: float | None = None
    line_style: str | None = None
    separate_pane: bool | None = None
    pane_height: int | None = None
    is_overlay: bool | None = None
    z_index: int | None = None
    is_active: bool | None = None


class CustomIndicatorResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    formula_type: str
    parameters: dict[str, Any]
    color: str | None
    line_width: float | None
    line_style: str | None
    separate_pane: bool
    pane_height: int | None
    is_overlay: bool
    z_index: int
    is_active: bool
    watchlist_id: int | None
    created_at: datetime
    updated_at: datetime


# ── Helpers ───────────────────────────────────────────────────────────────


def _to_response(model: CustomIndicator) -> CustomIndicatorResponse:
    params = {}
    if model.parameters:
        try:
            params = json.loads(model.parameters)
        except (json.JSONDecodeError, TypeError):
            params = {}
    return CustomIndicatorResponse(
        id=model.id,
        name=model.name,
        slug=model.slug,
        description=model.description,
        formula_type=model.formula_type,
        parameters=params,
        color=model.color,
        line_width=model.line_width,
        line_style=model.line_style,
        separate_pane=model.separate_pane,
        pane_height=model.pane_height,
        is_overlay=model.is_overlay,
        z_index=model.z_index,
        is_active=model.is_active,
        watchlist_id=model.watchlist_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _validate_formula_type(formula_type: str) -> None:
    if formula_type not in VALID_FORMULA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid formula_type '{formula_type}'. Must be one of: {sorted(VALID_FORMULA_TYPES)}",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("", response_model=list[CustomIndicatorResponse])
def list_indicators(
    watchlist_id: int | None = Query(default=None, description="Filter by watchlist"),
    active_only: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """List custom indicators. By default returns global + active indicators."""
    q = db.query(CustomIndicator)
    if watchlist_id is not None:
        q = q.filter(CustomIndicator.watchlist_id == watchlist_id)
    else:
        # Return global indicators + those belonging to a watchlist the user
        # owns. For now we just return globals.
        q = q.filter(CustomIndicator.watchlist_id.is_(None))
    if active_only:
        q = q.filter(CustomIndicator.is_active == True)  # noqa: E712
    items = q.order_by(CustomIndicator.z_index, CustomIndicator.name).all()
    return [_to_response(i) for i in items]


@router.post("", response_model=CustomIndicatorResponse, status_code=201)
def create_indicator(payload: CustomIndicatorCreate, db: Session = Depends(get_db)):
    """Create a new custom indicator."""
    _validate_formula_type(payload.formula_type)

    # Slug uniqueness check.
    existing = db.query(CustomIndicator).filter(CustomIndicator.slug == payload.slug).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"slug '{payload.slug}' already in use")

    indicator = CustomIndicator(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        formula_type=payload.formula_type,
        parameters=json.dumps(payload.parameters),
        color=payload.color,
        line_width=payload.line_width,
        line_style=payload.line_style,
        separate_pane=payload.separate_pane,
        pane_height=payload.pane_height,
        is_overlay=payload.is_overlay,
        z_index=payload.z_index,
        watchlist_id=payload.watchlist_id,
    )
    db.add(indicator)
    db.commit()
    db.refresh(indicator)
    return _to_response(indicator)


@router.get("/{indicator_id}", response_model=CustomIndicatorResponse)
def get_indicator(indicator_id: int, db: Session = Depends(get_db)):
    """Get a single indicator by ID."""
    indicator = db.query(CustomIndicator).filter(CustomIndicator.id == indicator_id).first()
    if not indicator:
        raise HTTPException(status_code=404, detail="Indicator not found")
    return _to_response(indicator)


@router.get("/by-slug/{slug}", response_model=CustomIndicatorResponse)
def get_indicator_by_slug(slug: str, db: Session = Depends(get_db)):
    """Get a single indicator by slug."""
    indicator = db.query(CustomIndicator).filter(CustomIndicator.slug == slug).first()
    if not indicator:
        raise HTTPException(status_code=404, detail=f"Indicator with slug '{slug}' not found")
    return _to_response(indicator)


@router.patch("/{indicator_id}", response_model=CustomIndicatorResponse)
def update_indicator(
    indicator_id: int,
    payload: CustomIndicatorUpdate,
    db: Session = Depends(get_db),
):
    """Update an existing indicator. Only provided fields are changed."""
    indicator = db.query(CustomIndicator).filter(CustomIndicator.id == indicator_id).first()
    if not indicator:
        raise HTTPException(status_code=404, detail="Indicator not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "formula_type" in update_data:
        _validate_formula_type(update_data["formula_type"])
    if "parameters" in update_data and update_data["parameters"] is not None:
        update_data["parameters"] = json.dumps(update_data["parameters"])

    for field, value in update_data.items():
        setattr(indicator, field, value)
    db.commit()
    db.refresh(indicator)
    return _to_response(indicator)


@router.delete("/{indicator_id}", status_code=204)
def delete_indicator(indicator_id: int, db: Session = Depends(get_db)):
    """Permanently delete an indicator."""
    indicator = db.query(CustomIndicator).filter(CustomIndicator.id == indicator_id).first()
    if not indicator:
        raise HTTPException(status_code=404, detail="Indicator not found")
    db.delete(indicator)
    db.commit()
    return None


@router.post("/compute/{indicator_id}")
def compute_indicator(
    indicator_id: int,
    symbol: str = Query(..., min_length=1, max_length=10),
    timeframe: str = Query(..., min_length=1, max_length=10),
    limit: int = Query(default=200, ge=10, le=2000),
    db: Session = Depends(get_db),
):
    """Compute indicator values for a given symbol+timeframe.

    Returns the indicator's values for the most recent ``limit`` bars.
    """
    indicator = db.query(CustomIndicator).filter(CustomIndicator.id == indicator_id).first()
    if not indicator:
        raise HTTPException(status_code=404, detail="Indicator not found")

    # Lazy import to keep the router module free of side-effects.
    from backend.indicators.custom_engine import CustomIndicatorEngine

    params = {}
    if indicator.parameters:
        try:
            params = json.loads(indicator.parameters)
        except (json.JSONDecodeError, TypeError):
            params = {}

    engine = CustomIndicatorEngine()
    series = engine.compute(
        formula_type=indicator.formula_type,
        parameters=params,
        symbol=symbol.upper(),
        timeframe=timeframe,
        limit=limit,
    )
    return {
        "indicator_id": indicator_id,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "count": len(series),
        "values": series,
    }
