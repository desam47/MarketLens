"""
API endpoint for draft trade plans.

Routes:
  GET /api/trade-plan/{symbol}/draft  - candidate stops/targets from every
                                        source, plus one assembled plan

Read-only. The response is a proposal for the trader to review: nothing is
saved, no order is placed. Assembly lives in
``backend.services.trade_plan_builder``; the arithmetic lives in
``backend.ai.market_tools.build_trade_plan_tool``.
"""

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.services.excursion_stats import DEFAULT_MIN_SAMPLE
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
