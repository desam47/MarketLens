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
