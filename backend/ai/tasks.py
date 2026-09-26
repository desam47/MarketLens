"""
RQ task bodies (Phase 2.5: Background AI processing).

Each task function is what the worker process actually runs. The task
takes primitive arguments (so RQ can serialize them) and updates the
``ai_analysis_jobs`` DB row as it progresses.

Workers are started with::

    rq worker --url redis://localhost:6379/0 marketlens-workers

Or, if you're running inside the project (with the venv active)::

    python -m backend.workers.ai_worker
"""

from __future__ import annotations

import logging
import traceback

from backend.ai.analyze import analyze_symbol

# RQ workers run these tasks in loop-less processes, so the async
# analyze_symbol is bridged with run_sync rather than awaited.
from backend.ai.sync_bridge import run_sync
from backend.api.ai_templates.router import resolve_and_render
from backend.database import SessionLocal

logger = logging.getLogger(__name__)


# ── Single-symbol analysis task ────────────────────────────────────────────


def _job_payload(result, template_id: int | None, template_name: str | None) -> dict:
    """The job result in the same shape as ``POST /api/ai/analyze``.

    Uses the streaming route's serializer, so a background result carries
    the same evidence, plan validation and context as a blocking one.
    """
    from backend.ai.analyze import _result_to_dict

    payload = _result_to_dict(result)
    payload["template_id"] = template_id
    payload["template_name"] = template_name
    return payload


def analyze_symbol_task(
    symbol: str,
    timeframe: str = "1d",
    template_id: int | None = None,
    template_name: str | None = None,
    job_id: str | None = None,
    portfolio_symbols: list[str] | None = None,
) -> dict:
    """Run an AI analysis for ``symbol`` and persist the result.

    Parameters
    ----------
    symbol : str
        Ticker symbol (uppercased before analysis).
    timeframe : str
        Primary analysis window (e.g. "1d", "4h").
    template_id : int | None
        Optional AI template to render before calling the AI.
    template_name : str | None
        Human-readable template name. Captured at enqueue time; we don't
        re-query it on the worker side.
    job_id : str | None
        RQ job ID for status updates. Optional — if absent we skip the
        DB updates (useful for direct invocation in tests).
    portfolio_symbols : list[str] | None
        Optional peer tickers for cross-ticker context (O10).

    Returns
    -------
    dict
        The serialized result with keys ``summary``, ``trend``,
        ``confidence``, ``supporting_factors``, ``risk_factors``,
        ``timeframe_conflicts``, ``key_levels``, ``provider``, ``model``,
        ``is_uncertain``, ``template_id``, ``template_name``.
    """

    if not job_id:
        return _run_direct(symbol, timeframe, template_id, template_name, portfolio_symbols)

    _update_status(job_id, "started")

    # Render template (if any) on a fresh DB session.
    rendered_system: str | None = None
    resolved_template_id: int | None = None
    resolved_template_name: str | None = template_name
    if template_id is not None:
        db = SessionLocal()
        try:
            rendered_system = resolve_and_render(
                db,
                template_id,
                {"symbol": symbol, "timeframe": timeframe},
            )
            resolved_template_id = template_id
        except Exception as exc:  # noqa: BLE001
            # Inactive, not found, etc. — bubble up so the user sees the
            # error rather than a silent fallback to the default prompt.
            _update_status(job_id, "failed", error=str(exc))
            raise
        finally:
            db.close()

    try:
        result = run_sync(
            analyze_symbol(
                symbol=symbol,
                timeframe=timeframe,
                system_prompt_override=rendered_system,
                portfolio_symbols=portfolio_symbols,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI analysis job %s failed", job_id)
        _update_status(job_id, "failed", error=str(exc) + "\n" + traceback.format_exc())
        raise

    payload = _job_payload(result, resolved_template_id, resolved_template_name)

    _update_status(job_id, "finished", result=payload)
    return payload


def _run_direct(
    symbol: str,
    timeframe: str,
    template_id: int | None,
    template_name: str | None,
    portfolio_symbols: list[str] | None = None,
) -> dict:
    """Run the analysis without touching the DB job table (used by tests)."""

    rendered_system: str | None = None
    if template_id is not None:
        db = SessionLocal()
        try:
            rendered_system = resolve_and_render(
                db,
                template_id,
                {"symbol": symbol, "timeframe": timeframe},
            )
        finally:
            db.close()

    result = run_sync(
        analyze_symbol(
            symbol=symbol,
            timeframe=timeframe,
            system_prompt_override=rendered_system,
            portfolio_symbols=portfolio_symbols,
        )
    )
    return _job_payload(result, template_id, template_name)


# ── Alert commentary task (Version 4, AI feature 3) ───────────────────────


def generate_alert_commentary_task(trigger_id: int) -> None:
    """Worker entry point: generate + persist AI commentary for an
    AlertTrigger row.

    Thin wrapper — all the real logic (context building, prompting,
    parsing, writing the row) lives in
    ``backend.ai.alert_commentary.generate_commentary``, which never
    raises. Unlike ``analyze_symbol_task``, there's no separate
    job-status row to update: the AlertTrigger row IS the state, and
    it's already committed by the time this task runs.
    """
    from backend.ai.alert_commentary import generate_commentary

    generate_commentary(trigger_id)


# ── DB update helper ──────────────────────────────────────────────────────


def _update_status(
    job_id: str,
    status: str,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    """Update the AIAnalysisJob row for ``job_id``."""
    from backend.repositories.ai_analysis_job_repository import AIAnalysisJobRepository

    db = SessionLocal()
    try:
        repo = AIAnalysisJobRepository(db)
        repo.update_status(job_id, status, result=result, error=error)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("Failed to update job %s to %s: %s", job_id, status, exc)
    finally:
        db.close()
