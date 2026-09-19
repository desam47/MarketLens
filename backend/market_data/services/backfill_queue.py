"""
Backfill job queue — RQ-based, mirrors ``backend/ai/background.py``.

The ticker-add pipeline used to have two independent, uncoordinated
triggers for the same symbol: the watchlist router's own synchronous
in-process trigger (blocking the HTTP request thread up to 10 minutes) and
``ingestion_service``'s bootstrap trigger (reachable only via a second,
client-owned HTTP call after add). They were coordinated after the fact by
an asyncio lock/semaphore that had to be re-keyed per event loop to avoid
crashing outright (see the removed ``_get_lock``/``_get_backfill_semaphore``
in ``backfill_service.py``).

This module replaces both with a single owned path: the watchlist router
enqueues one RQ job (``backfill_symbol_task`` in ``backfill_service.py``)
and returns immediately — no provider I/O on the request thread, no second
client-owned call required for correctness. Single-flight is now a
Redis-backed, cross-process fact (checked against the ``backfill_jobs``
table + RQ's own job status) instead of an in-process primitive that has
to be re-derived per event loop.

Usage
-----
- API enqueues via :func:`enqueue_backfill`.
- A worker process picks it up:
  ``rq worker --url redis://localhost:6379/0 marketlens-backfill``
  (run two instances to reproduce the old semaphore's concurrency cap of 2).
- The job body is :func:`backfill_symbol_task` in
  ``backend.market_data.services.backfill_service``.

If Redis is disabled (``REDIS_ENABLED=false``) or unavailable, every
function here degrades to a no-op / ``None`` return so callers don't have
to special-case it — same contract as ``backend/ai/background.py``.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from backend.config.settings import settings

logger = logging.getLogger(__name__)

_QUEUE: Any = None

_ACTIVE_RQ_STATUSES = ("queued", "started", "deferred", "scheduled")


def get_backfill_queue() -> Any:
    """Return the RQ backfill queue, or None if Redis isn't available.

    Separate queue instance from ``backend.ai.background.get_queue()`` —
    same Redis connection (reused via ``get_redis()``), different queue
    name, so a slow backfill can't block behind/starve AI analysis jobs.
    """
    global _QUEUE
    if not settings.background.enabled:
        return None
    if _QUEUE is not None:
        return _QUEUE
    from backend.ai.background import get_redis
    client = get_redis()
    if client is None:
        return None
    try:
        from rq import Queue
        _QUEUE = Queue(
            settings.background.backfill_queue_name,
            connection=client,
            default_timeout=settings.background.job_timeout,
        )
        return _QUEUE
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to create RQ backfill queue: %s", exc)
        return None


def reset_for_tests() -> None:
    """Clear the cached queue — used by tests that re-initialize Redis."""
    global _QUEUE
    _QUEUE = None


def _in_flight_job_id(symbol: str) -> str | None:
    """Return the job_id of a still-active backfill for ``symbol``, if any.

    Checked against the DB row's status AND confirmed against RQ itself —
    a worker crash mid-job never gets to write "failed" to the DB, so
    trusting the DB row alone would wedge a symbol into "always in
    flight" forever after any worker crash.
    """
    from backend.database import SessionLocal
    from backend.models import BackfillJob

    db = SessionLocal()
    try:
        latest = (
            db.query(BackfillJob)
            .filter(BackfillJob.symbol == symbol)
            .order_by(BackfillJob.created_at.desc())
            .first()
        )
        if latest is None or latest.status not in ("queued", "started"):
            return None
        from backend.ai.background import get_redis
        client = get_redis()
        if client is None:
            # Can't confirm with RQ — trust the DB row rather than risk a
            # duplicate concurrent backfill.
            return latest.job_id
        try:
            from rq.job import Job
            rq_job = Job.fetch(latest.job_id, connection=client)
            if rq_job.get_status() in _ACTIVE_RQ_STATUSES:
                return latest.job_id
        except Exception:
            # RQ has no record of it (expired, never enqueued, etc.) —
            # the DB row is stale, not in flight.
            return None
        return None
    finally:
        db.close()


def enqueue_backfill(symbol: str) -> str | None:
    """Enqueue a ``backfill_symbol_task`` and return the RQ job ID.

    Single-flight: if ``symbol`` already has a confirmed in-flight job,
    this is a no-op that returns None (mirrors the old lock's
    ``if lock.locked(): skip`` behavior, now enforced across processes
    instead of per-event-loop).

    The check-then-act sequence (check ``_in_flight_job_id``, then enqueue
    + insert the ``BackfillJob`` row) is wrapped in a short-lived Redis
    lock (``SET NX EX``) so two near-simultaneous callers for the SAME
    symbol — e.g. two API workers handling a double-submitted add, or
    ``ingestion_service._seed_check`` racing an explicit add moments
    apart — can't both pass the in-flight check before either has written
    its row, which would otherwise still enqueue two jobs (found via a
    2026-09-08 post-redesign completeness audit: without this lock, the
    check and the write aren't atomic, so this specific race survived the
    redesign even though the class of bug it belongs to — the 2026-09-09
    SOFI incident — was the whole reason for the redesign). The lock
    auto-expires so a crash between acquire and release can't wedge the
    symbol forever; if Redis is briefly unreachable for the lock itself,
    this degrades to the unlocked check (same behavior as before this
    fix) rather than blocking the caller.

    Also creates the ``BackfillJob`` row synchronously so the caller (and
    ``get_backfill_job_status``) has a pollable record immediately.
    Returns None if Redis/RQ is unavailable, a job is already in flight,
    or the enqueue fails — callers degrade gracefully in every case (the
    symbol still gets live data via ``ingestion_service.register_symbol``;
    it just won't have historical bars until a worker is available).
    """
    symbol = symbol.upper()
    queue = get_backfill_queue()
    if queue is None:
        return None

    from backend.ai.background import get_redis
    client = get_redis()
    lock_key = f"backfill:enqueue-lock:{symbol}"
    lock_acquired = False
    if client is not None:
        try:
            lock_acquired = bool(client.set(lock_key, "1", nx=True, ex=10))
            if not lock_acquired:
                logger.debug(f"enqueue_backfill: {symbol} enqueue lock held by another caller — skipping")
                return None
        except Exception as e:
            logger.debug(f"enqueue_backfill: lock attempt for {symbol} failed, proceeding without it: {e}")

    try:
        if _in_flight_job_id(symbol) is not None:
            logger.debug(f"enqueue_backfill: {symbol} already has an in-flight job — skipping")
            return None

        from backend.database import SessionLocal
        from backend.models import BackfillJob
        from backend.market_data.services.backfill_service import backfill_symbol_task

        # RQ's Job ID validator only allows letters, numbers, underscores
        # and dashes — a colon-separated id embedding the raw symbol (this
        # function's original format) raises ValueError("Job ID must only
        # contain letters, numbers, underscores and dashes") from every
        # REAL queue.enqueue() call, meaning every real backfill enqueue
        # silently failed and fell into the except-and-return-None branch
        # below. Every test up to this point had mocked queue.enqueue()
        # (never exercising RQ's actual id validation), so this went
        # undetected until a 2026-09-08 post-redesign completeness audit's
        # concurrency test was switched from a mocked queue to a real one
        # specifically to test single-flight — and hit this instead.
        #
        # Fix: don't embed the symbol in the job_id at all, rather than
        # just swapping the separator — some real tickers legitimately
        # contain characters RQ also rejects (e.g. "BRK.B"), and
        # validate_symbol() imposes no character-set restriction of its
        # own, so a dash-only format would just move the same bug to a
        # smaller set of real symbols instead of eliminating it. Nothing
        # needs to parse the symbol back out of job_id — every lookup
        # (_in_flight_job_id, cancel_backfill, get_backfill_job_status)
        # already goes through BackfillJob.symbol, the real DB column.
        job_id = f"backfill-{uuid.uuid4().hex}"
        db = SessionLocal()
        try:
            queue.enqueue(
                backfill_symbol_task,
                kwargs={"symbol": symbol, "job_id": job_id},
                job_id=job_id,
                result_ttl=settings.background.result_ttl,
            )
            db.add(BackfillJob(job_id=job_id, symbol=symbol, status="queued"))
            db.commit()
            logger.info(f"enqueue_backfill: queued {job_id} for {symbol}")
            return job_id
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.error(f"enqueue_backfill failed for {symbol}: {exc}")
            return None
        finally:
            db.close()
    finally:
        if lock_acquired:
            try:
                client.delete(lock_key)
            except Exception:
                pass


# A job may legitimately sit queued behind a backlog, or run for up to ``background.job_timeout``;
# rows younger than this are never touched.
_ORPHAN_MIN_AGE = timedelta(minutes=30)


def _orphan_reason(client: Any, job_id: str) -> str | None:
    """Why the DB row for ``job_id`` can never finish, or None if it still can (or we can't tell)."""
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    try:
        rq_job = Job.fetch(job_id, connection=client)
    except NoSuchJobError:
        return "backfill job lost from the queue (worker crash or a Redis flush) before it completed"
    except Exception:  # noqa: BLE001
        return None  # Redis unreachable etc.: absence of an answer is not proof the job is gone
    status = rq_job.get_status()
    status = getattr(status, "value", status)       # RQ 2.x returns a JobStatus enum
    if status in _ACTIVE_RQ_STATUSES:
        return None
    detail = ((rq_job.exc_info or "").strip().splitlines() or [""])[-1][:300]
    return f"RQ reports the job as {status} and it never updated its row" + (f": {detail}" if detail else "")


def reap_orphaned_jobs(min_age: timedelta = _ORPHAN_MIN_AGE) -> int:
    """Mark ``queued`` / ``started`` rows whose RQ job is gone or dead as ``failed``.

    A worker killed mid-job (RQ: ``AbandonedJobError``) never reaches the code that writes the
    terminal status, so its row stayed ``started`` forever: 17 such rows had piled up. Only the
    single-flight check and a poll of that exact symbol ever noticed. Returns the number reaped.
    Rows younger than ``min_age`` are left alone, and nothing is reaped when Redis can't answer.
    """
    from backend.ai.background import get_redis
    from backend.database import SessionLocal
    from backend.models import BackfillJob

    client = get_redis()
    if client is None:
        return 0
    cutoff = datetime.utcnow() - min_age
    db = SessionLocal()
    try:
        rows = (
            db.query(BackfillJob)
            .filter(BackfillJob.status.in_(("queued", "started")), BackfillJob.created_at < cutoff)
            .all()
        )
        reaped = 0
        for row in rows:
            reason = _orphan_reason(client, row.job_id)
            if reason is None:
                continue
            row.status = "failed"
            row.error = reason
            row.completed_at = datetime.utcnow()
            reaped += 1
        if reaped:
            db.commit()
        return reaped
    finally:
        db.close()


def cancel_backfill(symbol: str) -> bool:
    """Cancel any queued/started backfill job for ``symbol``.

    Best-effort against RQ itself (a queued job is dequeued cleanly; a
    started job gets a stop signal that its worker may or may not honor
    mid-provider-call — same "best effort, not a guarantee" character the
    old ``asyncio.Task.cancel()`` version had). The DB row is always
    marked failed/cancelled regardless, so status queries are correct
    even in the worst case where the OS-level job runs to completion
    anyway (wasted work, not incorrect state).

    Returns True if an in-flight job was found (and cancellation
    attempted), False if there was nothing to cancel.
    """
    from backend.database import SessionLocal
    from backend.models import BackfillJob

    db = SessionLocal()
    try:
        record = (
            db.query(BackfillJob)
            .filter(BackfillJob.symbol == symbol.upper(), BackfillJob.status.in_(["queued", "started"]))
            .order_by(BackfillJob.created_at.desc())
            .first()
        )
        if record is None:
            return False

        from backend.ai.background import get_redis
        client = get_redis()
        if client is not None:
            try:
                from rq.job import Job
                rq_job = Job.fetch(record.job_id, connection=client)
                rq_status = rq_job.get_status()
                if rq_status == "started":
                    try:
                        from rq.command import send_stop_job_command
                        send_stop_job_command(client, record.job_id)
                    except Exception as e:
                        logger.debug(f"cancel_backfill: stop signal for {record.job_id} failed: {e}")
                elif rq_status in ("queued", "deferred", "scheduled"):
                    rq_job.cancel()
            except Exception as e:
                logger.debug(f"cancel_backfill: RQ lookup for {symbol} failed: {e}")

        record.status = "failed"
        record.error = "cancelled (symbol removed from watchlist)"
        record.completed_at = datetime.utcnow()
        db.commit()
        logger.debug(f"cancel_backfill: cancelled {record.job_id} for {symbol}")
        return True
    finally:
        db.close()


def get_backfill_job_status(symbol: str) -> dict[str, Any] | None:
    """Return the latest backfill job's status for ``symbol``, or None if
    it has never had one.

    Refreshes queued/started rows from RQ first (same pattern as
    ``backend.ai.background.get_job_status``) since the worker's own DB
    writes lag its actual RQ state slightly.
    """
    from backend.database import SessionLocal
    from backend.models import BackfillJob

    db = SessionLocal()
    try:
        record = (
            db.query(BackfillJob)
            .filter(BackfillJob.symbol == symbol.upper())
            .order_by(BackfillJob.created_at.desc())
            .first()
        )
        if record is None:
            return None

        if record.status in ("queued", "started"):
            from backend.ai.background import get_redis
            client = get_redis()
            if client is not None:
                try:
                    from rq.job import Job
                    rq_job = Job.fetch(record.job_id, connection=client)
                    rq_status = rq_job.get_status()
                    # Only trust RQ to move queued->started here — terminal
                    # states (completed/partial/failed) are written by the
                    # task itself with the tier/gap detail attached; RQ's
                    # own "finished"/"failed" doesn't carry that.
                    if rq_status == "started" and record.status != "started":
                        record.status = "started"
                        record.started_at = record.started_at or datetime.utcnow()
                        db.commit()
                except Exception:
                    # RQ has no record of this job at all — it's genuinely
                    # gone (a worker crash before it wrote "started", or —
                    # confirmed as a real, previously-live scenario,
                    # 2026-09-08 — a Redis flush wiping unstarted jobs out
                    # from under a "queued" DB row; see main.py's lifespan
                    # for the startup-flush bug this traces back to).
                    # Reconcile here so a poller (this function, or the
                    # frontend calling it) doesn't report "queued"/
                    # "started" forever for a job that will never run —
                    # same reconciliation _in_flight_job_id already does
                    # for the single-flight check, applied to the status
                    # a caller actually sees.
                    record.status = "failed"
                    record.error = "backfill job lost from the queue (worker crash or a Redis flush) before it completed"
                    record.completed_at = datetime.utcnow()
                    db.commit()

        result_data: Any = None
        if record.result:
            try:
                result_data = json.loads(record.result)
            except (ValueError, TypeError):
                result_data = record.result

        return {
            "symbol": record.symbol,
            "job_id": record.job_id,
            "status": record.status,
            "tier1_written": record.tier1_written,
            "tier2_written": record.tier2_written,
            "tier3_written": record.tier3_written,
            "gaps_found": record.gaps_found,
            "gaps_filled": record.gaps_filled,
            "result": result_data,
            "error": record.error,
            "created_at": _iso(record.created_at),
            "started_at": _iso(record.started_at),
            "completed_at": _iso(record.completed_at),
        }
    finally:
        db.close()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None
