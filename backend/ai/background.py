"""
Background processing helpers (Phase 2.5: RQ-based AI job queue).

Wraps the redis client and RQ queue so the API layer doesn't have to know
about RQ's specific API. The same Redis instance is shared with the cache
layer (see ``backend.market_data.services.manager.RedisCache``); RQ just
uses a different logical database index by default (``REDIS_URL`` path).

Usage
-----
- API enqueues a job via :func:`enqueue_analyze_job`.
- A worker process picks it up: ``rq worker --url redis://localhost:6379/0 marketlens-workers``.
- The job body is :func:`analyze_symbol_task` in ``backend.ai.tasks``.

If Redis is disabled in settings (``REDIS_ENABLED=false``) or the
connection fails at startup, enqueue is a no-op that returns ``None``
so callers can degrade gracefully. The same function is always available —
callers don't have to special-case.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.config.settings import settings

# rq and redis are optional; imported lazily inside functions that need them.
# This lets the API server start even when rq is not installed.
logger = logging.getLogger(__name__)

_QUEUE: Any = None
_REDIS: Any = None


# ── Connection helpers ─────────────────────────────────────────────────────


def get_redis() -> Any:
    """Return the shared Redis client, or None if Redis is disabled/unavailable.

    The client is created lazily on first use so test suites that don't
    have Redis don't pay the connection cost. If the first connection
    attempt fails, we cache ``None`` to avoid retrying on every request.
    """
    global _REDIS
    if not settings.redis.enabled:
        return None
    if _REDIS is not None:
        return _REDIS
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(
            settings.redis.url,
            password=settings.redis.password or None,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        client.ping()
        _REDIS = client
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis connection failed; background processing disabled: %s", exc)
        _REDIS = None
        return None


def get_queue() -> Any:
    """Return the RQ queue, or None if Redis isn't available.

    The queue is created on first use and cached. RQ's ``Queue`` is a
    thin wrapper over Redis, so it's safe to keep in module state.
    """
    global _QUEUE
    if not settings.background.enabled:
        return None
    if _QUEUE is not None:
        return _QUEUE
    client = get_redis()
    if client is None:
        return None
    try:
        from rq import Queue

        _QUEUE = Queue(
            settings.background.queue_name,
            connection=client,
            default_timeout=settings.background.job_timeout,
        )
        return _QUEUE
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to create RQ queue: %s", exc)
        return None


def reset_for_tests() -> None:
    """Clear cached clients — used by tests that re-initialize Redis."""
    global _REDIS, _QUEUE
    _REDIS = None
    _QUEUE = None


# ── Enqueue helpers ───────────────────────────────────────────────────────


def enqueue_analyze_job(
    symbol: str,
    timeframe: str = "1d",
    template_id: int | None = None,
    template_name: str | None = None,
    portfolio_symbols: list[str] | None = None,
) -> str | None:
    """Enqueue an ``analyze_symbol_task`` and return the RQ job ID.

    The RQ job ID is a UUID string. We also create a row in the
    ``ai_analysis_jobs`` table synchronously so the API can return a
    caller-known primary key (and poll status without going through RQ).

    Returns the RQ ``job_id`` (a string), or None if enqueue failed.
    """
    from backend.ai.tasks import analyze_symbol_task
    from backend.database import SessionLocal

    queue = get_queue()
    if queue is None:
        return None

    job_id = uuid4().hex
    rq_job = queue.enqueue(
        analyze_symbol_task,
        kwargs={
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "template_id": template_id,
            "template_name": template_name,
            "job_id": job_id,
            "portfolio_symbols": portfolio_symbols,
        },
        job_id=job_id,
        result_ttl=settings.background.result_ttl,
    )

    from backend.repositories.ai_analysis_job_repository import AIAnalysisJobRepository

    db = SessionLocal()
    try:
        repo = AIAnalysisJobRepository(db)
        repo.create(job_id, symbol.upper(), timeframe, template_id, template_name)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("Failed to persist AIAnalysisJob for rq_id=%s: %s", rq_job.id, exc)
        # Don't fail the enqueue — the RQ job is in the queue, and the
        # task itself can recreate the row if needed.
    finally:
        db.close()

    return job_id


def enqueue_alert_commentary_job(trigger_id: int) -> str | None:
    """Enqueue a ``generate_alert_commentary_task`` and return the RQ job ID.

    Version 4, AI feature 3. Unlike :func:`enqueue_analyze_job`, no new
    DB row is created here — the ``AlertTrigger`` row already exists
    (inserted synchronously by ``AlertsEngine._persist_trigger()``
    just before this is called); the worker task updates that same
    row's ``ai_commentary`` column in place once it has an answer.

    Reuses the existing ``marketlens-workers`` queue (the same one
    :func:`enqueue_analyze_job` uses) rather than a dedicated queue —
    alert-commentary jobs are short (one context build + one small
    completion, the same cost class as an AI Analysis job) and
    low-volume (gated by the alerts engine's own 1-hour per-alert
    dedup window), so they don't have the starvation profile that
    justified giving ticker backfill its own separate queue.

    Same no-op-if-Redis-down contract as ``enqueue_analyze_job``:
    returns ``None`` if the queue is unavailable, and the caller
    (``_persist_trigger()``) treats that as "no commentary this time",
    never as a reason the trigger itself failed to record.
    """
    from backend.ai.tasks import generate_alert_commentary_task

    queue = get_queue()
    if queue is None:
        return None

    rq_job = queue.enqueue(
        generate_alert_commentary_task,
        kwargs={"trigger_id": trigger_id},
        result_ttl=settings.background.result_ttl,
    )
    return rq_job.id


# ── Status helpers ────────────────────────────────────────────────────────


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Look up a job's status and return a JSON-friendly dict.

    Returns None if the job isn't found. The dict has the same shape
    the API returns to the frontend:

    - ``status`` (str): "queued" | "started" | "finished" | "failed" | "cancelled"
    - ``result`` (dict | None): AnalysisResponse-like dict when finished
    - ``error`` (str | None): error message when failed
    - ``symbol`` / ``timeframe`` / ``template_id`` / ``template_name``
    - ``created_at`` / ``started_at`` / ``completed_at`` (ISO strings)
    """
    from backend.database import SessionLocal
    from backend.repositories.ai_analysis_job_repository import AIAnalysisJobRepository

    db = SessionLocal()
    try:
        record = AIAnalysisJobRepository(db).find_by_job_id(job_id)
        if record is None:
            return None

        # Refresh status from RQ if we still think it's queued/started
        # and there's a Redis connection — the RQ Job knows more
        # than our DB row in the in-between states.
        if record.status in ("queued", "started"):
            rq_status = _safe_rq_status(job_id)
            if rq_status and rq_status != record.status:
                record.status = rq_status
                if rq_status == "started" and record.started_at is None:
                    record.started_at = datetime.now(UTC)
                if rq_status in ("finished", "failed", "cancelled") and record.completed_at is None:
                    record.completed_at = datetime.now(UTC)
                db.commit()

        result_data: Any = None
        if record.result:
            try:
                result_data = json.loads(record.result)
            except (ValueError, TypeError):
                result_data = record.result

        return {
            "id": record.id,
            "job_id": record.job_id,
            "status": record.status,
            "result": result_data,
            "error": record.error,
            "symbol": record.symbol,
            "timeframe": record.timeframe,
            "template_id": record.template_id,
            "template_name": record.template_name,
            "created_at": _iso(record.created_at),
            "started_at": _iso(record.started_at),
            "completed_at": _iso(record.completed_at),
        }
    finally:
        db.close()


def cancel_job(job_id: str) -> dict[str, Any] | None:
    """Cancel a queued analysis job and return its current status.

    RQ cannot safely interrupt a worker that has already started running an
    analysis. In that case ``cancelled`` is false and the caller can stop
    waiting without claiming that server-side work stopped. Queued jobs are
    cancelled in RQ and marked terminal in our database.
    """
    from backend.database import SessionLocal
    from backend.repositories.ai_analysis_job_repository import AIAnalysisJobRepository

    db = SessionLocal()
    try:
        record = AIAnalysisJobRepository(db).find_by_job_id(job_id)
        if record is None:
            return None
        if record.status in ("finished", "failed", "cancelled"):
            current = get_job_status(job_id)
            return {**current, "cancelled": record.status == "cancelled"} if current else None

        client = get_redis()
        if client is None:
            current = get_job_status(job_id)
            return {**current, "cancelled": False} if current else None

        try:
            from rq.job import Job

            rq_job = Job.fetch(job_id, connection=client)
            rq_status = rq_job.get_status()
            if rq_status in ("queued", "deferred", "scheduled"):
                rq_job.cancel()
                record.status = "cancelled"
                record.error = "Cancelled by user."
                record.completed_at = datetime.now(UTC)
                db.commit()
                current = get_job_status(job_id)
                return {**current, "cancelled": True} if current else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to cancel AI job %s: %s", job_id, exc)

        current = get_job_status(job_id)
        return {**current, "cancelled": False} if current else None
    finally:
        db.close()


def _safe_rq_status(job_id: str) -> str | None:
    """Map an RQ job state to one of our four statuses, or None on error."""
    client = get_redis()
    if client is None:
        return None
    try:
        from rq.job import Job

        rq_job = Job.fetch(job_id, connection=client)
        status = rq_job.get_status()
        if status in ("queued", "started", "finished", "failed"):
            return status
        if status in ("canceled", "cancelled"):
            return "cancelled"
        if status == "deferred":
            return "queued"
        if status in ("scheduled",):
            return "queued"
        return status
    except Exception:  # noqa: BLE001
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None
