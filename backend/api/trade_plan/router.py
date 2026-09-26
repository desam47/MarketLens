"""
API endpoints for trade plans.

Routes:
  GET    /api/trade-plan/{symbol}/draft  - draft plan candidates (read-only)
  GET    /api/trade-plan/saved           - list all saved plans
  POST   /api/trade-plan/saved           - upsert a saved plan by client_id
  DELETE /api/trade-plan/saved/{id}      - remove a saved plan by client_id
"""

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.services.excursion_stats import DEFAULT_MIN_SAMPLE
from backend.repositories.saved_trade_plan_repository import SavedTradePlanRepository
from backend.services.trade_plan_builder import build_draft_plan

from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trade-plan", tags=["trade-plan"])


class PriceCandidate(BaseModel):
    source: str
    price: float
    distance_pct: float
    rationale: str


class SelectedLevels(BaseModel):
    entry_zone_low: float
    entry_zone_high: float
    stop_price: float
    stop_source: str
    targets: list[float]
    target_sources: list[str]


class TradePlanDraft(BaseModel):
    """Every candidate, plus the one draft the UI opens with.

    ``sources`` carries a per-source ``{available, error}`` envelope so a
    failing provider is visible rather than silently missing. ``selected`` and
    ``plan`` are null when no stop or target lands on the tradeable side of
    entry -- ``warnings`` says why.
    """

    symbol: str
    timeframe: str
    direction: str
    current_price: float | None
    latest_bar_timestamp: datetime | None = None
    latest_bar_data_status: str | None = None
    latest_bar_source: str | None = None
    bars_used: int | None = None
    sources: dict[str, Any]
    candidate_stops: list[PriceCandidate]
    candidate_targets: list[PriceCandidate]
    # Best reward:risk across every stop/target pairing, reported even when
    # nothing clears min_reward_risk so the UI can show how far off the setup is.
    best_reward_risk: float | None = None
    min_reward_risk: float | None = None
    selected: SelectedLevels | None
    plan: dict[str, Any] | None
    warnings: list[str]


@router.get("/{symbol}/draft", response_model=TradePlanDraft)
async def get_trade_plan_draft(
    symbol: str,
    timeframe: str = Query(..., description="Bar timeframe the plan is built on"),
    direction: Literal["long", "short"] = Query(...),
    account_value: float | None = Query(None, gt=0, description="Required to size the position"),
    risk_percent: float | None = Query(None, gt=0, le=100, description="Percent of account at risk"),
    include_options: bool = Query(True, description="Set false to skip the options provider"),
    min_sample: int = Query(
        DEFAULT_MIN_SAMPLE, ge=1, description="Comparable signals needed before an empirical stop is offered"
    ),
    db: Session = Depends(get_db),
):
    """Build a draft trade plan for one symbol, timeframe and direction.

    Stop and target candidates come from four independent angles -- structural
    (support/resistance), volatility (ATR and the supertrend flip), empirical
    (the adverse-excursion distribution of comparable past signals), and the
    options-implied expected move -- and all of them are returned so the trader
    compares rather than trusting one. The pre-selected draft takes the
    **widest** stop, since a stop inside another source's estimate of normal
    noise gets taken out by a trade that would otherwise have worked.

    Position size appears only when both ``account_value`` and ``risk_percent``
    are supplied.
    """
    return await build_draft_plan(
        db,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        account_value=account_value,
        risk_percent=risk_percent,
        include_options=include_options,
        min_sample=min_sample,
    )


# ------------------------------------------------------------------ #
# Saved plans persistence                                             #
# ------------------------------------------------------------------ #

class SavedPlanPayload(BaseModel):
    client_id: str
    symbol: str
    side: str
    timeframe: str
    entry_price: float
    stop_price: float
    target_price: float
    quantity: float
    stop_source: str = ""
    reward_risk: float | None = None
    thesis: str = ""
    created_at: str | None = None


def _serialize_plan(plan) -> dict:
    return {
        "id": plan.client_id,
        "symbol": plan.symbol,
        "side": plan.side,
        "timeframe": plan.timeframe,
        "entryPrice": plan.entry_price,
        "stopPrice": plan.stop_price,
        "targetPrice": plan.target_price,
        "quantity": plan.quantity,
        "stopSource": plan.stop_source,
        "rewardRisk": plan.reward_risk,
        "thesis": plan.thesis,
        "createdAt": plan.created_at.isoformat() if plan.created_at else None,
    }


@router.get("/saved")
def list_saved_plans(db: Session = Depends(get_db)):
    repo = SavedTradePlanRepository(db)
    return [_serialize_plan(p) for p in repo.list_all()]


@router.post("/saved")
def upsert_saved_plan(payload: SavedPlanPayload, db: Session = Depends(get_db)):
    from backend.utils.timezone import now_ny
    repo = SavedTradePlanRepository(db)
    data = {
        "client_id": payload.client_id,
        "symbol": payload.symbol.upper(),
        "side": payload.side,
        "timeframe": payload.timeframe,
        "entry_price": payload.entry_price,
        "stop_price": payload.stop_price,
        "target_price": payload.target_price,
        "quantity": payload.quantity,
        "stop_source": payload.stop_source,
        "reward_risk": payload.reward_risk,
        "thesis": payload.thesis,
        "created_at": (
            datetime.fromisoformat(payload.created_at) if payload.created_at else now_ny()
        ),
    }
    plan = repo.upsert(data)
    return _serialize_plan(plan)


@router.delete("/saved/{client_id}")
def delete_saved_plan(client_id: str, db: Session = Depends(get_db)):
    repo = SavedTradePlanRepository(db)
    if not repo.delete_by_client_id(client_id):
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"deleted": True}
