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
    _WATCHLIST_LIST_INTENT,
    _add_to_watchlist,
    _confirm_prompt,
    _create_alert,
    _create_watchlist,
    _delete_alert,
    _delete_watchlist,
    _fallback_action,
    _fallback_confirmation,
    _finalize_parsed,
    _remove_from_watchlist,
    _resolve_watchlist,
    _run_action,
    _run_backtest,
    _set_entity_type,
    _watchlist_list_reply,
    answer_chat_message,
)
from backend.ai.manager import ai_manager
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

    def test_remove_from_watchlist_not_present_anywhere(self):
        WatchlistRepository(self.db).create_watchlist("Watch1")
        text, grounded = _remove_from_watchlist(self.db, _parsed(action_symbol="ZZZZ"))
        self.assertFalse(grounded)
        self.assertIn("isn't on any of your watchlists", text)

    def test_remove_from_watchlist_named_but_not_present_there(self):
        repo = WatchlistRepository(self.db)
        repo.create_watchlist("Watch1")
        text, grounded = _remove_from_watchlist(
            self.db, _parsed(action_symbol="ZZZZ", action_watchlist="Watch1"),
        )
        self.assertFalse(grounded)
        self.assertIn("wasn't in", text)

    def test_remove_from_watchlist_unnamed_but_named_list_missing(self):
        text, grounded = _remove_from_watchlist(
            self.db, _parsed(action_symbol="AAPL", action_watchlist="Nope"),
        )
        self.assertFalse(grounded)
        self.assertIn("couldn't find", text.lower())

    def test_remove_from_watchlist_resolves_the_one_list_that_has_it(self):
        # RIVN is only on Tech, even though a 2nd unrelated list exists —
        # this must NOT ask "which watchlist", it's unambiguous by
        # membership.
        repo = WatchlistRepository(self.db)
        tech = repo.create_watchlist("Tech")
        repo.create_watchlist("Swing Setups")
        repo.add_symbol_to_watchlist(tech.id, "RIVN")

        text, grounded = _remove_from_watchlist(self.db, _parsed(action_symbol="RIVN"))

        self.assertTrue(grounded)
        self.assertIn("removed RIVN from Tech", text)
        self.assertIsNone(repo.get_watchlist_symbol(tech.id, "RIVN"))

    def test_remove_from_watchlist_on_two_lists_asks_which_one(self):
        repo = WatchlistRepository(self.db)
        tech = repo.create_watchlist("Tech")
        swing = repo.create_watchlist("Swing Setups")
        repo.add_symbol_to_watchlist(tech.id, "RIVN")
        repo.add_symbol_to_watchlist(swing.id, "RIVN")

        text, grounded = _remove_from_watchlist(self.db, _parsed(action_symbol="RIVN"))

        self.assertTrue(grounded)
        self.assertIn("Tech", text)
        self.assertIn("Swing Setups", text)
        # neither list touched — it asked instead of guessing
        self.assertIsNotNone(repo.get_watchlist_symbol(tech.id, "RIVN"))
        self.assertIsNotNone(repo.get_watchlist_symbol(swing.id, "RIVN"))

    def test_remove_from_watchlist_on_two_lists_named_one_resolves_it(self):
        repo = WatchlistRepository(self.db)
        tech = repo.create_watchlist("Tech")
        swing = repo.create_watchlist("Swing Setups")
        repo.add_symbol_to_watchlist(tech.id, "RIVN")
        repo.add_symbol_to_watchlist(swing.id, "RIVN")

        text, grounded = _remove_from_watchlist(
            self.db, _parsed(action_symbol="RIVN", action_watchlist="Swing Setups"),
        )

        self.assertTrue(grounded)
        self.assertIn("removed RIVN from Swing Setups", text)
        self.assertIsNotNone(repo.get_watchlist_symbol(tech.id, "RIVN"))  # untouched
        self.assertIsNone(repo.get_watchlist_symbol(swing.id, "RIVN"))

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


class TestSetEntityType(_DBBase):
    def test_relabels_stock_to_etf(self):
        repo = WatchlistRepository(self.db)
        wl = repo.create_watchlist("Default")
        repo.add_symbol_to_watchlist(wl.id, "SPY")

        text, grounded = _set_entity_type(
            self.db, _parsed(action_symbol="SPY", action_entity_type="etf"),
        )

        self.assertTrue(grounded)
        self.assertIn("SPY", text)
        self.assertIn("ETF", text)
        sym = repo.get_watchlist_symbol(wl.id, "SPY")
        self.assertEqual(sym.entity_type, "etf")

    def test_relabels_etf_to_stock(self):
        repo = WatchlistRepository(self.db)
        wl = repo.create_watchlist("Default")
        repo.add_symbol_to_watchlist(wl.id, "SPY")
        repo.update_symbol_in_watchlist(wl.id, "SPY", entity_type="etf")

        text, grounded = _set_entity_type(
            self.db, _parsed(action_symbol="SPY", action_entity_type="stock"),
        )

        self.assertTrue(grounded)
        self.assertEqual(repo.get_watchlist_symbol(wl.id, "SPY").entity_type, "stock")

    def test_missing_entity_type_asks_not_writes(self):
        repo = WatchlistRepository(self.db)
        wl = repo.create_watchlist("Default")
        repo.add_symbol_to_watchlist(wl.id, "SPY")

        text, grounded = _set_entity_type(self.db, _parsed(action_symbol="SPY"))

        self.assertFalse(grounded)
        self.assertIsNone(repo.get_watchlist_symbol(wl.id, "SPY").entity_type)

    def test_symbol_not_on_any_watchlist(self):
        text, grounded = _set_entity_type(
            self.db, _parsed(action_symbol="ZZZZ", action_entity_type="etf"),
        )
        self.assertFalse(grounded)
        self.assertIn("isn't on any of your watchlists", text)

    def test_ambiguous_when_on_two_lists(self):
        repo = WatchlistRepository(self.db)
        tech = repo.create_watchlist("Tech")
        swing = repo.create_watchlist("Swing Setups")
        repo.add_symbol_to_watchlist(tech.id, "SPY")
        repo.add_symbol_to_watchlist(swing.id, "SPY")

        text, grounded = _set_entity_type(
            self.db, _parsed(action_symbol="SPY", action_entity_type="etf"),
        )

        self.assertTrue(grounded)
        self.assertIn("Tech", text)
        self.assertIn("Swing Setups", text)
        self.assertIsNone(repo.get_watchlist_symbol(tech.id, "SPY").entity_type)
        self.assertIsNone(repo.get_watchlist_symbol(swing.id, "SPY").entity_type)

    def test_named_watchlist_resolves_the_ambiguity(self):
        repo = WatchlistRepository(self.db)
        tech = repo.create_watchlist("Tech")
        swing = repo.create_watchlist("Swing Setups")
        repo.add_symbol_to_watchlist(tech.id, "SPY")
        repo.add_symbol_to_watchlist(swing.id, "SPY")

        text, grounded = _set_entity_type(self.db, _parsed(
            action_symbol="SPY", action_entity_type="etf", action_watchlist="Tech",
        ))

        self.assertTrue(grounded)
        self.assertEqual(repo.get_watchlist_symbol(tech.id, "SPY").entity_type, "etf")
        self.assertIsNone(repo.get_watchlist_symbol(swing.id, "SPY").entity_type)

    def test_not_destructive_not_gated_by_confirmation(self):
        repo = WatchlistRepository(self.db)
        wl = repo.create_watchlist("Default")
        repo.add_symbol_to_watchlist(wl.id, "SPY")

        text, grounded = _finalize_parsed(
            self.db,
            _parsed(
                action="set_entity_type", action_symbol="SPY", action_entity_type="etf",
                action_confirmed=False,
            ),
            symbol_blocks=[],
        )

        self.assertTrue(grounded)
        self.assertEqual(repo.get_watchlist_symbol(wl.id, "SPY").entity_type, "etf")


class TestResolveWatchlist(_DBBase):
    def test_by_name(self):
        wl = WatchlistRepository(self.db).create_watchlist("Swing Setups")
        found, ambiguous, candidates = _resolve_watchlist(self.db, "Swing Setups")
        self.assertEqual(found.id, wl.id)
        self.assertFalse(ambiguous)
        self.assertEqual(candidates, [])

    def test_none_given_single_list(self):
        wl = WatchlistRepository(self.db).create_watchlist("Watch1")
        found, ambiguous, _ = _resolve_watchlist(self.db, None)
        self.assertEqual(found.id, wl.id)
        self.assertFalse(ambiguous)

    def test_none_given_no_lists(self):
        found, ambiguous, candidates = _resolve_watchlist(self.db, None)
        self.assertIsNone(found)
        self.assertFalse(ambiguous)
        self.assertEqual(candidates, [])

    def test_none_given_multiple_lists_is_ambiguous(self):
        WatchlistRepository(self.db).create_watchlist("Watch1")
        WatchlistRepository(self.db).create_watchlist("Swing Setups")
        found, ambiguous, candidates = _resolve_watchlist(self.db, None)
        self.assertIsNone(found)
        self.assertTrue(ambiguous)
        self.assertEqual({c.name for c in candidates}, {"Watch1", "Swing Setups"})

    def test_containing_symbol_resolves_the_one_list_that_has_it(self):
        repo = WatchlistRepository(self.db)
        tech = repo.create_watchlist("Tech")
        repo.create_watchlist("Swing Setups")  # a 2nd list that does NOT have RIVN
        repo.add_symbol_to_watchlist(tech.id, "RIVN")

        found, ambiguous, candidates = _resolve_watchlist(self.db, None, containing_symbol="RIVN")

        self.assertEqual(found.id, tech.id)
        self.assertFalse(ambiguous)
        self.assertEqual(candidates, [])

    def test_containing_symbol_on_two_lists_is_ambiguous(self):
        repo = WatchlistRepository(self.db)
        tech = repo.create_watchlist("Tech")
        swing = repo.create_watchlist("Swing Setups")
        repo.add_symbol_to_watchlist(tech.id, "RIVN")
        repo.add_symbol_to_watchlist(swing.id, "RIVN")

        found, ambiguous, candidates = _resolve_watchlist(self.db, None, containing_symbol="RIVN")

        self.assertIsNone(found)
        self.assertTrue(ambiguous)
        self.assertEqual({c.name for c in candidates}, {"Tech", "Swing Setups"})

    def test_containing_symbol_not_on_any_list_is_not_ambiguous(self):
        repo = WatchlistRepository(self.db)
        repo.create_watchlist("Tech")
        repo.create_watchlist("Swing Setups")

        found, ambiguous, candidates = _resolve_watchlist(self.db, None, containing_symbol="RIVN")

        self.assertIsNone(found)
        self.assertFalse(ambiguous)
        self.assertEqual(candidates, [])


class TestRunBacktest(_DBBase):
    """run_backtest — a real, non-destructive read: fixed 6-month/daily
    window, its own rate-limit bucket, gated on its own settings flag."""

    def _enabled(self):
        return patch.object(ai_manager.settings, "backtest_tool_enabled", True)

    def test_no_symbol(self):
        with self._enabled():
            text, grounded = _run_backtest(self.db, _parsed())
        self.assertFalse(grounded)
        self.assertIn("which ticker", text.lower())

    def test_disabled_flag_degrades_without_running(self):
        with patch.object(ai_manager.settings, "backtest_tool_enabled", False), \
             patch("backend.backtesting.engine.backtest_engine") as engine:
            text, grounded = _run_backtest(self.db, _parsed(action_symbol="AAPL"))
        self.assertFalse(grounded)
        self.assertIn("isn't enabled", text)
        engine.run.assert_not_called()

    def test_rate_limited_degrades(self):
        with self._enabled(), \
             patch("backend.api.rate_limit._backtest_limiter") as limiter, \
             patch("backend.backtesting.engine.backtest_engine") as engine:
            limiter.is_allowed.return_value = (False, 42)
            text, grounded = _run_backtest(self.db, _parsed(action_symbol="AAPL"))
        self.assertFalse(grounded)
        self.assertIn("rate-limited", text)
        self.assertIn("42", text)
        engine.run.assert_not_called()

    def test_insufficient_data_degrades(self):
        run = MagicMock(status="completed", total_signals=0)
        with self._enabled(), \
             patch("backend.api.rate_limit._backtest_limiter") as limiter, \
             patch("backend.backtesting.engine.backtest_engine") as engine, \
             patch("backend.repositories.backtest_repository.BacktestRepository") as repo_cls:
            limiter.is_allowed.return_value = (True, 0)
            engine.run.return_value = 1
            repo_cls.return_value.get_run.return_value = run
            text, grounded = _run_backtest(self.db, _parsed(action_symbol="ZZZZ"))
        self.assertFalse(grounded)
        self.assertIn("Not enough historical data", text)

    def test_happy_path_reports_real_numbers(self):
        run = MagicMock(
            status="completed", total_signals=42, signals_requested="RSI_OVERSOLD,MACD_BULLISH",
            win_rate_1d=0.62, avg_return_1d=0.012, avg_return_5d=0.034,
        )
        with self._enabled(), \
             patch("backend.api.rate_limit._backtest_limiter") as limiter, \
             patch("backend.backtesting.engine.backtest_engine") as engine, \
             patch("backend.repositories.backtest_repository.BacktestRepository") as repo_cls:
            limiter.is_allowed.return_value = (True, 0)
            engine.run.return_value = 7
            repo_cls.return_value.get_run.return_value = run
            text, grounded = _run_backtest(self.db, _parsed(action_symbol="AAPL"))
        self.assertTrue(grounded)
        self.assertIn("42 times", text)
        self.assertIn("62% win rate", text)
        engine.run.assert_called_once()
        repo_cls.return_value.get_run.assert_called_once_with(7)

    def test_finalize_parsed_never_asks_for_confirmation(self):
        # run_backtest is not in _DESTRUCTIVE_ACTIONS — it fires on the
        # first mention, like create_alert.
        run = MagicMock(
            status="completed", total_signals=5, signals_requested="RSI_OVERSOLD",
            win_rate_1d=0.4, avg_return_1d=-0.01, avg_return_5d=0.0,
        )
        with self._enabled(), \
             patch("backend.api.rate_limit._backtest_limiter") as limiter, \
             patch("backend.backtesting.engine.backtest_engine") as engine, \
             patch("backend.repositories.backtest_repository.BacktestRepository") as repo_cls:
            limiter.is_allowed.return_value = (True, 0)
            engine.run.return_value = 3
            repo_cls.return_value.get_run.return_value = run
            text, grounded = _finalize_parsed(
                self.db, _parsed(action="run_backtest", action_symbol="AAPL"), [],
            )
        self.assertNotIn("confirm", text.lower())
        engine.run.assert_called_once()


class TestRunActionNeverRaises(_DBBase):
    @patch("backend.ai.chat._ACTION_HANDLERS", {
        "create_alert": MagicMock(side_effect=RuntimeError("boom")),
    })
    def test_handler_exception_degrades_gracefully(self):
        text, grounded = _run_action(self.db, _parsed(action="create_alert"))
        self.assertFalse(grounded)
        self.assertIn("went wrong", text.lower())


class TestConfirmPromptWording(_DBBase):
    def test_delete_alert_wording(self):
        self.assertIn(
            "confirm", _confirm_prompt(self.db, _parsed(action="delete_alert")).lower(),
        )

    def test_remove_from_watchlist_wording_includes_symbol_and_list(self):
        text = _confirm_prompt(self.db, _parsed(
            action="remove_from_watchlist", action_symbol="RIVN", action_watchlist="Swing Setups",
        ))
        self.assertIn("RIVN", text)
        self.assertIn("Swing Setups", text)

    def test_delete_watchlist_wording_includes_name(self):
        text = _confirm_prompt(
            self.db, _parsed(action="delete_watchlist", action_watchlist="Swing Setups"),
        )
        self.assertIn("Swing Setups", text)

    def test_delete_watchlist_no_name_resolves_the_single_watchlist(self):
        # The model is never told the trader's watchlist count/names, so it
        # can leave action_watchlist unset even when there's exactly one —
        # the confirmation question must still name it correctly, not fall
        # back to a vague "that watchlist" and not ask "which one" when
        # there's nothing to disambiguate.
        from backend.repositories.watchlist_repository import WatchlistRepository
        WatchlistRepository(self.db).create_watchlist("My Longs")
        text = _confirm_prompt(self.db, _parsed(action="delete_watchlist", action_watchlist=None))
        self.assertIn("My Longs", text)
        self.assertNotIn("which one", text.lower())

    def test_delete_watchlist_no_name_with_multiple_asks_with_real_names(self):
        from backend.repositories.watchlist_repository import WatchlistRepository
        repo = WatchlistRepository(self.db)
        repo.create_watchlist("My Longs")
        repo.create_watchlist("Swing Setups")
        text = _confirm_prompt(self.db, _parsed(action="delete_watchlist", action_watchlist=None))
        self.assertIn("My Longs", text)
        self.assertIn("Swing Setups", text)
        self.assertIn("which one", text.lower())


class TestFallbackAction(unittest.TestCase):
    """_fallback_action: the deterministic safety net for a destructive
    request the model left untagged (action="none"). Confirmed live
    2026-09-11 the model does this even after prompt fixes."""

    def test_delete_watchlist_phrasing_with_no_symbol_matches(self):
        for phrase in (
            "delete my watchlist", "please remove my watchlist",
            "can you clear my watchlist", "trash my watch list",
        ):
            self.assertEqual(_fallback_action(phrase, []), "delete_watchlist", phrase)

    def test_resolved_symbol_this_turn_suppresses_the_fallback(self):
        # "remove AAPL from my watchlist" also matches the phrase, but a
        # resolved ticker means remove_from_watchlist, not delete_watchlist
        # — the fallback must not misfire and nuke the whole list.
        blocks = [{"symbol": "AAPL"}]
        self.assertIsNone(_fallback_action("remove AAPL from my watchlist", blocks))

    def test_unrelated_text_does_not_match(self):
        self.assertIsNone(_fallback_action("what's the market doing today", []))
        self.assertIsNone(_fallback_action("add AAPL to my watchlist", []))


class TestFallbackConfirmation(unittest.TestCase):
    """_fallback_confirmation: the safety net for the turn AFTER the
    app's own confirmation question — confirmed live 2026-09-11 the
    model can narrate a fake "done" here too instead of re-proposing
    the action with action_confirmed=true."""

    def test_yes_after_delete_watchlist_confirm_resolves_name(self):
        transcript = [("assistant", 'Delete the watchlist "Watch1"? This removes every ticker in it — say yes to confirm.')]
        self.assertEqual(
            _fallback_confirmation("yes", transcript),
            ("delete_watchlist", None, "Watch1"),
        )

    def test_yes_after_remove_from_watchlist_confirm_resolves_symbol_and_list(self):
        transcript = [("assistant", 'Remove AAPL from "Watch1"? Say yes to confirm.')]
        self.assertEqual(
            _fallback_confirmation("yes", transcript),
            ("remove_from_watchlist", "AAPL", "Watch1"),
        )

    def test_non_affirmative_reply_does_not_match(self):
        transcript = [("assistant", 'Delete the watchlist "Watch1"? This removes every ticker in it — say yes to confirm.')]
        self.assertIsNone(_fallback_confirmation("actually never mind", transcript))

    def test_prior_non_confirmation_message_does_not_match(self):
        transcript = [("assistant", "Here's what's happening with AAPL today.")]
        self.assertIsNone(_fallback_confirmation("yes", transcript))

    def test_empty_transcript_does_not_match(self):
        self.assertIsNone(_fallback_confirmation("yes", []))


class TestWatchlistListReply(_DBBase):
    """_watchlist_list_reply / _WATCHLIST_LIST_INTENT: a deterministic,
    real answer to "how many/which watchlists do I have" — the model
    is never given this data, so left to itself it either guesses or
    (confirmed live 2026-09-11) declines outright."""

    def test_intent_matches_reported_phrasing(self):
        for phrase in (
            "how many watchlist i have", "how many watchlists do i have",
            "what watchlists do I have", "which watchlists do I have",
            "list my watchlists",
        ):
            self.assertTrue(_WATCHLIST_LIST_INTENT.search(phrase), phrase)

    def test_intent_does_not_match_unrelated_watchlist_mentions(self):
        for phrase in ("delete my watchlist", "add AAPL to my watchlist"):
            self.assertFalse(_WATCHLIST_LIST_INTENT.search(phrase), phrase)

    def test_no_watchlists(self):
        self.assertIn("don't have any", _watchlist_list_reply(self.db))

    def test_single_watchlist_names_real_symbols(self):
        repo = WatchlistRepository(self.db)
        wl = repo.create_watchlist("Watch1")
        repo.add_symbol_to_watchlist(wl.id, "AAPL")
        repo.add_symbol_to_watchlist(wl.id, "NVDA")
        text = _watchlist_list_reply(self.db)
        self.assertIn("1 watchlist", text)
        self.assertIn("Watch1", text)
        self.assertIn("AAPL", text)
        self.assertIn("NVDA", text)

    def test_multiple_watchlists_all_named(self):
        repo = WatchlistRepository(self.db)
        repo.create_watchlist("Longs")
        repo.create_watchlist("Swing Setups")
        text = _watchlist_list_reply(self.db)
        self.assertIn("2 watchlists", text)
        self.assertIn("Longs", text)
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

    @patch("backend.ai.chat.ai_manager")
    def test_delete_watchlist_survives_model_never_setting_action(self, mock_ai):
        """Regression test for the exact bug reported live 2026-09-11: the
        model left action="none" on BOTH the initial "delete my
        watchlist" turn and the "yes" confirmation turn, narrating fake
        prose ("Which watchlist...", "...has been deleted") each time
        while the watchlist was never actually touched. The
        _fallback_action / _fallback_confirmation safety nets must catch
        both turns regardless of what the model returns.
        """
        db = self.Session()
        repo = WatchlistRepository(db)
        wl = repo.create_watchlist("Watch1")
        wl_id = wl.id
        repo.add_symbol_to_watchlist(wl_id, "AAPL")
        db.close()

        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply(
            '{"reply": "Which watchlist would you like to delete?", "grounded": false, '
            '"action": "none"}'
        )
        msg1, grounded1, *_ = answer_chat_message(self.session_id, "delete my watchlist")
        self.assertIn("Watch1", msg1.content)
        self.assertIn("confirm", msg1.content.lower())
        db = self.Session()
        try:
            self.assertIsNotNone(WatchlistRepository(db).get_watchlist(wl_id))
        finally:
            db.close()

        mock_ai.complete.return_value = _reply(
            '{"reply": "The watchlist has been deleted.", "grounded": false, "action": "none"}'
        )
        msg2, grounded2, *_ = answer_chat_message(self.session_id, "yes")
        self.assertIn("deleted", msg2.content.lower())
        self.assertIn("Watch1", msg2.content)
        db = self.Session()
        try:
            self.assertIsNone(WatchlistRepository(db).get_watchlist(wl_id))
        finally:
            db.close()

    @patch("backend.ai.chat.ai_manager")
    def test_how_many_watchlists_answered_without_asking_the_model(self, mock_ai):
        db = self.Session()
        repo = WatchlistRepository(db)
        wl = repo.create_watchlist("Watch1")
        repo.add_symbol_to_watchlist(wl.id, "AAPL")
        db.close()

        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000

        msg, grounded, *_ = answer_chat_message(self.session_id, "how many watchlist i have")
        self.assertIn("Watch1", msg.content)
        self.assertIn("AAPL", msg.content)
        mock_ai.complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
