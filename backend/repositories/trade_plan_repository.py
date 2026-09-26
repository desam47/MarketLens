"""
Repository for AITradePlanOutcome — injectable session, no raw SessionLocal calls.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.ai_trade_plan_outcome import AITradePlanOutcome


class TradePlanRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def find_open_duplicates(
        self,
        symbol: str,
        recommendation: str,
        conviction: str,
        time_horizon: str,
        timeframe: str,
    ) -> list[AITradePlanOutcome]:
        return (
            self.db.query(AITradePlanOutcome)
            .filter(
                AITradePlanOutcome.symbol == symbol.upper(),
                AITradePlanOutcome.status == "open",
                AITradePlanOutcome.recommendation == recommendation,
                AITradePlanOutcome.conviction == conviction,
                AITradePlanOutcome.time_horizon == time_horizon,
                AITradePlanOutcome.timeframe == timeframe,
            )
            .all()
        )

    def create(
        self,
        symbol: str,
        recommendation: str,
        conviction: str,
        time_horizon: str,
        entry_zone_low: float | None,
        entry_zone_high: float | None,
        stop_loss: float | None,
        targets_json: str,
        risk_reward: float | None,
        provider: str,
        model: str,
        timeframe: str,
        analysis_id: str,
    ) -> AITradePlanOutcome:
        row = AITradePlanOutcome(
            symbol=symbol.upper(),
            recommendation=recommendation,
            conviction=conviction,
            time_horizon=time_horizon,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            stop_loss=stop_loss,
            targets_json=targets_json,
            risk_reward=risk_reward,
            provider=provider[:50],
            model=model[:100],
            timeframe=timeframe[:8],
            analysis_id=analysis_id[:64],
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_open_rows(self) -> list[AITradePlanOutcome]:
        return (
            self.db.query(AITradePlanOutcome)
            .filter(AITradePlanOutcome.status == "open")
            .all()
        )

    def get_resolved_rows(self, symbol: str, limit: int = 20) -> list[AITradePlanOutcome]:
        return (
            self.db.query(AITradePlanOutcome)
            .filter(
                AITradePlanOutcome.symbol == symbol.upper(),
                AITradePlanOutcome.status.in_(("win", "loss")),
            )
            .order_by(AITradePlanOutcome.resolved_at.desc())
            .limit(limit)
            .all()
        )

    def get_status_counts(self, symbol: str) -> dict[str, int]:
        rows = (
            self.db.query(AITradePlanOutcome.status, func.count())
            .filter(AITradePlanOutcome.symbol == symbol.upper())
            .group_by(AITradePlanOutcome.status)
            .all()
        )
        return dict(rows)
