"""
Tests for the chat action tools (2026-09-11): create/delete alert,
add/remove watchlist ticker, create/delete watchlist.

Two layers:
  - TestFinalizeParsed / TestActionHandlers exercise _finalize_parsed and
    the individual _*_watchlist / _*_alert handlers directly against a
    real in-memory DB (fast, precise on edge cases — the confirm gate
    especially).
  - TestEndToEnd drives the whole thing through answer_chat_message with
    ai_manager mocked to return the JSON the model would produce, to
    prove the db-threading (ChatRepository -> _generate_reply ->
    _finalize_parsed -> the repos) actually wires up.
"""
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.ai.chat import (
    _add_to_watchlist,
    _confirm_prompt,
    _create_alert,
    _create_watchlist,
    _delete_alert,
    _delete_watchlist,
    _finalize_parsed,
    _remove_from_watchlist,
    _resolve_watchlist,
    _run_action,
    answer_chat_message,
)
from backend.ai.prompt import ChatReplyResponse
from backend.ai.provider import AIResponse
from backend.models import (
    Alert,
    AlertTrigger,
    ChatMessage,
    ChatSession,
    Watchlist,
    WatchlistSymbol,
)
from backend.repositories.alert_repository import AlertRepository
from backend.repositories.watchlist_repository import WatchlistRepository


def _reply(text):
    return AIResponse(text=f"```json\n{text}\n```", provider="ollama", model="llama3.2")


def _parsed(**over) -> ChatReplyResponse:
    base = dict(reply="ok", grounded=True)
    base.update(over)
    return ChatReplyResponse(**base)


class _DBBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
        )
        for model in (
            ChatSession, ChatMessage, Alert, AlertTrigger, Watchlist, WatchlistSymbol,
        ):
            model.__table__.create(self.engine, checkfirst=True)
        Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = Session()
        # _kickoff_backfill reaches into the watchlist router + ingestion
        # service — irrelevant to these tests and does real work, so
        # it's patched everywhere in this file.
        p = patch("backend.ai.chat._kickoff_backfill")
        self.mock_backfill = p.start()
        self.addCleanup(p.stop)


class TestConditionTypeValidation(unittest.TestCase):
    def test_invalid_condition_type_becomes_none(self):
        r = ChatReplyResponse(
            reply="ok", action="create_alert", action_symbol="AAPL",
            action_condition_type="not_a_real_condition", action_parameter="200",
        )
        self.assertIsNone(r.action_condition_type)

    def test_valid_condition_type_kept(self):
        r = ChatReplyResponse(
            reply="ok", action="create_alert", action_symbol="AAPL",
            action_condition_type="price_above", action_parameter="200",
        )
        self.assertEqual(r.action_condition_type, "price_above")


class TestConfirmGate(_DBBase):
    """The safety-critical part: a destructive action never touches the
    DB without action_confirmed=True, no matter what the model set for
    "reply" — _finalize_parsed enforces this in Python."""

    def test_delete_alert_without_confirmation_does_not_delete(self):
        alert = AlertRepository(self.db).create("A", "AAPL", "price_above", "200")
        parsed = _parsed(action="delete_alert", action_target_id=alert.id, action_confirmed=False)

        text, grounded = _finalize_parsed(self.db, parsed, [])

        self.assertIn("confirm", text.lower())
        self.assertTrue(grounded)
        self.assertIsNotNone(AlertRepository(self.db).get_by_id(alert.id))  # still there

    def test_delete_alert_with_confirmation_deletes(self):
        alert = AlertRepository(self.db).create("A", "AAPL", "price_above", "200")
        parsed = _parsed(action="delete_alert", action_target_id=alert.id, action_confirmed=True)

        text, grounded = _finalize_parsed(self.db, parsed, [])

        self.assertIn("deleted", text.lower())
        self.assertTrue(grounded)
        self.assertIsNone(AlertRepository(self.db).get_by_id(alert.id))

    def test_remove_from_watchlist_without_confirmation_does_not_remove(self):
        wl = WatchlistRepository(self.db).create_watchlist("Watch1")
        WatchlistRepository(self.db).add_symbol_to_watchlist(wl.id, "AAPL")
        parsed = _parsed(
            action="remove_from_watchlist", action_symbol="AAPL", action_confirmed=False,
        )

        text, grounded = _finalize_parsed(self.db, parsed, [])

        self.assertIn("confirm", text.lower())
        self.assertIsNotNone(WatchlistRepository(self.db).get_watchlist_symbol(wl.id, "AAPL"))

    def test_remove_from_watchlist_with_confirmation_removes(self):
        wl = WatchlistRepository(self.db).create_watchlist("Watch1")
        WatchlistRepository(self.db).add_symbol_to_watchlist(wl.id, "AAPL")
        parsed = _parsed(
            action="remove_from_watchlist", action_symbol="AAPL", action_confirmed=True,
        )

        text, grounded = _finalize_parsed(self.db, parsed, [])

        self.assertIn("removed", text.lower())
        self.assertIsNone(WatchlistRepository(self.db).get_watchlist_symbol(wl.id, "AAPL"))

    def test_delete_watchlist_without_confirmation_does_not_delete(self):
        wl = WatchlistRepository(self.db).create_watchlist("Swing Setups")
        parsed = _parsed(
            action="delete_watchlist", action_watchlist="Swing Setups", action_confirmed=False,
        )

        text, grounded = _finalize_parsed(self.db, parsed, [])

        self.assertIn("confirm", text.lower())
        self.assertIsNotNone(WatchlistRepository(self.db).get_watchlist(wl.id))

    def test_delete_watchlist_with_confirmation_deletes(self):
        wl = WatchlistRepository(self.db).create_watchlist("Swing Setups")
        parsed = _parsed(
            action="delete_watchlist", action_watchlist="Swing Setups", action_confirmed=True,
        )

        text, grounded = _finalize_parsed(self.db, parsed, [])

        self.assertIn("deleted", text.lower())
        self.assertIsNone(WatchlistRepository(self.db).get_watchlist(wl.id))

    def test_additive_actions_ignore_action_confirmed(self):
        # create_alert / add_to_watchlist / create_watchlist fire
        # regardless of action_confirmed — it's only meaningful for the
        # destructive set.
        parsed = _parsed(
            action="create_alert", action_symbol="AAPL",
            action_condition_type="price_above", action_parameter="200",
            action_confirmed=False,
        )
        text, grounded = _finalize_parsed(self.db, parsed, [])
        self.assertIn("done", text.lower())
        self.assertEqual(len(AlertRepository(self.db).get_all()), 1)


class TestActionHandlers(_DBBase):
    def test_create_alert(self):
        text, grounded = _create_alert(self.db, _parsed(
            action_symbol="AAPL", action_condition_type="price_above", action_parameter="200",
        ))
        self.assertTrue(grounded)
        alerts = AlertRepository(self.db).get_all()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].symbol, "AAPL")
        self.assertEqual(alerts[0].condition_type, "price_above")
        self.assertEqual(alerts[0].parameter, "200")

    def test_create_alert_uses_custom_label(self):
        text, _ = _create_alert(self.db, _parsed(
            action_symbol="AAPL", action_condition_type="price_above",
            action_parameter="200", action_label="Breakout watch",
        ))
        self.assertEqual(AlertRepository(self.db).get_all()[0].name, "Breakout watch")

    def test_create_alert_missing_fields_asks_not_creates(self):
        text, grounded = _create_alert(self.db, _parsed(action_symbol="AAPL"))
        self.assertFalse(grounded)
        self.assertEqual(AlertRepository(self.db).get_all(), [])

    def test_delete_alert_not_found(self):
        text, grounded = _delete_alert(self.db, _parsed(action_target_id=999))
        self.assertFalse(grounded)
        self.assertIn("doesn't exist", text.lower())

    def test_add_to_watchlist_creates_default_list_when_none_exists(self):
        text, grounded = _add_to_watchlist(self.db, _parsed(action_symbol="RIVN"))
        self.assertTrue(grounded)
        lists = WatchlistRepository(self.db).get_watchlists()
        self.assertEqual(len(lists), 1)
        self.assertIsNotNone(WatchlistRepository(self.db).get_watchlist_symbol(lists[0].id, "RIVN"))
        self.mock_backfill.assert_called_once_with("RIVN")

    def test_add_to_watchlist_reuses_single_existing_list(self):
        wl = WatchlistRepository(self.db).create_watchlist("Watch1")
        text, _ = _add_to_watchlist(self.db, _parsed(action_symbol="RIVN"))
        self.assertIsNotNone(WatchlistRepository(self.db).get_watchlist_symbol(wl.id, "RIVN"))

    def test_add_to_watchlist_by_name(self):
        WatchlistRepository(self.db).create_watchlist("Watch1")
        wl2 = WatchlistRepository(self.db).create_watchlist("Swing Setups")
        _add_to_watchlist(self.db, _parsed(action_symbol="RIVN", action_watchlist="Swing Setups"))
        self.assertIsNotNone(WatchlistRepository(self.db).get_watchlist_symbol(wl2.id, "RIVN"))

    def test_add_to_watchlist_ambiguous_when_unnamed_and_multiple_lists(self):
        WatchlistRepository(self.db).create_watchlist("Watch1")
        WatchlistRepository(self.db).create_watchlist("Swing Setups")
        text, grounded = _add_to_watchlist(self.db, _parsed(action_symbol="RIVN"))
        self.assertIn("more than one", text.lower())
        self.mock_backfill.assert_not_called()

    def test_add_to_watchlist_existing_symbol_skips_backfill(self):
        wl = WatchlistRepository(self.db).create_watchlist("Watch1")
        WatchlistRepository(self.db).add_symbol_to_watchlist(wl.id, "AAPL")
        self.mock_backfill.reset_mock()
        _add_to_watchlist(self.db, _parsed(action_symbol="AAPL"))
        self.mock_backfill.assert_not_called()

    def test_remove_from_watchlist_not_present(self):
        WatchlistRepository(self.db).create_watchlist("Watch1")
        text, grounded = _remove_from_watchlist(self.db, _parsed(action_symbol="ZZZZ"))
        self.assertFalse(grounded)
        self.assertIn("wasn't in", text)

    def test_create_watchlist_with_initial_symbol(self):
        text, grounded = _create_watchlist(self.db, _parsed(
            action_watchlist="Swing Setups", action_symbol="TSLA",
        ))
        self.assertTrue(grounded)
        wl = WatchlistRepository(self.db).get_watchlist_by_name("Swing Setups")
        self.assertIsNotNone(wl)
        self.assertIsNotNone(WatchlistRepository(self.db).get_watchlist_symbol(wl.id, "TSLA"))

    def test_create_watchlist_duplicate_name_rejected(self):
        WatchlistRepository(self.db).create_watchlist("Swing Setups")
        text, grounded = _create_watchlist(self.db, _parsed(action_watchlist="Swing Setups"))
        self.assertFalse(grounded)
        self.assertIn("already exists", text)

    def test_delete_watchlist_ambiguous(self):
        WatchlistRepository(self.db).create_watchlist("Watch1")
        WatchlistRepository(self.db).create_watchlist("Swing Setups")
        text, grounded = _delete_watchlist(self.db, _parsed())
        self.assertIn("more than one", text.lower())

    def test_delete_watchlist_not_found(self):
        text, grounded = _delete_watchlist(self.db, _parsed(action_watchlist="Nope"))
        self.assertFalse(grounded)
        self.assertIn("couldn't find", text.lower())


class TestResolveWatchlist(_DBBase):
    def test_by_name(self):
        wl = WatchlistRepository(self.db).create_watchlist("Swing Setups")
        found, ambiguous = _resolve_watchlist(self.db, "Swing Setups")
        self.assertEqual(found.id, wl.id)
        self.assertFalse(ambiguous)

    def test_none_given_single_list(self):
        wl = WatchlistRepository(self.db).create_watchlist("Watch1")
        found, ambiguous = _resolve_watchlist(self.db, None)
        self.assertEqual(found.id, wl.id)
        self.assertFalse(ambiguous)

    def test_none_given_no_lists(self):
        found, ambiguous = _resolve_watchlist(self.db, None)
        self.assertIsNone(found)
        self.assertFalse(ambiguous)

    def test_none_given_multiple_lists_is_ambiguous(self):
        WatchlistRepository(self.db).create_watchlist("Watch1")
        WatchlistRepository(self.db).create_watchlist("Swing Setups")
        found, ambiguous = _resolve_watchlist(self.db, None)
        self.assertIsNone(found)
        self.assertTrue(ambiguous)


class TestRunActionNeverRaises(_DBBase):
    @patch("backend.ai.chat._ACTION_HANDLERS", {
        "create_alert": MagicMock(side_effect=RuntimeError("boom")),
    })
    def test_handler_exception_degrades_gracefully(self):
        text, grounded = _run_action(self.db, _parsed(action="create_alert"))
        self.assertFalse(grounded)
        self.assertIn("went wrong", text.lower())


class TestConfirmPromptWording(unittest.TestCase):
    def test_delete_alert_wording(self):
        self.assertIn("confirm", _confirm_prompt(_parsed(action="delete_alert")).lower())

    def test_remove_from_watchlist_wording_includes_symbol_and_list(self):
        text = _confirm_prompt(_parsed(
            action="remove_from_watchlist", action_symbol="RIVN", action_watchlist="Swing Setups",
        ))
        self.assertIn("RIVN", text)
        self.assertIn("Swing Setups", text)

    def test_delete_watchlist_wording_includes_name(self):
        text = _confirm_prompt(_parsed(action="delete_watchlist", action_watchlist="Swing Setups"))
        self.assertIn("Swing Setups", text)


class TestEndToEnd(unittest.TestCase):
    """Through answer_chat_message -> _generate_reply -> _finalize_parsed
    -> the real repos, proving the db gets threaded all the way down."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
        )
        for model in (
            ChatSession, ChatMessage, Alert, AlertTrigger, Watchlist, WatchlistSymbol,
        ):
            model.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        p = patch("backend.repositories.chat_repository.SessionLocal", self.Session)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch("backend.ai.chat.resolve_turn_symbols", return_value=([], False))
        p2.start()
        self.addCleanup(p2.stop)
        p3 = patch("backend.ai.chat.build_market_baseline", return_value={})
        p3.start()
        self.addCleanup(p3.stop)
        p4 = patch("backend.ai.chat._kickoff_backfill")
        self.mock_backfill = p4.start()
        self.addCleanup(p4.stop)

        db = self.Session()
        session = ChatSession(symbol="*", scope="universal")
        db.add(session)
        db.commit()
        db.refresh(session)
        self.session_id = session.id
        db.close()

    @patch("backend.ai.chat.ai_manager")
    def test_add_to_watchlist_end_to_end(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply(
            '{"reply": "On it.", "grounded": true, "action": "add_to_watchlist", '
            '"action_symbol": "RIVN"}'
        )

        msg, grounded, *_ = answer_chat_message(self.session_id, "add RIVN to my watchlist")

        self.assertTrue(grounded)
        self.assertIn("RIVN", msg.content)
        db = self.Session()
        try:
            wl = WatchlistRepository(db).get_watchlists()[0]
            self.assertIsNotNone(WatchlistRepository(db).get_watchlist_symbol(wl.id, "RIVN"))
        finally:
            db.close()

    @patch("backend.ai.chat.ai_manager")
    def test_delete_alert_end_to_end_confirm_then_execute(self, mock_ai):
        db = self.Session()
        alert = AlertRepository(db).create("A", "NVDA", "price_above", "220")
        alert_id = alert.id
        db.close()

        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply(
            '{"reply": "Delete the NVDA alert?", "grounded": true, '
            '"action": "delete_alert", "action_target_id": ' + str(alert_id) + '}'
        )

        msg1, grounded1, *_ = answer_chat_message(self.session_id, "delete my nvda alert")
        self.assertIn("confirm", msg1.content.lower())
        db = self.Session()
        try:
            self.assertIsNotNone(AlertRepository(db).get_by_id(alert_id))
        finally:
            db.close()

        mock_ai.complete.return_value = _reply(
            '{"reply": "Done.", "grounded": true, "action": "delete_alert", '
            '"action_target_id": ' + str(alert_id) + ', "action_confirmed": true}'
        )
        msg2, grounded2, *_ = answer_chat_message(self.session_id, "yes")
        self.assertIn("deleted", msg2.content.lower())
        db = self.Session()
        try:
            self.assertIsNone(AlertRepository(db).get_by_id(alert_id))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
