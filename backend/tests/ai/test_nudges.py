"""
Tests for backend.ai.nudges.NudgeService.

Deliberately tests the sync tick methods directly (_check_alert_triggers,
_check_big_moves, _insert_nudge) rather than the asyncio sleep loop —
mirrors test_digest_service.py's approach of testing the firing logic
with no real waiting involved.
"""
import unittest
from unittest.mock import MagicMock, patch

from backend.ai.nudges import NudgeService


def _trigger(id_, symbol="NVDA", alert_name="NVDA breakout", message=None, commentary=None, observed=None):
    t = MagicMock()
    t.id = id_
    t.symbol = symbol
    t.message = message
    t.ai_commentary = commentary
    t.observed_value = observed
    t.alert = MagicMock(name=alert_name)
    t.alert.name = alert_name
    return t


class TestFormatAlertNudge(unittest.TestCase):
    def test_prefers_ai_commentary(self):
        t = _trigger(1, commentary="Broke above the 20-day high on strong volume.")
        text = NudgeService._format_alert_nudge(t)
        self.assertIn("NVDA", text)
        self.assertIn("Broke above the 20-day high", text)

    def test_falls_back_to_message_when_no_commentary(self):
        t = _trigger(1, message="Price crossed above 220")
        text = NudgeService._format_alert_nudge(t)
        self.assertIn("Price crossed above 220", text)

    def test_falls_back_to_observed_value_when_nothing_else(self):
        t = _trigger(1, observed="221.50")
        text = NudgeService._format_alert_nudge(t)
        self.assertIn("221.50", text)


class TestCheckAlertTriggers(unittest.TestCase):
    def setUp(self):
        self.service = NudgeService()

    def test_inserts_nudge_for_new_trigger_and_advances_watermark(self):
        self.service._last_trigger_id = 0
        repo = MagicMock()
        repo.get_recent_triggers.return_value = [_trigger(5, message="fired")]
        with patch("backend.repositories.alert_repository.AlertRepository", return_value=repo), \
             patch.object(self.service, "_insert_nudge") as mock_insert:
            self.service._check_alert_triggers()
        mock_insert.assert_called_once()
        self.assertIn("NVDA", mock_insert.call_args.args[0])
        self.assertEqual(self.service._last_trigger_id, 5)

    def test_skips_triggers_at_or_below_watermark(self):
        self.service._last_trigger_id = 5
        repo = MagicMock()
        repo.get_recent_triggers.return_value = [_trigger(5), _trigger(3)]
        with patch("backend.repositories.alert_repository.AlertRepository", return_value=repo), \
             patch.object(self.service, "_insert_nudge") as mock_insert:
            self.service._check_alert_triggers()
        mock_insert.assert_not_called()
        self.assertEqual(self.service._last_trigger_id, 5)

    def test_multiple_new_triggers_processed_in_id_order(self):
        self.service._last_trigger_id = 0
        repo = MagicMock()
        repo.get_recent_triggers.return_value = [_trigger(7, symbol="TSLA"), _trigger(6, symbol="AAPL")]
        with patch("backend.repositories.alert_repository.AlertRepository", return_value=repo), \
             patch.object(self.service, "_insert_nudge") as mock_insert:
            self.service._check_alert_triggers()
        texts_in_order = [c.args[0] for c in mock_insert.call_args_list]
        self.assertIn("AAPL", texts_in_order[0])
        self.assertIn("TSLA", texts_in_order[1])
        self.assertEqual(self.service._last_trigger_id, 7)

    def test_repo_error_does_not_raise(self):
        with patch("backend.repositories.alert_repository.AlertRepository", side_effect=RuntimeError("db down")):
            self.service._check_alert_triggers()  # must not raise


class TestCheckBigMoves(unittest.TestCase):
    def setUp(self):
        self.service = NudgeService()

    def _run(self, scores: dict, threshold=60.0, cooldown=1800.0):
        result_objs = {}
        for sym, score in scores.items():
            r = MagicMock()
            r.calculate_signed_total_score.return_value = score
            result_objs[sym] = r
        scanner = MagicMock()
        scanner.scan_results = result_objs
        with patch("backend.api.main_helpers._watched_symbols", return_value=list(scores.keys())), \
             patch("backend.scanner.scanner.market_scanner", scanner), \
             patch.object(self.service, "_insert_nudge") as mock_insert:
            self.service._check_big_moves(threshold, cooldown)
        return mock_insert

    def test_no_fire_on_first_observation(self):
        mock_insert = self._run({"NVDA": 75.0})
        mock_insert.assert_not_called()
        self.assertEqual(self.service._last_scores["NVDA"], 75.0)

    def test_fires_on_upward_cross(self):
        self.service._last_scores["NVDA"] = 40.0
        mock_insert = self._run({"NVDA": 65.0})
        mock_insert.assert_called_once()
        self.assertIn("bullish", mock_insert.call_args.args[0])

    def test_fires_on_downward_cross(self):
        self.service._last_scores["NVDA"] = -40.0
        mock_insert = self._run({"NVDA": -65.0})
        mock_insert.assert_called_once()
        self.assertIn("bearish", mock_insert.call_args.args[0])

    def test_no_fire_when_already_above_threshold(self):
        # Already past the line on the prior tick -> no re-fire on staying there.
        self.service._last_scores["NVDA"] = 65.0
        mock_insert = self._run({"NVDA": 70.0})
        mock_insert.assert_not_called()

    def test_no_fire_when_move_stays_below_threshold(self):
        self.service._last_scores["NVDA"] = 10.0
        mock_insert = self._run({"NVDA": 30.0})
        mock_insert.assert_not_called()

    def test_cooldown_blocks_repeat_nudge(self):
        self.service._last_scores["NVDA"] = 40.0
        self._run({"NVDA": 65.0})  # first cross fires and sets cooldown
        self.service._last_scores["NVDA"] = 40.0  # drop and cross again immediately
        mock_insert = self._run({"NVDA": 65.0})
        mock_insert.assert_not_called()


class TestInsertNudge(unittest.TestCase):
    def test_writes_to_universal_session(self):
        repo = MagicMock()
        session = MagicMock(id=1)
        repo.get_or_create_open_session.return_value = session
        with patch("backend.repositories.chat_repository.ChatRepository", return_value=repo):
            NudgeService._insert_nudge("hello")
        repo.get_or_create_open_session.assert_called_once_with(scope="universal")
        repo.add_message.assert_called_once_with(1, "assistant", "hello")

    def test_repo_error_does_not_raise(self):
        with patch("backend.repositories.chat_repository.ChatRepository", side_effect=RuntimeError("db down")):
            NudgeService._insert_nudge("hello")  # must not raise


class TestTick(unittest.TestCase):
    def test_disabled_skips_both_checks(self):
        service = NudgeService()
        with patch("backend.ai.nudges.settings") as mock_settings, \
             patch.object(service, "_check_alert_triggers") as mock_alerts, \
             patch.object(service, "_check_big_moves") as mock_moves:
            mock_settings.ai_nudges.enabled = False
            service._tick()
        mock_alerts.assert_not_called()
        mock_moves.assert_not_called()


if __name__ == "__main__":
    unittest.main()
