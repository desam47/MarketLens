"""Best-effort external delivery for alert triggers.

The alert engine persists the in-app trigger synchronously, then calls this
module asynchronously. External failures are isolated in ``AlertDelivery``
rows and can be retried from the API without re-firing the alert.
"""

from __future__ import annotations

import json
import logging
import smtplib
import threading
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from backend.config.settings import settings
from backend.database import SessionLocal
from backend.models import Alert, AlertDelivery, AlertTrigger
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


def _profile(alert: Alert) -> dict:
    if alert.condition_type != "signal_profile":
        return {}
    try:
        value = json.loads(alert.parameter or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _quiet_hours(profile: dict) -> bool:
    quiet = profile.get("quiet_hours")
    if not isinstance(quiet, dict) or not quiet.get("start") or not quiet.get("end"):
        return False
    try:
        current = datetime.now(_ET).time()
        start = datetime.strptime(str(quiet["start"]), "%H:%M").time()
        end = datetime.strptime(str(quiet["end"]), "%H:%M").time()
    except (TypeError, ValueError):
        return False
    if start == end:
        return True
    return start <= current < end if start < end else current >= start or current < end


def _channels(profile: dict) -> list[str]:
    values = profile.get("channels", ["in_app", "browser"])
    if not isinstance(values, list):
        return ["in_app", "browser"]
    allowed = {"in_app", "browser", "webhook", "email"}
    selected = [str(value) for value in values if str(value) in allowed]
    return selected or ["in_app"]


def _payload(trigger: AlertTrigger, alert: Alert) -> dict:
    return {
        "event": "alert_triggered",
        "trigger_id": trigger.id,
        "alert_id": alert.id,
        "alert_name": alert.name,
        "symbol": trigger.symbol,
        "message": trigger.message,
        "observed_value": trigger.observed_value,
        "triggered_at": trigger.triggered_at.isoformat() if trigger.triggered_at else None,
    }


def send_test_delivery(alert: Alert, channel: str) -> dict[str, str]:
    """Send a one-off test without creating a trigger or delivery history row."""
    profile = _profile(alert)
    if channel not in {"in_app", "browser", "webhook", "email"}:
        raise ValueError("unsupported notification channel")
    if channel == "in_app":
        return {"status": "delivered", "response": "In-app alerts are active"}
    if channel == "browser":
        return {"status": "skipped", "response": "Browser delivery is handled by connected clients"}
    if not settings.notifications.enabled:
        return {"status": "skipped", "response": "External notifications are disabled"}
    payload = {
        "event": "alert_test",
        "trigger_id": None,
        "alert_id": alert.id,
        "alert_name": alert.name,
        "symbol": alert.symbol,
        "message": f"Test notification for {alert.name}",
        "observed_value": None,
        "triggered_at": now_ny().isoformat(),
    }
    try:
        if channel == "webhook":
            response = _deliver_webhook(str(profile.get("webhook_url") or ""), payload)
        else:
            response = _deliver_email(str(profile.get("email_to") or ""), payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Alert test delivery failed: alert=%s channel=%s: %s", alert.id, channel, exc
        )
        return {"status": "failed", "response": str(exc)[:1000]}
    return {"status": "delivered", "response": response}


def _deliver_webhook(url: str, payload: dict) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("webhook URL must use http or https")
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "MarketLens/alert"},
        method="POST",
    )
    with urlopen(request, timeout=settings.notifications.request_timeout) as response:  # noqa: S310
        return f"HTTP {response.status}"


def _deliver_email(recipient: str, payload: dict) -> str:
    cfg = settings.notifications
    if not recipient.strip():
        raise ValueError("email recipient is required")
    if not cfg.smtp_host or not cfg.smtp_from:
        raise RuntimeError("SMTP is not configured")
    message = EmailMessage()
    message["Subject"] = f"MarketLens alert: {payload['symbol']}"
    message["From"] = cfg.smtp_from
    message["To"] = recipient
    message.set_content(payload.get("message") or f"Alert triggered for {payload['symbol']}")
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.request_timeout) as smtp:
        smtp.ehlo()
        if cfg.smtp_port == 587:
            smtp.starttls()
            smtp.ehlo()
        if cfg.smtp_username:
            smtp.login(cfg.smtp_username, cfg.smtp_password)
        smtp.send_message(message)
    return "SMTP accepted message"


def _attempt(
    db, delivery: AlertDelivery, trigger: AlertTrigger, alert: Alert, profile: dict
) -> str:
    delivery.attempts = int(delivery.attempts or 0) + 1
    delivery.status = "pending"
    db.commit()
    try:
        if delivery.channel == "in_app":
            result = "Available in MarketLens"
        elif delivery.channel == "browser":
            result = "Delivered by connected browser clients"
            delivery.status = "skipped"
        elif delivery.channel == "webhook":
            result = _deliver_webhook(
                str(profile.get("webhook_url") or ""), _payload(trigger, alert)
            )
        elif delivery.channel == "email":
            result = _deliver_email(str(profile.get("email_to") or ""), _payload(trigger, alert))
        else:
            raise ValueError(f"unsupported channel: {delivery.channel}")
        if delivery.status != "skipped":
            delivery.status = "delivered"
            delivery.delivered_at = now_ny()
        delivery.response = result[:1000]
    except Exception as exc:  # noqa: BLE001
        delivery.status = "failed"
        delivery.response = str(exc)[:1000]
        logger.warning(
            "Alert delivery failed: trigger=%s channel=%s: %s", trigger.id, delivery.channel, exc
        )
    db.commit()
    return delivery.status


def dispatch_trigger(trigger_id: int, retry_failures: bool = False) -> None:
    db = SessionLocal()
    try:
        trigger = db.query(AlertTrigger).filter(AlertTrigger.id == trigger_id).first()
        if trigger is None:
            return
        alert = db.query(Alert).filter(Alert.id == trigger.alert_id).first()
        if alert is None:
            return
        profile = _profile(alert)
        selected = _channels(profile) if alert.condition_type == "signal_profile" else ["in_app"]
        quiet = _quiet_hours(profile)
        failed = False
        for channel in selected:
            idempotency_key = f"{trigger.id}:{channel}"
            delivery = (
                db.query(AlertDelivery)
                .filter(AlertDelivery.idempotency_key == idempotency_key)
                .first()
            )
            if delivery is None:
                delivery = AlertDelivery(
                    trigger_id=trigger.id,
                    idempotency_key=idempotency_key,
                    channel=channel,
                    status="pending",
                    attempts=0,
                )
                db.add(delivery)
                try:
                    db.commit()
                    db.refresh(delivery)
                except Exception:
                    db.rollback()
                    delivery = (
                        db.query(AlertDelivery)
                        .filter(AlertDelivery.idempotency_key == idempotency_key)
                        .first()
                    )
                    if delivery is None:
                        raise
            if delivery.status in ("delivered", "skipped"):
                continue
            if channel not in ("in_app", "browser") and not settings.notifications.enabled:
                delivery.status = "skipped"
                delivery.response = "External notifications are disabled"
                db.commit()
            elif channel not in ("in_app", "browser") and quiet:
                delivery.status = "skipped"
                delivery.response = "Skipped during configured quiet hours"
                db.commit()
            else:
                if _attempt(db, delivery, trigger, alert, profile) == "failed":
                    failed = True
        if failed and retry_failures:
            raise RuntimeError(f"Alert delivery failed for trigger {trigger_id}")
    except Exception:  # noqa: BLE001
        logger.exception("Alert delivery dispatch failed for trigger %s", trigger_id)
        db.rollback()
        if retry_failures:
            raise
    finally:
        db.close()


def dispatch_trigger_async(trigger_id: int) -> None:
    """Queue delivery durably, falling back to a daemon thread without Redis."""
    queue = _get_delivery_queue()
    if queue is not None:
        try:
            from rq import Retry

            from backend.notifications.tasks import deliver_trigger_task

            queue.enqueue(
                deliver_trigger_task,
                kwargs={"trigger_id": trigger_id},
                retry=Retry(
                    max=settings.notifications.retry_max,
                    interval=[
                        settings.notifications.retry_backoff_seconds,
                        settings.notifications.retry_backoff_seconds * 4,
                        settings.notifications.retry_backoff_seconds * 20,
                    ],
                ),
                result_ttl=settings.background.result_ttl,
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to enqueue durable alert delivery %s: %s", trigger_id, exc)
    thread = threading.Thread(
        target=dispatch_trigger,
        args=(trigger_id,),
        daemon=True,
        name=f"alert-delivery-{trigger_id}",
    )
    thread.start()


def _get_delivery_queue():
    """Use the shared RQ queue when background processing is available."""
    try:
        from backend.ai.background import get_queue

        return get_queue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Alert delivery queue unavailable: %s", exc)
        return None


def retry_delivery(delivery_id: int) -> bool:
    db = SessionLocal()
    try:
        delivery = db.query(AlertDelivery).filter(AlertDelivery.id == delivery_id).first()
        if delivery is None:
            return False
        trigger = db.query(AlertTrigger).filter(AlertTrigger.id == delivery.trigger_id).first()
        alert = db.query(Alert).filter(Alert.id == trigger.alert_id).first() if trigger else None
        if trigger is None or alert is None:
            return False
        _attempt(db, delivery, trigger, alert, _profile(alert))
        return True
    finally:
        db.close()
