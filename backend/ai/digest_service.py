"""
AI feature 2 (Version 4): scheduled local summary scheduler.

No general-purpose scheduler exists anywhere in this codebase
(confirmed absent: APScheduler/celery/croniter/rq-scheduler). Rather
than add one for a small set of summary slots, this reuses the codebase's
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
pattern as ``alerts_engine.startup()``. The configured slots are
premarket, midday, post-market, and the Friday weekly review.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from backend.config.settings import settings
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)


class DigestService:
    """Fires each local scheduled summary once per period.

    The service remains deliberately small and in-process.  Durable repository
    checks make restarts safe, while the configured slots cover premarket,
    midday, post-market, and the Friday weekly review.
    """

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
        for session, hour, minute in self._scheduled_slots(cfg, ny):
            target = ny.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if ny >= target and self._last_fired.get(session) != today:
                self._last_fired[session] = today
                # ``_last_fired`` lives in memory, so every process start (each dev reload,
                # each deploy) used to regenerate the slot: 76 digests in a day instead of 2,
                # each one a real AI call. The DB is the durable record of "already done".
                if await self._already_generated(session, target):
                    logger.info("%s digest already generated today; not regenerating", session)
                    continue
                await self._fire(session)

    @staticmethod
    def _int_setting(cfg, name: str, default: int) -> int:
        """Read a config integer without letting unconfigured test mocks leak in."""
        value = getattr(cfg, name, default)
        return value if isinstance(value, int) and not isinstance(value, bool) else default

    @staticmethod
    def _optional_int_setting(cfg, name: str) -> int | None:
        value = getattr(cfg, name, None)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @classmethod
    def _scheduled_slots(cls, cfg, ny: datetime) -> list[tuple[str, int, int]]:
        slots = [
            (
                "premarket",
                cls._int_setting(cfg, "premarket_hour", 8),
                cls._int_setting(cfg, "premarket_minute", 30),
            ),
            (
                "close",
                cls._int_setting(cfg, "close_hour", 16),
                cls._int_setting(cfg, "close_minute", 15),
            ),
        ]
        midday_hour = cls._optional_int_setting(cfg, "midday_hour")
        midday_minute = cls._optional_int_setting(cfg, "midday_minute")
        if midday_hour is not None and midday_minute is not None:
            slots.insert(1, ("midday", midday_hour, midday_minute))
        weekly_enabled = getattr(cfg, "weekly_enabled", True)
        if not isinstance(weekly_enabled, bool):
            weekly_enabled = True
        weekly_day = cls._optional_int_setting(cfg, "weekly_day")
        weekly_hour = cls._optional_int_setting(cfg, "weekly_hour")
        weekly_minute = cls._optional_int_setting(cfg, "weekly_minute")
        if (
            weekly_enabled
            and weekly_day is not None
            and weekly_hour is not None
            and weekly_minute is not None
            and ny.weekday() == weekly_day
        ):
            slots.append(
                (
                    "weekly",
                    weekly_hour,
                    weekly_minute,
                )
            )
        return slots

    async def _already_generated(self, session: str, target: datetime) -> bool:
        """Has a ``session`` digest been stored since today's slot time?

        Counting only rows at/after the slot time means a manual run earlier in the day
        does not suppress the scheduled one. Fails open: if the check itself errors,
        generate (the old behaviour) rather than skip the day's digest.
        """
        try:
            return await asyncio.to_thread(self._digest_exists_since, session, target)
        except Exception:  # noqa: BLE001
            logger.warning("digest dedupe check failed; generating anyway", exc_info=True)
            return False

    @staticmethod
    def _digest_exists_since(session: str, since: datetime) -> bool:
        from backend.repositories.ai_digest_repository import AIDigestRepository

        repo = AIDigestRepository()
        try:
            return repo.exists_since(session, since)
        finally:
            repo.close()

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
