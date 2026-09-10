"""
AI feature 2 (Version 4): daily/session digest scheduler.

No general-purpose scheduler exists anywhere in this codebase
(confirmed absent: APScheduler/celery/croniter/rq-scheduler). Rather
than add one for a single twice-daily job, this reuses the codebase's
own established pattern for "run this at a time of day" —
ingestion_service.py's hand-rolled
``while self.is_running: <time gate>: do work; sleep(jittered)``
loops (e.g. ``_daily_write_loop``).

One deliberate difference from that precedent: instead of a narrow
"minute == target and second < N" window (which requires the tick
cadence and window width to be carefully matched — get it wrong and
a slot can be silently skipped some days), this tracks "have I
already fired session X today" and fires as soon as the loop notices
the target time has passed and today's flag isn't set yet. Robust to
any tick cadence, and trivial to unit test (no sleep/timing
involved — just call ``_maybe_fire()`` with a mocked ``now_ny()``).

Started from ``backend.api.main``'s lifespan hook, same lifecycle
pattern as ``alerts_engine.startup()``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from backend.config.settings import settings
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)


class DigestService:
    """Fires ``generate_and_store_digest()`` once per day per session
    (premarket / close), on the FastAPI app's own event loop."""

    def __init__(self) -> None:
        self.is_running = False
        self._task: asyncio.Task | None = None
        self._last_fired: dict[str, date] = {}

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("DigestService started")

    def stop(self) -> None:
        self.is_running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while self.is_running:
            try:
                await self._maybe_fire()
            except Exception:  # noqa: BLE001
                logger.exception("digest loop error")
            await self._jittered_sleep()

    async def _maybe_fire(self) -> None:
        cfg = settings.ai_digest
        if not cfg.enabled:
            return

        ny = now_ny()
        today = ny.date()
        for session, hour, minute in (
            ("premarket", cfg.premarket_hour, cfg.premarket_minute),
            ("close", cfg.close_hour, cfg.close_minute),
        ):
            target = ny.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if ny >= target and self._last_fired.get(session) != today:
                self._last_fired[session] = today
                await self._fire(session)

    async def _fire(self, session: str) -> None:
        from backend.ai.digest import generate_and_store_digest

        try:
            await asyncio.to_thread(generate_and_store_digest, session)
            logger.info("Generated %s digest", session)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to generate %s digest", session)

    async def _jittered_sleep(self, base_seconds: float = 60.0, jitter: float = 10.0) -> None:
        import random

        offset = random.uniform(-jitter, jitter)
        await asyncio.sleep(max(1.0, base_seconds + offset))


digest_service = DigestService()
