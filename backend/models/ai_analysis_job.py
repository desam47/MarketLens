"""
AI Analysis Job model (Phase 2.5: Background AI processing with RQ).

Stores an enqueued AI analysis job so the frontend can poll for results.
Status transitions: queued → started → finished | failed
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

_JOB_STATUS_ENUM = ["queued", "started", "finished", "failed"]


class AIAnalysisJob(Base):
    """Tracks a background AI analysis job.

    Attributes
    ----------
    id : int
        Auto-incrementing primary key.
    job_id : str
        RQ job ID — a UUID string assigned by RQ when the job is enqueued.
        Stored here so the API can look up the job by our own ``id`` and still
        communicate the RQ ``job_id`` to callers.
    symbol : str
        Ticker symbol (e.g. "AAPL").
    timeframe : str
        Analysis timeframe (e.g. "1d", "4h").
    template_id : int | None
        ID of the AI template used, if any.
    template_name : str | None
        Human-readable template name captured at enqueue time.
    status : str
        One of "queued", "started", "finished", "failed".
    result : str | None
        JSON-serialized ``AnalysisResponse`` when status is "finished".
    error : str | None
        Error message string when status is "failed".
    created_at : datetime
        When the job was enqueued.
    started_at : datetime | None
        When the worker picked up the job.
    completed_at : datetime | None
        When the job finished (success or failure).
    """

    __tablename__ = "ai_analysis_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="1d")
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", index=True,
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_ai_analysis_jobs_symbol_created", "symbol", "created_at"),
    )
