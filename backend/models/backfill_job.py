"""
Backfill Job model.

Tracks a background per-symbol history backfill (RQ job) — tier-by-tier
write counts and the gap-check-and-fill outcome. Introduced when the
ticker-add pipeline was rebuilt around a single owned RQ job instead of two
racing in-process triggers coordinated by a per-event-loop asyncio lock (see
``backend/market_data/services/backfill_queue.py`` for the queue/status
plumbing and ``backend/market_data/services/backfill_service.py`` for the
pipeline itself). Mirrors ``AIAnalysisJob``'s shape — same house pattern for
"a queued background job with a pollable DB row".

Status transitions: queued -> started -> completed | partial | failed
  - "completed": all three tiers fetched, no gaps remained after the
    gap-fill pass.
  - "partial": tiers fetched but some expected bars could not be sourced
    from any configured provider (see ``result`` for per-timeframe detail)
    — not necessarily a bug, sometimes just data no provider has (e.g. a
    thinly-traded symbol with genuinely no prints in some minute).
  - "failed": an exception aborted the pipeline before it finished.
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

_JOB_STATUS_ENUM = ["queued", "started", "completed", "partial", "failed"]


class BackfillJob(Base):
    """Tracks a background symbol-history backfill job.

    Attributes
    ----------
    id : int
        Auto-incrementing primary key.
    job_id : str
        RQ job ID (also used as the RQ-side idempotency key — see
        ``enqueue_backfill``'s single-flight check).
    symbol : str
        Ticker symbol (e.g. "AAPL"). Not unique — each add/re-add creates a
        new row, so history accumulates the same way ``AIAnalysisJob`` does.
    status : str
        One of "queued", "started", "completed", "partial", "failed".
    tier1_written / tier2_written / tier3_written : int
        Bars written for 1m / 1h / 1d respectively.
    gaps_found / gaps_filled : int
        Aggregate across all three tiers' gap-check-and-fill passes.
    result : str | None
        JSON-serialized per-timeframe detail (tier counts + gap detail).
    error : str | None
        Error message when status is "failed".
    created_at : datetime
        When the job was enqueued.
    started_at : datetime | None
        When the worker picked up the job.
    completed_at : datetime | None
        When the job finished (success, partial, or failure).
    """

    __tablename__ = "backfill_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", index=True,
    )
    tier1_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tier2_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tier3_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gaps_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gaps_filled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_backfill_jobs_symbol_created", "symbol", "created_at"),
    )
