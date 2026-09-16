"""
Proactive chat nudges (2026-09-15).

The universal AI Hub chat (backend.ai.chat) is otherwise 100%
reactive — nothing appears in the thread unless the trader sends a
message. This polls two signal sources that already exist elsewhere
in this codebase and, when either fires, drops an unprompted
assistant message into the single universal session
(ChatRepository.get_or_create_open_session) so it's waiting there the
next time the trader opens (or polls) the AI Hub:

  - a NEW AlertTrigger row. AlertsEngine already persists these and
    kicks off async AI commentary (backend/ai/alert_commentary.py) —
    this only decides how the result reaches chat, it doesn't
    duplicate any evaluation logic.
  - a watched symbol's scanner ``signed_total_score`` (the same value
    backend.ai.market_baseline._watchlist_snapshot already surfaces
    for "which of my names look weak?") crossing +/- score_threshold
    since the previous tick. Crossing rather than level, so a symbol
    sitting at an extreme doesn't re-nudge every tick, plus a per
    symbol cooldown so one oscillating right at the line doesn't spam
    the thread.

Same lifecycle/loop shape as backend.ai.digest_service.DigestService
(asyncio task on the FastAPI event loop, jittered poll) — no general
scheduler exists in this codebase, and one twice-a-tick job doesn't
justify adding one. Started from backend.api.main's lifespan hook,
alongside alerts_engine.startup() / digest_service.start().

Every tick is best-effort: a failure in one symbol or one trigger
logs and moves on rather than aborting the whole tick, and a failure
in the whole tick is caught by the loop so the service itself never
dies.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from backend.config.settings import settings
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)


class NudgeService:
    def __init__(self) -> None:
        self.is_running = False
        self._task: asyncio.Task | None = None
        self._last_trigger_id: int = 0
        self._last_scores: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("NudgeService started")

    def stop(self) -> None:
        self.is_running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        try:
            await asyncio.to_thread(self._init_watermark)
        except Exception:  # noqa: BLE001
            logger.exception("nudge watermark init failed")
        while self.is_running:
            try:
                await asyncio.to_thread(self._tick)
            except Exception:  # noqa: BLE001
                logger.exception("nudge loop error")
            await self._jittered_sleep()

    def _init_watermark(self) -> None:
        """Highest existing AlertTrigger id at startup, so a restart
        never replays trigger history that predates this service."""
        from backend.repositories.alert_repository import AlertRepository

        repo = AlertRepository()
        try:
            recent = repo.get_recent_triggers()
            self._last_trigger_id = max((t.id for t in recent), default=0)
        finally:
            repo.close()

    def _tick(self) -> None:
        cfg = settings.ai_nudges
        if not cfg.enabled:
            return
        self._check_alert_triggers()
        self._check_big_moves(cfg.score_threshold, cfg.cooldown_seconds)

    def _check_alert_triggers(self) -> None:
        from backend.repositories.alert_repository import AlertRepository

        try:
            repo = AlertRepository()
        except Exception:  # noqa: BLE001
            logger.exception("nudge alert-trigger check failed")
            return
        try:
            # A generous fixed lookback (rather than "since the last
            # tick") means a slow tick or a missed one never drops a
            # trigger — the id watermark is what actually dedupes.
            since = now_ny() - timedelta(minutes=10)
            triggers = [
                t for t in repo.get_recent_triggers(since=since)
                if t.id > self._last_trigger_id
            ]
            if not triggers:
                return
            triggers.sort(key=lambda t: t.id)
            for trigger in triggers:
                text = self._format_alert_nudge(trigger)
                self._insert_nudge(text)
            self._last_trigger_id = triggers[-1].id
        except Exception:  # noqa: BLE001
            logger.exception("nudge alert-trigger check failed")
        finally:
            repo.close()

    @staticmethod
    def _format_alert_nudge(trigger) -> str:
        name = trigger.alert.name if trigger.alert is not None else "alert"
        detail = trigger.ai_commentary or trigger.message or f"observed {trigger.observed_value}"
        return f"Alert fired — {trigger.symbol} \"{name}\": {detail}"

    def _check_big_moves(self, score_threshold: float, cooldown_seconds: float) -> None:
        try:
            from backend.api.main_helpers import _watched_symbols
            from backend.scanner.scanner import market_scanner
        except Exception:  # noqa: BLE001
            logger.exception("nudge big-move check failed to import")
            return

        now = time.monotonic()
        for sym in sorted(set(_watched_symbols())):
            result = market_scanner.scan_results.get(sym)
            if result is None:
                continue
            try:
                score = float(result.calculate_signed_total_score())
            except Exception:  # noqa: BLE001
                continue

            prev = self._last_scores.get(sym)
            self._last_scores[sym] = score
            if prev is None:
                continue  # first observation of this symbol — no baseline to cross from

            crossed_up = prev < score_threshold <= score
            crossed_down = prev > -score_threshold >= score
            if not (crossed_up or crossed_down):
                continue
            if now < self._cooldown_until.get(sym, 0.0):
                continue
            self._cooldown_until[sym] = now + cooldown_seconds

            direction = "bullish" if crossed_up else "bearish"
            self._insert_nudge(
                f"{sym} just crossed into strongly {direction} scanner territory "
                f"(signed score {score:+.1f}) — might be worth a look."
            )

    @staticmethod
    def _insert_nudge(text: str) -> None:
        from backend.repositories.chat_repository import ChatRepository

        try:
            repo = ChatRepository()
        except Exception:  # noqa: BLE001
            logger.exception("failed to insert proactive nudge")
            return
        try:
            session = repo.get_or_create_open_session(scope="universal")
            repo.add_message(session.id, "assistant", text)
        except Exception:  # noqa: BLE001
            logger.exception("failed to insert proactive nudge")
        finally:
            repo.close()

    async def _jittered_sleep(self) -> None:
        import random

        base = settings.ai_nudges.poll_interval_seconds
        offset = random.uniform(-0.2 * base, 0.2 * base)
        await asyncio.sleep(max(1.0, base + offset))


nudge_service = NudgeService()
