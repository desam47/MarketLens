"""Short-lived server-side handles for verified AI trade plans.

The browser receives only an opaque handle after the analysis pipeline has
validated an actionable plan.  Tracking looks the handle up again and
revalidates the retained, server-authored plan against current context.  That
keeps a client from turning an arbitrary hand-edited payload into a tracked
AI setup while still requiring the trader's explicit confirmation in the UI.

Handles deliberately live in memory: they are valid for a brief UI action,
not durable credentials.  Restarting the API or waiting past the TTL simply
requires rerunning the analysis.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from backend.ai.prompt import TradePlan

_TTL_SECONDS = 10 * 60
_MAX_ENTRIES = 256


@dataclass(frozen=True)
class VerifiedPlan:
    """The immutable server-side provenance for one verified setup."""

    id: str
    symbol: str
    timeframe: str
    provider: str
    model: str
    plan: TradePlan
    issued_at: float


_lock = threading.Lock()
_plans: OrderedDict[str, VerifiedPlan] = OrderedDict()
_ids_by_source: dict[str, str] = {}


def _purge_expired(now: float) -> None:
    global _ids_by_source
    expired_ids = [
        plan_id for plan_id, plan in _plans.items() if now - plan.issued_at > _TTL_SECONDS
    ]
    for plan_id in expired_ids:
        _plans.pop(plan_id, None)
    if expired_ids:
        expired = set(expired_ids)
        _ids_by_source = {key: value for key, value in _ids_by_source.items() if value not in expired}


def issue_verified_plan(
    *,
    symbol: str,
    timeframe: str,
    provider: str,
    model: str,
    plan: TradePlan | dict[str, Any] | None,
    validation: dict[str, Any] | None,
    source_key: str | None = None,
) -> str | None:
    """Return an opaque ID only for a server-verified actionable plan.

    ``source_key`` lets a background-job poll return the same handle on each
    poll without putting a secret into the RQ worker's result payload.
    """
    global _ids_by_source
    if (validation or {}).get("status") != "verified":
        return None
    try:
        parsed_plan = plan if isinstance(plan, TradePlan) else TradePlan.model_validate(plan)
    except (TypeError, ValueError):
        return None
    if parsed_plan.recommendation not in ("buy", "sell"):
        return None

    now = time.monotonic()
    with _lock:
        _purge_expired(now)
        if source_key:
            existing_id = _ids_by_source.get(source_key)
            if existing_id and existing_id in _plans:
                return existing_id

        plan_id = secrets.token_urlsafe(24)
        _plans[plan_id] = VerifiedPlan(
            id=plan_id,
            symbol=symbol.upper(),
            timeframe=timeframe,
            provider=provider or "unknown",
            model=model or "unknown",
            plan=parsed_plan.model_copy(deep=True),
            issued_at=now,
        )
        if source_key:
            _ids_by_source[source_key] = plan_id
        while len(_plans) > _MAX_ENTRIES:
            evicted_id, _ = _plans.popitem(last=False)
            _ids_by_source = {
                key: value for key, value in _ids_by_source.items() if value != evicted_id
            }
        return plan_id


def get_verified_plan(plan_id: str) -> VerifiedPlan | None:
    """Return an unexpired plan handle without consuming it (retry-safe)."""
    now = time.monotonic()
    with _lock:
        _purge_expired(now)
        return _plans.get(plan_id)


def _clear_verified_plan_store() -> None:
    """Test-only reset for deterministic store tests."""
    with _lock:
        _plans.clear()
        _ids_by_source.clear()
