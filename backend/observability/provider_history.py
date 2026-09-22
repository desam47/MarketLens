"""Bounded provider activity and failover evidence for local diagnostics."""

from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

_events: deque[dict[str, Any]] = deque(maxlen=200)
_lock = threading.RLock()


def record_provider_event(
    provider: str,
    method: str,
    outcome: str,
    *,
    error: str | None = None,
) -> None:
    """Record a compact provider attempt without symbol-level cardinality."""
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "provider": provider,
        "method": method,
        "outcome": outcome,
        "error": error[:240] if error else None,
    }
    with _lock:
        _events.append(event)


def provider_history(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        return list(_events)[-max(1, min(limit, 200)) :][::-1]


def provider_history_stats() -> dict[str, Any]:
    with _lock:
        events = list(_events)
    failures = [event for event in events if event["outcome"] == "failure"]
    successes = [event for event in events if event["outcome"] == "success"]
    return {
        "events": list(reversed(events[-50:])),
        "failure_count": len(failures),
        "success_count": len(successes),
        "retained_events": len(events),
    }


def clear_provider_history() -> None:
    with _lock:
        _events.clear()
