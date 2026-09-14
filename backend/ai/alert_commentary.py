"""
Version 4, AI feature 3 — alert-triggered AI commentary.

Generates a short AI note explaining why a specific AlertTrigger
fired, then writes it back into that trigger's own row. Runs
out-of-band (RQ job, see backend/ai/background.py's
enqueue_alert_commentary_job + backend/ai/tasks.py's
generate_alert_commentary_task) — never called from the
trigger-persist path itself, which is latency-sensitive (an async
request handler and a live-tick callback that gates every other
alert evaluation for that tick).

Follows analyze_symbol's "never raise for an expected failure mode"
contract: AI off, a bad/malformed reply, or a missing trigger/alert
row all degrade to returning None (and leaving ai_commentary as
NULL) rather than raising. This is enrichment on an event that's
already meaningfully recorded without it — a failure here must never
look like anything went wrong with the alert itself.
"""
from __future__ import annotations

import logging

from backend.ai.context import build_context
from backend.ai.manager import ai_manager
from backend.ai.sync_bridge import run_sync
from backend.ai.prompt import (
    ALERT_COMMENTARY_SYSTEM_PROMPT,
    build_alert_commentary_prompt,
    parse_alert_commentary_reply,
)
from backend.database import SessionLocal
from backend.models import Alert, AlertTrigger

logger = logging.getLogger(__name__)


def generate_commentary(trigger_id: int) -> str | None:
    """Generate and persist AI commentary for ``trigger_id``.

    Returns the commentary string on success, or ``None`` on any
    failure mode (trigger/alert not found, AI off, bad reply) —
    matching this function's contract of never raising. On success,
    the AlertTrigger row's ``ai_commentary`` column is updated and
    committed; on failure, the row is left untouched (still NULL from
    insert, or whatever it was before).
    """
    db = SessionLocal()
    try:
        trigger = db.query(AlertTrigger).filter(AlertTrigger.id == trigger_id).first()
        if trigger is None:
            logger.warning("generate_commentary: trigger %s not found", trigger_id)
            return None

        alert = db.query(Alert).filter(Alert.id == trigger.alert_id).first()
        if alert is None:
            logger.warning(
                "generate_commentary: alert %s not found for trigger %s",
                trigger.alert_id, trigger_id,
            )
            return None

        # Runs in an RQ worker thread (no event loop) — bridge the
        # async manager calls.
        if not run_sync(ai_manager.is_available()):
            return None

        # Skip news/fundamentals — commentary explains *why this
        # specific condition fired*, not a full research brief; keep
        # it fast and cheap. Divergence stays on since many alert
        # conditions (RSI/MACD extremes, trend flips) relate to it.
        try:
            context = build_context(
                trigger.symbol, include_news=False, include_fundamentals=False,
            )
            context_dict = context.to_dict()
        except Exception as e:  # noqa: BLE001
            # InsufficientDataError or any other context-building
            # failure — still worth trying with just the alert/trigger
            # facts alone rather than giving up entirely.
            logger.info(
                "generate_commentary: build_context failed for %s, using alert facts only: %s",
                trigger.symbol, e,
            )
            context_dict = {}

        payload = {
            "alert": {
                "name": alert.name,
                "symbol": alert.symbol,
                "condition_type": alert.condition_type,
                "parameter": alert.parameter,
            },
            "trigger": {
                "observed_value": trigger.observed_value,
                "message": trigger.message,
                "triggered_at": str(trigger.triggered_at) if trigger.triggered_at else None,
            },
            "context": context_dict,
        }

        try:
            resp = run_sync(ai_manager.complete(
                prompt=build_alert_commentary_prompt(payload),
                system=ALERT_COMMENTARY_SYSTEM_PROMPT,
                max_tokens=200,
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("Alert commentary AI call raised: %s", e)
            return None

        if resp.text is None:
            return None

        try:
            parsed = parse_alert_commentary_reply(resp.text)
        except Exception as e:  # noqa: BLE001
            logger.info("Alert commentary reply failed to parse: %s", e)
            return None

        trigger.ai_commentary = parsed.commentary
        db.commit()
        return parsed.commentary
    except Exception:  # noqa: BLE001
        logger.exception("generate_commentary failed for trigger %s", trigger_id)
        db.rollback()
        return None
    finally:
        db.close()
