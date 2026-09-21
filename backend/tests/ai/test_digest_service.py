"""
Tests for backend.ai.digest_service.DigestService's time-gate logic.

Deliberately tests _maybe_fire() directly rather than the sleep loop
— the "fire once per day, track last-fired date" design means no
asyncio.sleep timing is involved in the actual firing decision, so
this can be tested with a mocked now_ny() and no real waiting.
"""

import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from backend.ai.digest_service import DigestService


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestDigestServiceTimeGate(unittest.TestCase):
    def setUp(self):
        self.service = DigestService()
        # These tests are about the time gate; the durable "already generated" check
        # (covered in TestDigestServiceDurableDedupe) must not touch a real database.
        patcher = patch.object(
            self.service, "_already_generated", new_callable=AsyncMock, return_value=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("backend.ai.digest_service.settings")
    @patch("backend.ai.digest_service.now_ny")
    def test_fires_once_time_has_passed_target(self, mock_now, mock_settings):
        mock_settings.ai_digest.enabled = True
        mock_settings.ai_digest.premarket_hour = 8
        mock_settings.ai_digest.premarket_minute = 30
        mock_settings.ai_digest.close_hour = 16
        mock_settings.ai_digest.close_minute = 15
        mock_now.return_value = datetime(2026, 9, 9, 8, 31)  # 1 min after premarket target

        with patch.object(self.service, "_fire", new_callable=AsyncMock) as mock_fire:
            _run(self.service._maybe_fire())

        mock_fire.assert_called_once_with("premarket")

    @patch("backend.ai.digest_service.settings")
    @patch("backend.ai.digest_service.now_ny")
    def test_does_not_fire_before_target_time(self, mock_now, mock_settings):
        mock_settings.ai_digest.enabled = True
        mock_settings.ai_digest.premarket_hour = 8
        mock_settings.ai_digest.premarket_minute = 30
        mock_settings.ai_digest.close_hour = 16
        mock_settings.ai_digest.close_minute = 15
        mock_now.return_value = datetime(2026, 9, 9, 8, 29)  # 1 min BEFORE target

        with patch.object(self.service, "_fire", new_callable=AsyncMock) as mock_fire:
            _run(self.service._maybe_fire())

        mock_fire.assert_not_called()

    @patch("backend.ai.digest_service.settings")
    @patch("backend.ai.digest_service.now_ny")
    def test_does_not_fire_twice_the_same_day(self, mock_now, mock_settings):
        mock_settings.ai_digest.enabled = True
        mock_settings.ai_digest.premarket_hour = 8
        mock_settings.ai_digest.premarket_minute = 30
        mock_settings.ai_digest.close_hour = 16
        mock_settings.ai_digest.close_minute = 15
        mock_now.return_value = datetime(2026, 9, 9, 9, 0)

        with patch.object(self.service, "_fire", new_callable=AsyncMock) as mock_fire:
            _run(self.service._maybe_fire())
            # A second check later the same day, still past the target.
            mock_now.return_value = datetime(2026, 9, 9, 12, 0)
            _run(self.service._maybe_fire())

        mock_fire.assert_called_once_with("premarket")

    @patch("backend.ai.digest_service.settings")
    @patch("backend.ai.digest_service.now_ny")
    def test_fires_again_the_next_day(self, mock_now, mock_settings):
        mock_settings.ai_digest.enabled = True
        mock_settings.ai_digest.premarket_hour = 8
        mock_settings.ai_digest.premarket_minute = 30
        mock_settings.ai_digest.close_hour = 16
        mock_settings.ai_digest.close_minute = 15

        with patch.object(self.service, "_fire", new_callable=AsyncMock) as mock_fire:
            mock_now.return_value = datetime(2026, 9, 9, 9, 0)
            _run(self.service._maybe_fire())
            mock_now.return_value = datetime(2026, 9, 10, 9, 0)
            _run(self.service._maybe_fire())

        self.assertEqual(mock_fire.call_count, 2)

    @patch("backend.ai.digest_service.settings")
    @patch("backend.ai.digest_service.now_ny")
    def test_fires_both_sessions_independently(self, mock_now, mock_settings):
        mock_settings.ai_digest.enabled = True
        mock_settings.ai_digest.premarket_hour = 8
        mock_settings.ai_digest.premarket_minute = 30
        mock_settings.ai_digest.close_hour = 16
        mock_settings.ai_digest.close_minute = 15

        with patch.object(self.service, "_fire", new_callable=AsyncMock) as mock_fire:
            mock_now.return_value = datetime(2026, 9, 9, 9, 0)  # past premarket only
            _run(self.service._maybe_fire())
            mock_now.return_value = datetime(2026, 9, 9, 17, 0)  # past close too
            _run(self.service._maybe_fire())

        calls = [c.args[0] for c in mock_fire.call_args_list]
        self.assertEqual(calls, ["premarket", "close"])

    @patch("backend.ai.digest_service.settings")
    @patch("backend.ai.digest_service.now_ny")
    def test_disabled_never_fires(self, mock_now, mock_settings):
        mock_settings.ai_digest.enabled = False
        mock_now.return_value = datetime(2026, 9, 9, 9, 0)

        with patch.object(self.service, "_fire", new_callable=AsyncMock) as mock_fire:
            _run(self.service._maybe_fire())

        mock_fire.assert_not_called()


class TestDigestServiceDurableDedupe(unittest.TestCase):
    """A process restart must not regenerate a slot that is already in the database.

    ``_last_fired`` is in memory, so before this every dev reload / deploy after the
    slot time fired a fresh (real, paid) AI digest: 76 rows in one day instead of 2.
    These tests use the real repository against a throwaway SQLite database.
    """

    PREMARKET = (8, 30)
    CLOSE = (16, 15)

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from backend.models import AIDigest

        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        AIDigest.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)

        for target, obj in (
            ("backend.repositories.ai_digest_repository.SessionLocal", self.Session),
            ("backend.ai.digest_service.settings.ai_digest.premarket_hour", 8),
            ("backend.ai.digest_service.settings.ai_digest.premarket_minute", 30),
            ("backend.ai.digest_service.settings.ai_digest.close_hour", 16),
            ("backend.ai.digest_service.settings.ai_digest.close_minute", 15),
            ("backend.ai.digest_service.settings.ai_digest.enabled", True),
        ):
            p = patch(target, obj)
            p.start()
            self.addCleanup(p.stop)

    def _store(self, session: str, when: datetime) -> None:
        from backend.repositories.ai_digest_repository import AIDigestRepository

        repo = AIDigestRepository()
        try:
            repo.create(
                session=session, market_regime=None, narrative="n", payload="{}", generated_at=when
            )
        finally:
            repo.close()

    def _tick(self, now: datetime, service: DigestService | None = None) -> list[str]:
        """Run one scheduler check at ``now`` on a FRESH service (= a process restart)."""
        service = service or DigestService()
        with (
            patch("backend.ai.digest_service.now_ny", return_value=now),
            patch.object(service, "_fire", new_callable=AsyncMock) as fire,
        ):
            _run(service._maybe_fire())
        return [c.args[0] for c in fire.call_args_list]

    def test_restart_after_the_slot_was_generated_does_not_regenerate(self):
        self._store("premarket", datetime(2026, 9, 9, 8, 31))
        self.assertEqual(self._tick(datetime(2026, 9, 9, 11, 0)), [])
        self.assertEqual(
            self._tick(datetime(2026, 9, 9, 11, 5)), [], "and again on the next restart"
        )

    def test_nothing_stored_yet_fires(self):
        self.assertEqual(self._tick(datetime(2026, 9, 9, 9, 0)), ["premarket"])

    def test_yesterdays_digest_does_not_suppress_today(self):
        self._store("premarket", datetime(2026, 9, 8, 8, 31))
        self.assertEqual(self._tick(datetime(2026, 9, 9, 9, 0)), ["premarket"])

    def test_a_manual_run_before_the_slot_time_does_not_suppress_the_scheduled_one(self):
        self._store("premarket", datetime(2026, 9, 9, 7, 0))
        self.assertEqual(self._tick(datetime(2026, 9, 9, 9, 0)), ["premarket"])

    def test_sessions_are_independent(self):
        self._store("premarket", datetime(2026, 9, 9, 8, 40))
        self.assertEqual(self._tick(datetime(2026, 9, 9, 17, 0)), ["close"])

    def test_a_skipped_slot_is_not_rechecked_every_tick(self):
        self._store("premarket", datetime(2026, 9, 9, 8, 40))
        service = DigestService()
        with patch.object(service, "_digest_exists_since", return_value=True) as check:
            self._tick(datetime(2026, 9, 9, 9, 0), service)
            self._tick(datetime(2026, 9, 9, 9, 1), service)
            self._tick(datetime(2026, 9, 9, 9, 2), service)
        self.assertEqual(check.call_count, 1, "one DB check per slot per process, not per minute")

    def test_a_failing_check_fails_open(self):
        service = DigestService()
        with (
            patch.object(service, "_digest_exists_since", side_effect=RuntimeError("db locked")),
            self.assertLogs("backend.ai.digest_service", "WARNING"),
        ):
            self.assertEqual(self._tick(datetime(2026, 9, 9, 9, 0), service), ["premarket"])

    def test_the_database_check_runs_off_the_event_loop(self):
        on_loop = []

        def spy(session, since):
            try:
                asyncio.get_running_loop()
                on_loop.append(True)
            except RuntimeError:
                on_loop.append(False)
            return False

        service = DigestService()
        with patch.object(service, "_digest_exists_since", side_effect=spy):
            self._tick(datetime(2026, 9, 9, 9, 0), service)
        self.assertEqual(on_loop, [False])


class TestDigestServiceFire(unittest.TestCase):
    def test_fire_swallows_generation_failure(self):
        """A broken digest generation must not crash the loop —
        matches every other background-loop's log-and-continue
        philosophy."""
        service = DigestService()
        with patch("backend.ai.digest.generate_and_store_digest", side_effect=RuntimeError("boom")):
            _run(service._fire("close"))
        # No exception propagated — reaching this line is the assertion.


if __name__ == "__main__":
    unittest.main()
