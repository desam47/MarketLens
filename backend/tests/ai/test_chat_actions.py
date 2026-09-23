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

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.ai.chat import (
    _MAX_CHAIN_STEPS,
    _WATCHLIST_CONTENTS_INTENT,
    _WATCHLIST_LIST_INTENT,
    _action_step_detail,
    _add_to_watchlist,
    _confirm_prompt,
    _create_alert,
    _create_watchlist,
    _delete_alert,
    _delete_watchlist,
    _fallback_action,
    _fallback_confirmation,
    _finalize_parsed,
    _modify_alert,
    _remove_from_watchlist,
    _resolve_watchlist,
    _run_action,
    _run_backtest,
    _run_screen,
    _run_turn_actions,
    _set_entity_type,
    _watchlist_contents_reply,
    _watchlist_list_reply,
    answer_chat_message,
)
from backend.ai.manager import ai_manager
from backend.ai.prompt import ChatReplyResponse
from backend.ai.provider import AIResponse
from backend.config.settings import settings
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
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        for model in (
            ChatSession,
            ChatMessage,
            Alert,
            AlertTrigger,
            Watchlist,
            WatchlistSymbol,
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
            reply="ok",
            action="create_alert",
            action_symbol="AAPL",
            action_condition_type="not_a_real_condition",
            action_parameter="200",
        )
        self.assertIsNone(r.action_condition_type)

    def test_valid_condition_type_kept(self):
        r = ChatReplyResponse(
            reply="ok",
            action="create_alert",
            action_symbol="AAPL",
            action_condition_type="price_above",
            action_parameter="200",
        )
        self.assertEqual(r.action_condition_type, "price_above")


class TestActionStepDetail(unittest.TestCase):
    """_action_step_detail — the action_confirmation block's tool-specific
    content (5.7.2: action-specific confirmation/results). Must read only
    the request's own typed action_* fields, never the model's prose."""

    def test_create_alert_detail(self):
        parsed = ChatReplyResponse(
            reply="ok", action="create_alert", action_symbol="AAPL",
            action_condition_type="price_above", action_parameter="200",
        )
        detail = _action_step_detail(parsed)
        self.assertEqual(
            detail,
            {"symbol": "AAPL", "condition_type": "price_above", "parameter": "200", "label": None, "target_id": None},
        )

    def test_modify_alert_detail(self):
        parsed = ChatReplyResponse(
            reply="ok", action="modify_alert", action_target_id=7, action_parameter="230",
        )
        detail = _action_step_detail(parsed)
        self.assertEqual(detail["target_id"], 7)
        self.assertEqual(detail["parameter"], "230")

    def test_delete_alert_detail(self):
        parsed = ChatReplyResponse(reply="ok", action="delete_alert", action_target_id=3)
        self.assertEqual(_action_step_detail(parsed), {"target_id": 3})

    def test_add_to_watchlist_detail(self):
        parsed = ChatReplyResponse(
            reply="ok", action="add_to_watchlist", action_symbol="TSLA", action_watchlist="Swing",
        )
        self.assertEqual(_action_step_detail(parsed), {"symbol": "TSLA", "watchlist": "Swing"})

    def test_remove_from_watchlist_detail(self):
        parsed = ChatReplyResponse(
            reply="ok", action="remove_from_watchlist", action_symbol="TSLA", action_watchlist=None,
        )
        self.assertEqual(_action_step_detail(parsed), {"symbol": "TSLA", "watchlist": None})

    def test_create_watchlist_detail(self):
        parsed = ChatReplyResponse(reply="ok", action="create_watchlist", action_watchlist="Core")
        self.assertEqual(_action_step_detail(parsed), {"watchlist": "Core"})

    def test_delete_watchlist_detail(self):
        parsed = ChatReplyResponse(reply="ok", action="delete_watchlist", action_watchlist="Old", action_target_id=5)
        self.assertEqual(_action_step_detail(parsed), {"watchlist": "Old", "target_id": 5})

    def test_non_crud_action_returns_none(self):
        parsed = ChatReplyResponse(reply="ok", action="get_quote", action_tool_arguments={"symbol": "AAPL"})
        self.assertIsNone(_action_step_detail(parsed))

    def test_none_action_returns_none(self):
        self.assertIsNone(_action_step_detail(ChatReplyResponse(reply="ok", action="none")))


class TestConfirmGate(_DBBase):
    """The safety-critical part: a destructive action never touches the
    DB without action_confirmed=True, no matter what the model set for
    "reply" — _finalize_parsed enforces this in Python."""

    def test_delete_alert_without_confirmation_does_not_delete(self):
        alert = AlertRepository(self.db).create("A", "AAPL", "price_above", "200")
        parsed = _parsed(action="delete_alert", action_target_id=alert.id, action_confirmed=False)

        text, grounded, _ = _finalize_parsed(self.db, parsed, [])

        self.assertIn("confirm", text.lower())
        self.assertTrue(grounded)
        self.assertIsNotNone(AlertRepository(self.db).get_by_id(alert.id))  # still there

    def test_pending_confirmation_survives_without_transcript(self):
        alert = AlertRepository(self.db).create("A", "AAPL", "price_above", "200")
        state = {}
        pending = _parsed(action="delete_alert", action_target_id=alert.id, action_confirmed=False)

        text, grounded, _ = _finalize_parsed(self.db, pending, [], planner_state=state)

        self.assertIn("confirm", text.lower())
        self.assertEqual(state["pending_confirmation"]["target_id"], alert.id)
        confirmed = _parsed(action="none", reply="yes")
        text, grounded, _ = _finalize_parsed(
            self.db, confirmed, [], user_content="yes", planner_state=state
        )
        self.assertIn("deleted", text.lower())
        self.assertTrue(grounded)
        self.assertIsNone(AlertRepository(self.db).get_by_id(alert.id))
        self.assertIsNone(state["pending_confirmation"])

    def test_delete_alert_with_confirmation_deletes(self):
        alert = AlertRepository(self.db).create("A", "AAPL", "price_above", "200")
        parsed = _parsed(action="delete_alert", action_target_id=alert.id, action_confirmed=True)

        text, grounded, _ = _finalize_parsed(self.db, parsed, [])

        self.assertIn("deleted", text.lower())
        self.assertTrue(grounded)
        self.assertIsNone(AlertRepository(self.db).get_by_id(alert.id))

    def test_remove_from_watchlist_without_confirmation_does_not_remove(self):
        wl = WatchlistRepository(self.db).create_watchlist("Watch1")
        WatchlistRepository(self.db).add_symbol_to_watchlist(wl.id, "AAPL")
        parsed = _parsed(
            action="remove_from_watchlist",
            action_symbol="AAPL",
            action_confirmed=False,
        )

        text, grounded, _ = _finalize_parsed(self.db, parsed, [])

        self.assertIn("confirm", text.lower())
        self.assertIsNotNone(WatchlistRepository(self.db).get_watchlist_symbol(wl.id, "AAPL"))

    def test_remove_from_watchlist_with_confirmation_removes(self):
        wl = WatchlistRepository(self.db).create_watchlist("Watch1")
        WatchlistRepository(self.db).add_symbol_to_watchlist(wl.id, "AAPL")
        parsed = _parsed(
            action="remove_from_watchlist",
            action_symbol="AAPL",
            action_confirmed=True,
        )

        text, grounded, _ = _finalize_parsed(self.db, parsed, [])

        self.assertIn("removed", text.lower())
        self.assertIsNone(WatchlistRepository(self.db).get_watchlist_symbol(wl.id, "AAPL"))

    def test_delete_watchlist_without_confirmation_does_not_delete(self):
        wl = WatchlistRepository(self.db).create_watchlist("Swing Setups")
        parsed = _parsed(
            action="delete_watchlist",
            action_watchlist="Swing Setups",
            action_confirmed=False,
        )

        text, grounded, _ = _finalize_parsed(self.db, parsed, [])

        self.assertIn("confirm", text.lower())
        self.assertIsNotNone(WatchlistRepository(self.db).get_watchlist(wl.id))

    def test_delete_watchlist_with_confirmation_deletes(self):
        wl = WatchlistRepository(self.db).create_watchlist("Swing Setups")
        parsed = _parsed(
            action="delete_watchlist",
            action_watchlist="Swing Setups",
            action_confirmed=True,
        )

        text, grounded, _ = _finalize_parsed(self.db, parsed, [])

        self.assertIn("deleted", text.lower())
        self.assertIsNone(WatchlistRepository(self.db).get_watchlist(wl.id))

    def test_save_to_journal_requires_server_confirmation_and_replays_typed_entry(self):
        state = {}
        proposed = _parsed(
            action="save_to_journal",
            action_confirmed=True,  # model claims approval; server must ignore it
            action_tool_arguments={
                "entry": {
                    "symbol": "AAPL",
                    "status": "planned",
                    "entry_price": 200,
                    "stop_price": 190,
                    "target_price": 220,
                    "notes": "breakout plan",
                },
            },
        )

        text, grounded, _ = _finalize_parsed(self.db, proposed, [], planner_state=state)

        self.assertIn("confirm", text.lower())
        self.assertTrue(grounded)
        self.assertFalse(proposed.action_confirmed)
        self.assertEqual(state["pending_confirmation"]["tool_arguments"]["entry"]["symbol"], "AAPL")

        confirmed = _parsed(action="none", reply="yes")
        text, grounded, _ = _finalize_parsed(
            self.db, confirmed, [], user_content="yes", planner_state=state
        )
        self.assertIn("AAPL", text)
        self.assertTrue(grounded)
        self.assertIsNone(state["pending_confirmation"])

    def test_additive_actions_ignore_action_confirmed(self):
        # create_alert / add_to_watchlist / create_watchlist fire
        # regardless of action_confirmed — it's only meaningful for the
        # destructive set.
        parsed = _parsed(
            action="create_alert",
            action_symbol="AAPL",
            action_condition_type="price_above",
            action_parameter="200",
            action_confirmed=False,
        )
        text, grounded, _ = _finalize_parsed(self.db, parsed, [])
        self.assertIn("done", text.lower())
        self.assertEqual(len(AlertRepository(self.db).get_all()), 1)


class TestRunTurnActions(_DBBase):
    """_run_turn_actions (2026-09-16): chains up to _MAX_CHAIN_STEPS
    single-action completion calls within one turn, gated on a cheap
    "and"/"then"/"also"/";" hint in the trader's OWN message so an
    ordinary single-action turn never pays for an extra completion."""

    def _mock_complete(self, *json_bodies: str):
        p = patch("backend.ai.chat.ai_manager")
        mock_ai = p.start()
        self.addCleanup(p.stop)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_model = ""
        mock_ai.complete = AsyncMock(side_effect=[_reply(b) for b in json_bodies])
        return mock_ai

    def test_no_chain_without_multi_step_hint(self):
        mock_ai = self._mock_complete()  # no continuation call should happen at all
        parsed = _parsed(action="create_watchlist", action_watchlist="Tech")
        text, grounded, _ = _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "create a watchlist called Tech",
            None,
        )
        self.assertIn("Tech", text)
        mock_ai.complete.assert_not_called()

    def test_no_chain_when_first_step_did_not_execute(self):
        # Hint present, but the first turn was just a plain answer
        # (action="none") — nothing was executed, so there's nothing to
        # continue from.
        mock_ai = self._mock_complete()
        parsed = _parsed(action="none", reply="Sure, here's the market and sector view.")
        text, grounded, _ = _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "how's the market and sector doing",
            None,
        )
        self.assertEqual(text, "Sure, here's the market and sector view.")
        mock_ai.complete.assert_not_called()

    def test_chains_second_action_when_hinted(self):
        mock_ai = self._mock_complete(
            '{"reply": "ok", "grounded": true, "action": "add_to_watchlist", '
            '"action_symbol": "NVDA", "action_watchlist": "Tech"}',
            '{"reply": "ok", "grounded": true}',  # action="none" (default) — stop
        )
        parsed = _parsed(action="create_watchlist", action_watchlist="Tech")
        text, grounded, _ = _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "create a watchlist called Tech and add NVDA to it",
            None,
        )
        self.assertIn("Tech", text)
        self.assertIn("NVDA", text)
        self.assertEqual(mock_ai.complete.call_count, 2)
        wl = WatchlistRepository(self.db).get_watchlist_by_name("Tech")
        self.assertIsNotNone(WatchlistRepository(self.db).get_watchlist_symbol(wl.id, "NVDA"))

    def test_continuation_call_uses_the_lean_system_prompt(self):
        # 2026-09-16 optimization: a chained continuation call must use
        # CHAT_CONTINUATION_SYSTEM_PROMPT, not the full CHAT_SYSTEM_PROMPT
        # — resending all 12 rules (grounding/analysis rules a
        # continuation never needs) on every chained step is pure waste.
        from backend.ai.prompt import CHAT_CONTINUATION_SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT

        mock_ai = self._mock_complete('{"reply": "ok", "grounded": true}')
        parsed = _parsed(action="create_watchlist", action_watchlist="Tech")
        _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "create a watchlist called Tech and add NVDA to it",
            None,
        )
        self.assertEqual(mock_ai.complete.call_count, 1)
        used_system = mock_ai.complete.call_args.kwargs["system"]
        self.assertEqual(used_system, CHAT_CONTINUATION_SYSTEM_PROMPT)
        self.assertNotEqual(used_system, CHAT_SYSTEM_PROMPT)
        self.assertLess(len(CHAT_CONTINUATION_SYSTEM_PROMPT), len(CHAT_SYSTEM_PROMPT))

    def test_stops_chain_at_pending_confirmation(self):
        alert = AlertRepository(self.db).create("A", "NVDA", "price_above", "220")
        mock_ai = self._mock_complete(
            '{"reply": "ok", "grounded": true, "action": "delete_alert", '
            f'"action_target_id": {alert.id}}}',
        )
        parsed = _parsed(
            action="create_alert",
            action_symbol="AAPL",
            action_condition_type="price_above",
            action_parameter="200",
        )
        text, grounded, _ = _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "create an AAPL alert and then delete my nvda alert",
            None,
        )
        self.assertIn("confirm", text.lower())
        self.assertEqual(mock_ai.complete.call_count, 1)
        # The destructive step only asked — it must not have run.
        self.assertIsNotNone(AlertRepository(self.db).get_by_id(alert.id))

    def test_respects_max_chain_steps(self):
        # Every continuation keeps returning another real action — the
        # cap must still stop it rather than looping indefinitely.
        mock_ai = self._mock_complete(
            *[
                '{"reply": "ok", "grounded": true, "action": "add_to_watchlist", '
                f'"action_symbol": "SYM{i}", "action_watchlist": "Tech"}}'
                for i in range(10)
            ]
        )
        parsed = _parsed(action="create_watchlist", action_watchlist="Tech")
        _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "create Tech and add A and B and C and D",
            None,
        )
        self.assertEqual(mock_ai.complete.call_count, _MAX_CHAIN_STEPS - 1)

    def test_stops_duplicate_action_in_chain(self):
        mock_ai = self._mock_complete(
            '{"reply": "ok", "grounded": true, "action": "create_watchlist", '
            '"action_watchlist": "Tech"}',
        )
        parsed = _parsed(action="create_watchlist", action_watchlist="Tech")
        text, grounded, _ = _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "create a watchlist called Tech and then create a watchlist called Tech",
            None,
        )
        self.assertIn("repeated", text)
        self.assertFalse(grounded)
        self.assertEqual(mock_ai.complete.call_count, 1)

    def test_reuses_duplicate_read_result_without_second_tool_call(self):
        mock_ai = self._mock_complete(
            '{"reply": "ok", "grounded": true, "action": "get_quote", '
            '"action_tool_arguments": {"symbol": "AAPL"}}',
        )
        parsed = _parsed(
            action="get_quote",
            action_tool_arguments={"symbol": "AAPL"},
        )
        trace = []
        with patch("backend.ai.chat._run_action", return_value=("AAPL quote", True, [])) as run:
            text, grounded, _ = _run_turn_actions(
                self.db,
                parsed,
                [],
                [],
                None,
                [],
                "get AAPL quote and then get AAPL quote again",
                None,
                trace=trace,
            )
        self.assertIn("reused", text)
        self.assertFalse(grounded)
        run.assert_called_once()
        self.assertEqual(mock_ai.complete.call_count, 1)
        cache_event = next(item for item in trace if item.get("provider") == "turn-cache")
        self.assertTrue(cache_event["reused"])

    def test_stops_before_continuation_when_turn_time_budget_is_exhausted(self):
        mock_ai = self._mock_complete(
            '{"reply": "should not be called", "grounded": true}'
        )
        previous = settings.ai.chat_max_turn_seconds
        self.addCleanup(setattr, settings.ai, "chat_max_turn_seconds", previous)
        settings.ai.chat_max_turn_seconds = 1
        parsed = _parsed(action="create_watchlist", action_watchlist="Tech")
        text, grounded, _ = _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "create Tech and then add NVDA",
            None,
            started_at=0,
        )
        self.assertIn("time budget", text)
        self.assertFalse(grounded)
        mock_ai.complete.assert_not_called()

    def test_continuation_failure_stops_chain_without_raising(self):
        p = patch("backend.ai.chat.ai_manager")
        mock_ai = p.start()
        self.addCleanup(p.stop)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_model = ""
        mock_ai.complete = AsyncMock(side_effect=RuntimeError("provider down"))
        parsed = _parsed(action="create_watchlist", action_watchlist="Tech")
        text, grounded, _ = _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "create a watchlist called Tech and add NVDA to it",
            None,
        )
        self.assertIn("Tech", text)  # first step's result still returned

    def test_continuation_retries_once_on_malformed_reply(self):
        mock_ai = self._mock_complete(
            "not json at all",  # malformed — should be retried, not give up
            '{"reply": "ok", "grounded": true, "action": "add_to_watchlist", '
            '"action_symbol": "NVDA", "action_watchlist": "Tech"}',
            '{"reply": "ok", "grounded": true}',  # stop the chain
        )
        parsed = _parsed(action="create_watchlist", action_watchlist="Tech")
        text, grounded, _ = _run_turn_actions(
            self.db,
            parsed,
            [],
            [],
            None,
            [],
            "create a watchlist called Tech and add NVDA to it",
            None,
        )
        self.assertIn("NVDA", text)
        self.assertEqual(mock_ai.complete.call_count, 3)  # malformed + retry + stop call

    def test_screened_tickers_accumulate_across_chain(self):
        from backend.nl_search.executor import ExecutionResult
        from backend.nl_search.schema import ScannedResultItem

        def item(symbol):
            return ScannedResultItem(symbol=symbol, total_score=1.0, rank=1, signals=[])

        first = ExecutionResult(
            matched_all=[],
            filter_description="oversold",
            matched_count=1,
            universe_size=3,
            top_n=[item("AAPL")],
        )
        second = ExecutionResult(
            matched_all=[],
            filter_description="overbought",
            matched_count=1,
            universe_size=3,
            top_n=[item("MSFT")],
        )
        with (
            patch(
                "backend.nl_search.parser.parse_query", return_value=(MagicMock(), None, "rules")
            ),
            patch("backend.nl_search.executor.execute_query", side_effect=[first, second]),
        ):
            self._mock_complete(
                '{"reply": "ok", "grounded": true, "action": "run_screen", '
                '"action_query": "overbought"}',
                '{"reply": "ok", "grounded": true}',
            )
            parsed = _parsed(action="run_screen", action_query="oversold")
            text, grounded, screened = _run_turn_actions(
                self.db,
                parsed,
                [],
                [],
                None,
                [],
                "screen for oversold names and also overbought names",
                None,
            )
        self.assertEqual(screened, ["AAPL", "MSFT"])


class TestActionHandlers(_DBBase):
    def test_create_alert(self):
        text, grounded = _create_alert(
            self.db,
            _parsed(
                action_symbol="AAPL",
                action_condition_type="price_above",
                action_parameter="200",
            ),
        )
        self.assertTrue(grounded)
        alerts = AlertRepository(self.db).get_all()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].symbol, "AAPL")
        self.assertEqual(alerts[0].condition_type, "price_above")
        self.assertEqual(alerts[0].parameter, "200")

    def test_create_alert_uses_custom_label(self):
        text, _ = _create_alert(
            self.db,
            _parsed(
                action_symbol="AAPL",
                action_condition_type="price_above",
                action_parameter="200",
                action_label="Breakout watch",
            ),
        )
        self.assertEqual(AlertRepository(self.db).get_all()[0].name, "Breakout watch")

    def test_create_alert_missing_fields_asks_not_creates(self):
        text, grounded = _create_alert(self.db, _parsed(action_symbol="AAPL"))
        self.assertFalse(grounded)
        self.assertEqual(AlertRepository(self.db).get_all(), [])

    def test_delete_alert_not_found(self):
        text, grounded = _delete_alert(self.db, _parsed(action_target_id=999))
        self.assertFalse(grounded)
        self.assertIn("doesn't exist", text.lower())

    def test_modify_alert_changes_threshold_in_place(self):
        alert = AlertRepository(self.db).create("A", "NVDA", "price_above", "220")
        original_id = alert.id
        text, grounded = _modify_alert(
            self.db,
            _parsed(action_target_id=alert.id, action_parameter="230"),
        )
        self.assertTrue(grounded)
        self.assertIn("230", text)
        updated = AlertRepository(self.db).get_by_id(original_id)
        self.assertEqual(updated.parameter, "230")
        self.assertEqual(updated.id, original_id)  # same row — not delete+recreate
        self.assertEqual(updated.condition_type, "price_above")  # untouched field kept

    def test_modify_alert_changes_condition_type(self):
        alert = AlertRepository(self.db).create("A", "NVDA", "price_above", "220")
        _modify_alert(
            self.db, _parsed(action_target_id=alert.id, action_condition_type="price_below")
        )
        updated = AlertRepository(self.db).get_by_id(alert.id)
        self.assertEqual(updated.condition_type, "price_below")
        self.assertEqual(updated.parameter, "220")  # untouched field kept

    def test_modify_alert_renames(self):
        alert = AlertRepository(self.db).create("Old name", "NVDA", "price_above", "220")
        _modify_alert(self.db, _parsed(action_target_id=alert.id, action_label="New name"))
        updated = AlertRepository(self.db).get_by_id(alert.id)
        self.assertEqual(updated.name, "New name")

    def test_modify_alert_not_found(self):
        text, grounded = _modify_alert(
            self.db,
            _parsed(action_target_id=999, action_parameter="230"),
        )
        self.assertFalse(grounded)
        self.assertIn("doesn't exist", text.lower())

    def test_modify_alert_missing_id_asks(self):
        text, grounded = _modify_alert(self.db, _parsed(action_parameter="230"))
        self.assertFalse(grounded)
        self.assertIn("id", text.lower())

    def test_modify_alert_no_fields_asks_what_to_change(self):
        alert = AlertRepository(self.db).create("A", "NVDA", "price_above", "220")
        text, grounded = _modify_alert(self.db, _parsed(action_target_id=alert.id))
        self.assertFalse(grounded)
        self.assertIn("change", text.lower())
        # Nothing was touched.
        unchanged = AlertRepository(self.db).get_by_id(alert.id)
        self.assertEqual(unchanged.parameter, "220")

    def test_modify_alert_not_destructive_fires_without_confirmation(self):
        alert = AlertRepository(self.db).create("A", "NVDA", "price_above", "220")
        text, grounded, _ = _finalize_parsed(
            self.db,
            _parsed(action="modify_alert", action_target_id=alert.id, action_parameter="230"),
            [],
        )
        self.assertNotIn("confirm", text.lower())
        self.assertEqual(AlertRepository(self.db).get_by_id(alert.id).parameter, "230")

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
            self.db,
            _parsed(action_symbol="ZZZZ", action_watchlist="Watch1"),
        )
        self.assertFalse(grounded)
        self.assertIn("wasn't in", text)

    def test_remove_from_watchlist_unnamed_but_named_list_missing(self):
        text, grounded = _remove_from_watchlist(
            self.db,
            _parsed(action_symbol="AAPL", action_watchlist="Nope"),
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
            self.db,
            _parsed(action_symbol="RIVN", action_watchlist="Swing Setups"),
        )

        self.assertTrue(grounded)
        self.assertIn("removed RIVN from Swing Setups", text)
        self.assertIsNotNone(repo.get_watchlist_symbol(tech.id, "RIVN"))  # untouched
        self.assertIsNone(repo.get_watchlist_symbol(swing.id, "RIVN"))

    def test_create_watchlist_with_initial_symbol(self):
        text, grounded = _create_watchlist(
            self.db,
            _parsed(
                action_watchlist="Swing Setups",
                action_symbol="TSLA",
            ),
        )
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
            self.db,
            _parsed(action_symbol="SPY", action_entity_type="etf"),
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
            self.db,
            _parsed(action_symbol="SPY", action_entity_type="stock"),
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
            self.db,
            _parsed(action_symbol="ZZZZ", action_entity_type="etf"),
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
            self.db,
            _parsed(action_symbol="SPY", action_entity_type="etf"),
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

        text, grounded = _set_entity_type(
            self.db,
            _parsed(
                action_symbol="SPY",
                action_entity_type="etf",
                action_watchlist="Tech",
            ),
        )

        self.assertTrue(grounded)
        self.assertEqual(repo.get_watchlist_symbol(tech.id, "SPY").entity_type, "etf")
        self.assertIsNone(repo.get_watchlist_symbol(swing.id, "SPY").entity_type)

    def test_not_destructive_not_gated_by_confirmation(self):
        repo = WatchlistRepository(self.db)
        wl = repo.create_watchlist("Default")
        repo.add_symbol_to_watchlist(wl.id, "SPY")

        text, grounded, _ = _finalize_parsed(
            self.db,
            _parsed(
                action="set_entity_type",
                action_symbol="SPY",
                action_entity_type="etf",
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
        with (
            patch.object(ai_manager.settings, "backtest_tool_enabled", False),
            patch("backend.backtesting.engine.backtest_engine") as engine,
        ):
            text, grounded = _run_backtest(self.db, _parsed(action_symbol="AAPL"))
        self.assertFalse(grounded)
        self.assertIn("isn't enabled", text)
        engine.run.assert_not_called()

    def test_rate_limited_degrades(self):
        with (
            self._enabled(),
            patch("backend.api.rate_limit._backtest_limiter") as limiter,
            patch("backend.backtesting.engine.backtest_engine") as engine,
        ):
            limiter.is_allowed.return_value = (False, 42)
            text, grounded = _run_backtest(self.db, _parsed(action_symbol="AAPL"))
        self.assertFalse(grounded)
        self.assertIn("rate-limited", text)
        self.assertIn("42", text)
        engine.run.assert_not_called()

    def test_insufficient_data_degrades(self):
        run = MagicMock(status="completed", total_signals=0)
        with (
            self._enabled(),
            patch("backend.api.rate_limit._backtest_limiter") as limiter,
            patch("backend.backtesting.engine.backtest_engine") as engine,
            patch("backend.repositories.backtest_repository.BacktestRepository") as repo_cls,
        ):
            limiter.is_allowed.return_value = (True, 0)
            engine.run.return_value = 1
            repo_cls.return_value.get_run.return_value = run
            text, grounded = _run_backtest(self.db, _parsed(action_symbol="ZZZZ"))
        self.assertFalse(grounded)
        self.assertIn("Not enough historical data", text)

    def test_happy_path_reports_real_numbers(self):
        run = MagicMock(
            status="completed",
            total_signals=42,
            signals_requested="RSI_OVERSOLD,MACD_BULLISH",
            win_rate_1d=0.62,
            avg_return_1d=0.012,
            avg_return_5d=0.034,
        )
        with (
            self._enabled(),
            patch("backend.api.rate_limit._backtest_limiter") as limiter,
            patch("backend.backtesting.engine.backtest_engine") as engine,
            patch("backend.repositories.backtest_repository.BacktestRepository") as repo_cls,
        ):
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
            status="completed",
            total_signals=5,
            signals_requested="RSI_OVERSOLD",
            win_rate_1d=0.4,
            avg_return_1d=-0.01,
            avg_return_5d=0.0,
        )
        with (
            self._enabled(),
            patch("backend.api.rate_limit._backtest_limiter") as limiter,
            patch("backend.backtesting.engine.backtest_engine") as engine,
            patch("backend.repositories.backtest_repository.BacktestRepository") as repo_cls,
        ):
            limiter.is_allowed.return_value = (True, 0)
            engine.run.return_value = 3
            repo_cls.return_value.get_run.return_value = run
            text, grounded, _ = _finalize_parsed(
                self.db,
                _parsed(action="run_backtest", action_symbol="AAPL"),
                [],
            )
        self.assertNotIn("confirm", text.lower())
        engine.run.assert_called_once()


class TestRunScreen(_DBBase):
    """run_screen — a real, non-destructive read over the trader's
    watchlist via the existing nl_search parser/executor (the same
    engine behind POST /api/nl-search)."""

    def _result(self, *, top_n=None, universe_size=5, matched_count=None):
        from backend.nl_search.executor import ExecutionResult

        top_n = top_n or []
        return ExecutionResult(
            matched_all=[],
            top_n=top_n,
            filter_description="RSI oversold",
            universe_size=universe_size,
            matched_count=matched_count if matched_count is not None else len(top_n),
        )

    def _item(self, symbol, score):
        from backend.nl_search.schema import ScannedResultItem

        return ScannedResultItem(symbol=symbol, total_score=score, rank=1, signals=[])

    def test_no_query(self):
        text, grounded, _ = _run_screen(self.db, _parsed())
        self.assertFalse(grounded)
        self.assertIn("screen", text.lower())

    def test_unknown_watchlist_name_degrades(self):
        text, grounded, _ = _run_screen(
            self.db,
            _parsed(action_query="oversold", action_watchlist="Nope"),
        )
        self.assertFalse(grounded)
        self.assertIn("couldn't find", text)

    def test_empty_universe_degrades(self):
        with (
            patch("backend.nl_search.parser.parse_query") as parse,
            patch("backend.nl_search.executor.execute_query") as execute,
        ):
            parse.return_value = (MagicMock(), None, "rules")
            execute.return_value = self._result(universe_size=0)
            text, grounded, _ = _run_screen(self.db, _parsed(action_query="oversold"))
        self.assertFalse(grounded)
        self.assertIn("empty", text.lower())

    def test_no_matches_is_still_grounded(self):
        with (
            patch("backend.nl_search.parser.parse_query") as parse,
            patch("backend.nl_search.executor.execute_query") as execute,
        ):
            parse.return_value = (MagicMock(), None, "rules")
            execute.return_value = self._result(top_n=[], universe_size=8, matched_count=0)
            text, grounded, _ = _run_screen(self.db, _parsed(action_query="oversold"))
        self.assertTrue(grounded)
        self.assertIn("No matches", text)
        self.assertIn("RSI oversold", text)

    def test_happy_path_reports_matches(self):
        with (
            patch("backend.nl_search.parser.parse_query") as parse,
            patch("backend.nl_search.executor.execute_query") as execute,
        ):
            parse.return_value = (MagicMock(), None, "rules")
            execute.return_value = self._result(
                top_n=[self._item("AAPL", 62.0), self._item("MSFT", 41.0)],
            )
            text, grounded, _ = _run_screen(self.db, _parsed(action_query="oversold"))
        self.assertTrue(grounded)
        self.assertIn("AAPL", text)
        self.assertIn("MSFT", text)
        self.assertIn("2 matches", text)

    def test_more_than_five_matches_are_summarized(self):
        items = [self._item(f"T{i}", float(i)) for i in range(7)]
        with (
            patch("backend.nl_search.parser.parse_query") as parse,
            patch("backend.nl_search.executor.execute_query") as execute,
        ):
            parse.return_value = (MagicMock(), None, "rules")
            execute.return_value = self._result(top_n=items, matched_count=7)
            text, grounded, _ = _run_screen(self.db, _parsed(action_query="oversold"))
        self.assertTrue(grounded)
        self.assertIn("+2 more", text)

    def test_executor_exception_degrades_gracefully(self):
        with patch("backend.nl_search.parser.parse_query", side_effect=RuntimeError("boom")):
            text, grounded, _ = _run_screen(self.db, _parsed(action_query="oversold"))
        self.assertFalse(grounded)
        self.assertIn("went wrong", text)

    def test_finalize_parsed_never_asks_for_confirmation(self):
        # run_screen is not in _DESTRUCTIVE_ACTIONS — it fires on the
        # first mention, like run_backtest.
        with (
            patch("backend.nl_search.parser.parse_query") as parse,
            patch("backend.nl_search.executor.execute_query") as execute,
        ):
            parse.return_value = (MagicMock(), None, "rules")
            execute.return_value = self._result(top_n=[self._item("AAPL", 62.0)])
            text, grounded, _ = _finalize_parsed(
                self.db,
                _parsed(action="run_screen", action_query="oversold"),
                [],
            )
        self.assertNotIn("confirm", text.lower())
        self.assertIn("AAPL", text)


class TestRunActionNeverRaises(_DBBase):
    @patch(
        "backend.ai.chat._ACTION_HANDLERS",
        {
            "create_alert": MagicMock(side_effect=RuntimeError("boom")),
        },
    )
    def test_handler_exception_degrades_gracefully(self):
        text, grounded, _ = _run_action(self.db, _parsed(action="create_alert"))
        self.assertFalse(grounded)
        self.assertIn("went wrong", text.lower())

    @patch("backend.ai.chat.default_registry.execute", side_effect=RuntimeError("provider down"))
    def test_market_tool_exception_degrades_gracefully(self, _execute):
        trace: list[dict] = []
        text, grounded, _ = _run_action(
            self.db, _parsed(action="get_quote", action_tool_arguments={"symbol": "AAPL"}), trace=trace
        )
        self.assertFalse(grounded)
        self.assertIn("went wrong", text.lower())
        self.assertEqual(trace[-1]["failure_kind"], "action_exception")


class TestRunActionInvalidatesBaseline(_DBBase):
    """2026-09-16: a chat action that changes alerts/watchlists must
    drop backend.ai.market_baseline's cache, or a related follow-up
    question within its 20s TTL (e.g. "create an alert" then
    immediately "change it to 230") can see a pre-mutation snapshot."""

    def test_create_alert_invalidates(self):
        with patch("backend.ai.market_baseline.invalidate_cache") as inv:
            _run_action(
                self.db,
                _parsed(
                    action="create_alert",
                    action_symbol="AAPL",
                    action_condition_type="price_above",
                    action_parameter="200",
                ),
            )
        inv.assert_called_once()

    def test_add_to_watchlist_invalidates(self):
        with patch("backend.ai.market_baseline.invalidate_cache") as inv:
            _run_action(self.db, _parsed(action="add_to_watchlist", action_symbol="RIVN"))
        inv.assert_called_once()

    def test_run_backtest_does_not_invalidate(self):
        with (
            patch("backend.ai.market_baseline.invalidate_cache") as inv,
            patch(
                "backend.ai.chat._ACTION_HANDLERS",
                {
                    "run_backtest": MagicMock(return_value=("ok", True)),
                },
            ),
        ):
            _run_action(self.db, _parsed(action="run_backtest", action_symbol="AAPL"))
        inv.assert_not_called()

    def test_run_screen_does_not_invalidate(self):
        with (
            patch("backend.ai.market_baseline.invalidate_cache") as inv,
            patch(
                "backend.ai.chat._ACTION_HANDLERS",
                {
                    "run_screen": MagicMock(return_value=("ok", True, [])),
                },
            ),
        ):
            _run_action(self.db, _parsed(action="run_screen", action_query="oversold"))
        inv.assert_not_called()

    def test_failed_handler_does_not_invalidate(self):
        with (
            patch("backend.ai.market_baseline.invalidate_cache") as inv,
            patch(
                "backend.ai.chat._ACTION_HANDLERS",
                {
                    "create_alert": MagicMock(side_effect=RuntimeError("boom")),
                },
            ),
        ):
            _run_action(self.db, _parsed(action="create_alert"))
        inv.assert_not_called()


class TestConfirmPromptWording(_DBBase):
    def test_delete_alert_wording(self):
        self.assertIn(
            "confirm",
            _confirm_prompt(self.db, _parsed(action="delete_alert")).lower(),
        )

    def test_remove_from_watchlist_wording_includes_symbol_and_list(self):
        text = _confirm_prompt(
            self.db,
            _parsed(
                action="remove_from_watchlist",
                action_symbol="RIVN",
                action_watchlist="Swing Setups",
            ),
        )
        self.assertIn("RIVN", text)
        self.assertIn("Swing Setups", text)

    def test_delete_watchlist_wording_includes_name(self):
        text = _confirm_prompt(
            self.db,
            _parsed(action="delete_watchlist", action_watchlist="Swing Setups"),
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
            "delete my watchlist",
            "please remove my watchlist",
            "can you clear my watchlist",
            "trash my watch list",
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
        transcript = [
            (
                "assistant",
                'Delete the watchlist "Watch1"? This removes every ticker in it — say yes to confirm.',
            )
        ]
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
        transcript = [
            (
                "assistant",
                'Delete the watchlist "Watch1"? This removes every ticker in it — say yes to confirm.',
            )
        ]
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
            "how many watchlist i have",
            "how many watchlists do i have",
            "what watchlists do I have",
            "which watchlists do I have",
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


class TestWatchlistContentsReply(_DBBase):
    """_watchlist_contents_reply / _WATCHLIST_CONTENTS_INTENT: a
    deterministic, real answer to "what tickers are in <name>" for one
    specific named watchlist. Confirmed live 2026-09-12: asked "what
    tickers are in Market Context" (a real watchlist name), the model had
    no watchlist data and fabricated the union of every real watchlist's
    symbols as if it were that one list's contents."""

    def test_intent_matches_reported_phrasing_and_captures_name(self):
        cases = {
            "what tickers are in Market Context": "Market Context",
            "What tickers are in Market Context?": "Market Context",
            "which symbols are on my Market Context list": "Market Context",
            "what's in the Market Context watchlist": "Market Context",
            "what's in the Market Context watchlist?": "Market Context",
            "what tickers are in my Default watchlist": "Default",
        }
        for phrase, expected in cases.items():
            m = _WATCHLIST_CONTENTS_INTENT.search(phrase)
            self.assertIsNotNone(m, phrase)
            name = m.group("name1") or m.group("name2") or ""
            self.assertEqual(name, expected, phrase)

    def test_generic_watchlist_mentions_capture_empty_name(self):
        # These should fall through to _WATCHLIST_LIST_INTENT (list-all),
        # not resolve as a bogus named lookup.
        for phrase in ("what's on my watchlist", "how many watchlists do I have"):
            m = _WATCHLIST_CONTENTS_INTENT.search(phrase)
            if m:
                name = m.group("name1") or m.group("name2") or ""
                self.assertEqual(name, "", phrase)

    def test_unknown_name_returns_none(self):
        self.assertIsNone(_watchlist_contents_reply(self.db, "Nonexistent List"))

    def test_empty_name_returns_none(self):
        self.assertIsNone(_watchlist_contents_reply(self.db, "  "))

    def test_known_name_returns_only_that_lists_symbols(self):
        repo = WatchlistRepository(self.db)
        default = repo.create_watchlist("Default")
        for sym in ("SPY", "CTNT", "CYN", "DVLT", "AAPL", "MSFT"):
            repo.add_symbol_to_watchlist(default.id, sym)
        mc = repo.create_watchlist("Market Context")
        for sym in ("SPY", "QQQ", "IWM", "VIXY"):
            repo.add_symbol_to_watchlist(mc.id, sym)

        text = _watchlist_contents_reply(self.db, "Market Context")
        self.assertIn("Market Context", text)
        for sym in ("SPY", "QQQ", "IWM", "VIXY"):
            self.assertIn(sym, text)
        # Must NOT leak the other watchlist's non-overlapping symbols.
        for sym in ("CTNT", "CYN", "DVLT", "AAPL", "MSFT"):
            self.assertNotIn(sym, text)

    def test_case_insensitive_fallback(self):
        repo = WatchlistRepository(self.db)
        wl = repo.create_watchlist("Market Context")
        repo.add_symbol_to_watchlist(wl.id, "SPY")
        text = _watchlist_contents_reply(self.db, "market context")
        self.assertIsNotNone(text)
        self.assertIn("SPY", text)


class TestEndToEnd(unittest.TestCase):
    """Through answer_chat_message -> _generate_reply -> _finalize_parsed
    -> the real repos, proving the db gets threaded all the way down."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        for model in (
            ChatSession,
            ChatMessage,
            Alert,
            AlertTrigger,
            Watchlist,
            WatchlistSymbol,
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
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "On it.", "grounded": true, "action": "add_to_watchlist", '
                '"action_symbol": "RIVN"}'
            )
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
    def test_multi_step_request_end_to_end(self, mock_ai):
        """The regression this was built for: a single compound message
        ("create X and add Y to it") used to only ever do the first
        thing. Three real completion calls: the turn's own first-action
        decision, then two multi-step continuations (the second one
        stopping the chain with action="none")."""
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            side_effect=[
                _reply(
                    '{"reply": "ok", "grounded": true, "action": "create_watchlist", '
                    '"action_watchlist": "Tech"}'
                ),
                _reply(
                    '{"reply": "ok", "grounded": true, "action": "add_to_watchlist", '
                    '"action_symbol": "NVDA", "action_watchlist": "Tech"}'
                ),
                _reply('{"reply": "ok", "grounded": true}'),
            ]
        )

        msg, grounded, *_ = answer_chat_message(
            self.session_id,
            "create a watchlist called Tech and add NVDA to it",
        )

        self.assertIn("Tech", msg.content)
        self.assertIn("NVDA", msg.content)
        self.assertEqual(mock_ai.complete.await_count, 3)
        db = self.Session()
        try:
            wl = WatchlistRepository(db).get_watchlist_by_name("Tech")
            self.assertIsNotNone(wl)
            self.assertIsNotNone(WatchlistRepository(db).get_watchlist_symbol(wl.id, "NVDA"))
        finally:
            db.close()

    @patch("backend.ai.chat.ai_manager")
    def test_delete_alert_end_to_end_confirm_then_execute(self, mock_ai):
        db = self.Session()
        alert = AlertRepository(db).create("A", "NVDA", "price_above", "220")
        alert_id = alert.id
        db.close()

        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "Delete the NVDA alert?", "grounded": true, '
                '"action": "delete_alert", "action_target_id": ' + str(alert_id) + "}"
            )
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
    def test_unanswered_confirmation_expires_after_one_turn(self, mock_ai):
        """A later, unrelated "ok" must not execute an old destructive request."""
        db = self.Session()
        alert = AlertRepository(db).create("A", "NVDA", "price_above", "220")
        alert_id = alert.id
        db.close()

        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "Delete the NVDA alert?", "grounded": true, '
                '"action": "delete_alert", "action_target_id": ' + str(alert_id) + "}"
            )
        )
        msg1, *_ = answer_chat_message(self.session_id, "delete my nvda alert")
        self.assertIn("confirm", msg1.content.lower())

        mock_ai.complete.return_value = _reply('{"reply": "Sure, anything else?", "grounded": false}')
        answer_chat_message(self.session_id, "never mind, what can you do?")
        answer_chat_message(self.session_id, "ok thanks")

        db = self.Session()
        try:
            self.assertIsNotNone(AlertRepository(db).get_by_id(alert_id))
            state = json.loads(db.get(ChatSession, self.session_id).planner_state)
            self.assertIsNone(state["pending_confirmation"])
        finally:
            db.close()

    @patch("backend.ai.chat.ai_manager")
    def test_save_to_journal_end_to_end_confirm_then_execute(self, mock_ai):
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "I can save that plan.", "grounded": true, '
                '"action": "save_to_journal", "action_confirmed": true, '
                '"action_tool_arguments": {"entry": {"symbol": "AAPL", '
                '"status": "planned", "entry_price": 200, "stop_price": 190, '
                '"target_price": 220, "notes": "breakout plan"}}}'
            )
        )

        msg1, grounded1, *_ = answer_chat_message(self.session_id, "save this AAPL plan")
        self.assertTrue(grounded1)
        self.assertIn("confirm", msg1.content.lower())
        self.assertIn("AAPL", msg1.content)

        # The confirmation turn may omit the action or fail to set the model
        # flag; the server-owned pending state is what authorizes the save.
        mock_ai.complete.return_value = _reply(
            '{"reply": "Yes.", "grounded": true, "action": "none"}'
        )
        msg2, grounded2, *_ = answer_chat_message(self.session_id, "yes")
        self.assertTrue(grounded2)
        self.assertIn("AAPL", msg2.content)

    @patch("backend.ai.chat.ai_manager")
    def test_run_screen_end_to_end_populates_focus(self, mock_ai):
        """The regression this was built for: a screen surfaces tickers
        the trader's own message never named (resolve_turn_symbols is
        patched to [] in this class's setUp), so turn.focus alone would
        be empty — the screened symbols must still reach the returned
        `focus` list, or the frontend's per-ticker quick-action buttons
        (add to watchlist / create alert) never render for them.
        """
        from backend.nl_search.executor import ExecutionResult
        from backend.nl_search.schema import ScannedResultItem

        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "checking", "grounded": true, "action": "run_screen", '
                '"action_query": "oversold"}'
            )
        )
        result = ExecutionResult(
            matched_all=[],
            top_n=[ScannedResultItem(symbol="AAPL", total_score=62.0, rank=1, signals=[])],
            filter_description="RSI oversold",
            universe_size=5,
            matched_count=1,
        )
        with (
            patch(
                "backend.nl_search.parser.parse_query", return_value=(MagicMock(), None, "rules")
            ),
            patch("backend.nl_search.executor.execute_query", return_value=result),
        ):
            msg, grounded, focus, partial, unavailable = answer_chat_message(
                self.session_id,
                "screen my watchlist for oversold names",
            )

        self.assertTrue(grounded)
        self.assertIn("AAPL", msg.content)
        self.assertIn("AAPL", focus)

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_analyze_named_watchlist_resolves_real_context(self, mock_ctx, mock_ai):
        """Regression test for the reported bug: asking to analyze a
        specific, real, named watchlist ('Analyze "My Watch" watchlist')
        got a flat "I don't have visibility into the contents of your
        watchlist" decline, even though the app has the real member
        list one query away. resolve_turn_symbols is patched to always
        return [] in this class's setUp (simulating "the message named
        no ticker via the normal text-resolution path"), so this proves
        the named-watchlist symbols are what actually populate the
        turn's context — not a leftover from some other resolution path.
        """
        db = self.Session()
        wl = WatchlistRepository(db).create_watchlist("My Watch")
        WatchlistRepository(db).add_symbol_to_watchlist(wl.id, "DVLT")
        db.close()

        mock_ctx.return_value.compact.return_value = {
            "price": 0.16,
            "trend_state": {"direction": "up"},
            "momentum": {"rsi": 55},
        }
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "DVLT is trending up on light volume.", "grounded": true}'
            )
        )

        msg, grounded, focus, partial, unavailable = answer_chat_message(
            self.session_id,
            'Analyze "My Watch" watchlist',
        )

        self.assertTrue(grounded)
        self.assertIn("DVLT", msg.content)
        self.assertIn("DVLT", focus)
        mock_ctx.assert_called_once()
        self.assertEqual(mock_ctx.call_args.args[0], "DVLT")

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

        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "Which watchlist would you like to delete?", "grounded": false, '
                '"action": "none"}'
            )
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

        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000

        msg, grounded, *_ = answer_chat_message(self.session_id, "how many watchlist i have")
        self.assertIn("Watch1", msg.content)
        self.assertIn("AAPL", msg.content)
        mock_ai.complete.assert_not_called()

    @patch("backend.ai.chat.ai_manager")
    def test_named_watchlist_contents_answered_without_asking_the_model(self, mock_ai):
        # Reproduces the live 2026-09-12 bug report verbatim: two real
        # watchlists exist ("Default" and "Market Context"), and asking
        # about the "Market Context" one specifically must answer with
        # only ITS symbols — not the union of both, which is what the
        # model fabricated when it had no watchlist data at all.
        db = self.Session()
        repo = WatchlistRepository(db)
        default = repo.create_watchlist("Default")
        for sym in ("SPY", "CTNT", "CYN", "DVLT", "AAPL", "MSFT"):
            repo.add_symbol_to_watchlist(default.id, sym)
        mc = repo.create_watchlist("Market Context")
        for sym in ("SPY", "QQQ", "IWM", "VIXY"):
            repo.add_symbol_to_watchlist(mc.id, sym)
        db.close()

        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000

        msg, grounded, *_ = answer_chat_message(
            self.session_id, "What tickers are in Market Context"
        )
        for sym in ("SPY", "QQQ", "IWM", "VIXY"):
            self.assertIn(sym, msg.content)
        for sym in ("CTNT", "CYN", "DVLT", "AAPL", "MSFT"):
            self.assertNotIn(sym, msg.content)
        mock_ai.complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestMaterialChange(unittest.TestCase):
    def _turn(self, mode):
        from backend.ai.chat import _Turn

        return _Turn(
            symbol_blocks=[], unavailable=[], market_baseline=None, transcript=[], user_content="q",
            alert_context=None, capped=False, base=[], planner_state={},
            regeneration_mode=mode, previous_evidence_fingerprint="old",
        )

    def test_new_question_with_different_evidence_is_not_a_material_change(self):
        from backend.ai.chat import _material_change

        self.assertFalse(_material_change(self._turn(None), "new"))

    def test_regeneration_with_different_evidence_is_a_material_change(self):
        from backend.ai.chat import _material_change

        self.assertTrue(_material_change(self._turn("refresh"), "new"))
        self.assertFalse(_material_change(self._turn("refresh"), "old"))


class TestPositionRiskParsing(unittest.TestCase):
    def test_labelled_fields_become_a_position_risk_request(self):
        from backend.ai.chat import _position_risk_calculation

        request = _position_risk_calculation("Buy 200 AAPL at $220, stop $212. What's my risk?")
        self.assertEqual(
            (request.calculation, request.shares, request.entry_price, request.stop_price),
            ("position_risk", 200, 220, 212),
        )
        request = _position_risk_calculation("300 shares at 50 with a stop at 47, target 60, account 25,000")
        self.assertEqual((request.entry_price, request.stop_price, request.target_price, request.account_value), (50, 47, 60, 25000))

    def test_missing_label_is_not_guessed(self):
        from backend.ai.chat import _position_risk_calculation

        self.assertIsNone(_position_risk_calculation("I have 100 shares of AAPL, how is it doing?"))
        self.assertIsNone(_position_risk_calculation("what if I buy AAPL at 220?"))

    def test_followup_overrides_only_named_inputs(self):
        from backend.ai.chat import _calculation_followup, _position_risk_calculation

        prior = _position_risk_calculation("Buy 200 AAPL at $220, stop $212").model_dump()
        request = _calculation_followup("Use the same stop but 100 shares", prior)
        self.assertEqual((request.shares, request.entry_price, request.stop_price), (100, 220, 212))
        self.assertIsNone(_calculation_followup("is it still above the stop at 212?", prior))
        self.assertIsNone(_calculation_followup("same thing but 100 shares", {"calculation": "percentage_change", "old_value": 1, "new_value": 2}))
