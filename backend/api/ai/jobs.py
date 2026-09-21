"""
Background AI analysis job API (Phase 2.5).

POST /api/ai/jobs       — enqueue a new analysis job
GET  /api/ai/jobs/{id}  — poll job status / fetch result

The ``id`` here is the RQ job ID (UUID string), not the integer
``AIAnalysisJob.id``. We use the RQ ID for lookup so callers see the
same identifier end-to-end (the RQ job ID is what RQ CLI tools show
in ``rq info`` output too).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...ai.background import enqueue_analyze_job, get_job_status
from ...ai.tasks import analyze_symbol_task

router = APIRouter(prefix="/api/ai/jobs", tags=["ai-jobs"])


# ── Schemas ────────────────────────────────────────────────────────────────


class JobEnqueueRequest(BaseModel):
    """Body for ``POST /api/ai/jobs``."""
    symbol: str = Field(..., min_length=1, max_length=10)
    timeframe: str = Field(default="1d", pattern=r"^(1d|1h|4h|15m|5m|1m)$")
    template_id: int | None = Field(default=None, ge=1)


class JobEnqueueResponse(BaseModel):
    """Response when a job is enqueued (or when enqueue fails)."""
    job_id: str
    status: str
    symbol: str
    timeframe: str
    template_id: int | None = None
    template_name: str | None = None


class JobStatusResponse(BaseModel):
    """Response for ``GET /api/ai/jobs/{job_id}``."""
    job_id: str
    status: str
    symbol: str
    timeframe: str
    template_id: int | None = None
    template_name: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=JobEnqueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_job(req: JobEnqueueRequest) -> JobEnqueueResponse:
    """Enqueue a new AI analysis job.

    The job runs in a background RQ worker. The response returns a
    ``job_id`` immediately — poll it via ``GET /api/ai/jobs/{job_id}``
    until ``status`` is ``finished`` or ``failed``.

    If Redis is disabled or unreachable, the endpoint returns 503 with
    a clear message so the client can fall back to the synchronous
    ``POST /api/ai/analyze`` endpoint.
    """
    template_name: str | None = None
    if req.template_id is not None:
        # Capture the template name up-front so workers don't need a DB read.
        from ...database import SessionLocal
        from ...models import AITemplate
        db = SessionLocal()
        try:
            tmpl = (
                db.query(AITemplate).filter(AITemplate.id == req.template_id).first()
            )
            if tmpl is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"AI template {req.template_id} not found",
                )
            if not tmpl.is_active:
                raise HTTPException(
                    status_code=400,
                    detail=f"AI template {req.template_id} is inactive",
                )
            template_name = tmpl.name
        finally:
            db.close()

    rq_job_id = enqueue_analyze_job(
        symbol=req.symbol,
        timeframe=req.timeframe,
        template_id=req.template_id,
        template_name=template_name,
    )
    if rq_job_id is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Background job queue is unavailable. Redis may be disabled. "
                "Use POST /api/ai/analyze for synchronous analysis."
            ),
        )

    return JobEnqueueResponse(
        job_id=rq_job_id,
        status="queued",
        symbol=req.symbol.upper(),
        timeframe=req.timeframe,
        template_id=req.template_id,
        template_name=template_name,
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
)
def get_job(job_id: str) -> JobStatusResponse:
    """Look up a job by its RQ job ID.

    Returns the current status. The ``result`` field is populated only
    when ``status == "finished"``. The ``error`` field is populated only
    when ``status == "failed"``.

    Poll this endpoint every 1–2 seconds. Long-running analyses can take
    up to ``BACKGROUND_JOB_TIMEOUT`` seconds (default 600) before timing out.
    """
    data = get_job_status(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobStatusResponse(**{k: v for k, v in data.items() if k != "id"})


# Make analyze_symbol_task importable by the worker process.
# (The worker imports ``backend.ai.tasks`` directly; this is a no-op
# reference to avoid ``imported but unused`` linting complaints.)
_ = analyze_symbol_task
