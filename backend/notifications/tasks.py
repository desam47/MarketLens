"""RQ task bodies for durable alert notification delivery."""

from __future__ import annotations

from backend.notifications.delivery import dispatch_trigger


def deliver_trigger_task(trigger_id: int) -> None:
    """Deliver one trigger; raising failures lets RQ apply its retry policy."""
    dispatch_trigger(trigger_id, retry_failures=True)
