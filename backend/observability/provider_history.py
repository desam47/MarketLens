"""Bounded provider activity and failover evidence for local diagnostics."""

from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

_events: deque[dict[str, Any]] = deque(maxlen=200)
_lock = threading.RLock()
_MAX_PERSISTED_EVENTS = 10_000
_RETENTION_DAYS = 30


def record_provider_event(
    provider: str,
    method: str,
    outcome: str,
    *,
    error: str | None = None,
) -> None:
    """Record a compact provider attempt without symbol-level cardinality."""
    recorded_at = datetime.now(UTC)
    event = {
        "timestamp": recorded_at.isoformat(),
        "provider": provider,
        "method": method,
        "outcome": outcome,
        "error": error[:240] if error else None,
    }
    with _lock:
        _events.append(event)
    _persist_event(recorded_at, event)


def _persist_event(recorded_at: datetime, event: dict[str, Any]) -> None:
    """Best-effort durable write; diagnostics must never break market data."""
    try:
        from backend.database import SessionLocal
        from backend.models.market_data_sql import ProviderEventModel

        db = SessionLocal()
        try:
            db.add(
                ProviderEventModel(
                    timestamp=recorded_at.replace(tzinfo=None),
                    provider=event["provider"],
                    method=event["method"],
                    outcome=event["outcome"],
                    error=event.get("error"),
                )
            )
            cutoff = (recorded_at - timedelta(days=_RETENTION_DAYS)).replace(tzinfo=None)
            db.query(ProviderEventModel).filter(ProviderEventModel.timestamp < cutoff).delete(
                synchronize_session=False
            )
            if len(_events) % 50 == 0:
                ids = [
                    row.id
                    for row in db.query(ProviderEventModel.id)
                    .order_by(ProviderEventModel.id.desc())
                    .offset(_MAX_PERSISTED_EVENTS)
                    .all()
                ]
                if ids:
                    db.query(ProviderEventModel).filter(ProviderEventModel.id.in_(ids)).delete(
                        synchronize_session=False
                    )
            db.commit()
        finally:
            db.close()
    except Exception:
        return


def provider_history(limit: int = 50) -> list[dict[str, Any]]:
    try:
        from backend.database import SessionLocal
        from backend.models.market_data_sql import ProviderEventModel

        db = SessionLocal()
        try:
            rows = (
                db.query(ProviderEventModel)
                .order_by(ProviderEventModel.id.desc())
                .limit(max(1, min(limit, 200)))
                .all()
            )
            if rows:
                return [
                    {
                        "timestamp": row.timestamp.replace(tzinfo=UTC).isoformat(),
                        "provider": row.provider,
                        "method": row.method,
                        "outcome": row.outcome,
                        "error": row.error,
                    }
                    for row in rows
                ]
        finally:
            db.close()
    except Exception:
        pass
    with _lock:
        return list(_events)[-max(1, min(limit, 200)) :][::-1]


def provider_history_stats() -> dict[str, Any]:
    events = provider_history(200)
    failures = [event for event in events if event["outcome"] == "failure"]
    successes = [event for event in events if event["outcome"] == "success"]
    return {
        "events": events[:50],
        "failure_count": len(failures),
        "success_count": len(successes),
        "retained_events": len(events),
    }


def clear_provider_history() -> None:
    with _lock:
        _events.clear()
