"""
AI trade-plan outcome tracking (2026-09-11).

One row per actionable (buy/sell) ``TradePlan`` explicitly confirmed by the
trader after fresh server-side validation (see
``backend.ai.trade_plan_tracker.record_confirmed_trade_plan``). A background
grading pass (``backend.ai.trade_plan_tracker._grade_once``) walks
daily bars since ``created_at`` and resolves each open row to
``win`` / ``loss`` / ``expired`` once the entry/stop/targets or the
time_horizon's holding window says so.

``hold``/``avoid`` recommendations are never captured — they carry no
actionable entry/stop/targets (``TradePlan._check_consistency`` clears
those fields for a non-entry call), so there's nothing to grade.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.timezone import now_ny


class AITradePlanOutcome(Base):
    """A single explicitly confirmed AI buy/sell setup, graded later
    against what actually happened.

    Attributes
    ----------
    status : str
        "open" (not yet resolved) | "win" (a target was hit before the
        stop) | "loss" (the stop was hit first) | "expired" (neither
        happened within the time_horizon's holding window).
    targets_json : str
        JSON-encoded ``list[float]`` — TradePlan.targets, already
        sorted in the trade's favorable direction by TradePlan's own
        validator.
    hit_target_index : int | None
        Which target (0-based, into targets_json) was hit, for a win.
    return_pct : float | None
        For a win/loss: the realized return from entry_mid to
        resolved_price. For an expired plan: a mark-to-expiry return
        against the last available close — informational only, not
        counted in a win-rate calculation.
    """

    __tablename__ = "ai_trade_plan_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    recommendation: Mapped[str] = mapped_column(String(10), nullable=False)
    conviction: Mapped[str] = mapped_column(String(10), nullable=False)
    time_horizon: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_zone_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_zone_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    targets_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    risk_reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    # Provenance for the exact server-verified analysis the trader chose to
    # track. ``analysis_id`` is an opaque short-lived handle, not the plan
    # body itself; legacy rows retain the empty default.
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="1d")
    analysis_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_ny, index=True
    )

    status: Mapped[str] = mapped_column(String(12), nullable=False, default="open", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    hit_target_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("ix_ai_trade_plan_outcomes_symbol_status", "symbol", "status"),)

    def __repr__(self):
        return (
            f"<AITradePlanOutcome(id={self.id}, symbol={self.symbol}, "
            f"{self.recommendation}, status={self.status})>"
        )
