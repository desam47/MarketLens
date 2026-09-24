"""
Tests for backend.ai.chat.answer_chat_message — universal AI Hub chat.

answer_chat_message() returns (message, grounded, focus, partial, unavailable)
and must never raise for an expected failure mode: AI off, a per-symbol
context-building failure, or a malformed AI reply all produce a stored
assistant message rather than an exception.

Fresh in-memory SQLite per test (same convention as
test_bar_repository.py) so this never touches the real marketlens.db;
resolve_turn_symbols / build_market_baseline / build_context are patched
so no network or scanner state is needed.
"""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.ai.chat import (
    _TURN_BROWSER_DATA,
    _browser_preset_for_query,
    _browser_safe_reply_data,
    _format_browser_local_reply,
    _generate_reply,
    _prune_context,
    answer_chat_message,
)
from backend.ai.context import InsufficientDataError
from backend.ai.prompt import AnalysisResponse, TradePlan, UncertaintyResponse
from backend.ai.provider import AIResponse
from backend.models import Alert, AlertTrigger, ChatMessage, ChatSession

WARM_CTX = {"price": 150.0, "trend_state": {"direction": "up"}, "momentum": {"rsi": 55}}
COLD_CTX = {
    "price": 12.0,
    "momentum": {"rsi": 60},
    "market_structure": {"score": 7},
    "trend_transition": {"delta": 3},
}


def _reply(text='{"reply": "ok", "grounded": true}'):
    return AIResponse(text=f"```json\n{text}\n```", provider="ollama", model="llama3.2")


def _astream(chunks):
    """Return an already-started async generator yielding *chunks*.

    ai_manager.stream is an async generator function and chat.py drives the
    call result through stream_sync(), so the stub must hand back a real
    async iterator (not iter([...]), which has no __anext__).
    """

    async def _gen():
        for c in chunks:
            yield c

    return _gen()


class _Base(unittest.TestCase):
    def setUp(self):
        # chat.py now keeps a 12s per-symbol context cache — reset it so
        # a patched build_context isn't shadowed by an earlier test's result
        from backend.ai import chat as _chat_mod

        _chat_mod._ctx_cache.clear()

        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        for model in (ChatSession, ChatMessage, Alert, AlertTrigger):
            model.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        p = patch("backend.repositories.chat_repository.SessionLocal", self.Session)
        p.start()
        self.addCleanup(p.stop)

        # Default stubs — each test overrides what it needs.
        self.mock_resolve = patch(
            "backend.ai.chat.resolve_turn_symbols", return_value=([], False)
        ).start()
        self.addCleanup(patch.stopall)
        self.mock_baseline = patch(
            "backend.ai.chat.build_market_baseline",
            return_value={"regime_live": {"regime": "risk_on"}},
        ).start()

        self.db = self.Session()
        self.session = self._make_session(symbol="AAPL")

    def _make_session(self, **kw):
        s = ChatSession(**kw)
        self.db.add(s)
        self.db.commit()
        self.db.refresh(s)
        return s

    def _stored(self, session_id):
        return (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
            .all()
        )

    def tearDown(self):
        self.db.close()
        self.engine.dispose()


class TestUniversalTurn(_Base):
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_full_round_trip_persists_both_messages(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply('{"reply": "AAPL up.", "grounded": true}'))

        msg, grounded, focus, partial, unavailable = answer_chat_message(
            self.session.id, "how's AAPL"
        )

        self.assertTrue(grounded)
        self.assertEqual(focus, ["AAPL"])
        self.assertEqual(unavailable, [])
        self.assertEqual(msg.content, "AAPL up.")
        self.assertEqual([m.role for m in self._stored(self.session.id)], ["user", "assistant"])
        self.db.expire_all()
        stored_session = self.db.query(ChatSession).filter(ChatSession.id == self.session.id).one()
        state = json.loads(stored_session.planner_state)
        self.assertEqual(state["current_symbols"], ["AAPL"])
        self.assertEqual(state["last_user_question"], "how's AAPL")

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_no_ticker_turn_is_market_only(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply('{"reply": "Risk-on tape.", "grounded": true}')
        )

        msg, grounded, focus, partial, unavailable = answer_chat_message(
            self.session.id, "what should I know?"
        )

        mock_ctx.assert_not_called()
        self.assertEqual(focus, [])
        prompt = mock_ai.complete.call_args.kwargs["prompt"]
        self.assertIn("<market>", prompt)
        self.assertNotIn("<context ", prompt)
        self.assertIn("No ticker resolved for this turn", prompt)

    @patch("backend.ai.chat.build_context")
    @patch("backend.ai.chat.ai_manager")
    def test_ambiguous_reference_asks_for_clarification(self, mock_ctx, mock_ai):
        mock_ai.enabled = True
        reply, grounded, screened = _generate_reply(
            self.db,
            [],
            [],
            None,
            [],
            "what about it?",
            None,
            False,
            [],
            {"current_symbols": ["AAPL", "MSFT"]},
        )
        self.assertIn("AAPL", reply)
        self.assertIn("MSFT", reply)
        self.assertFalse(grounded)
        self.assertEqual(screened, [])
        mock_ai.complete.assert_not_called()

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_chat_model_override_is_passed_to_ai_call(self, mock_ctx, mock_ai):
        """AISettings.chat_model (AI_CHAT_MODEL) routes the chat completion
        through that specific chain entry (ai_manager.complete's own
        `model` param — see its docstring) without touching the default
        chain every other AI feature uses."""
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_model = "openai_compatible:auto/best-free"
        mock_ai.complete = AsyncMock(return_value=_reply('{"reply": "ok", "grounded": true}'))

        answer_chat_message(self.session.id, "what should I know?")

        self.assertEqual(
            mock_ai.complete.call_args.kwargs["model"],
            "openai_compatible:auto/best-free",
        )

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_no_chat_model_override_passes_none(self, mock_ctx, mock_ai):
        """Empty AI_CHAT_MODEL (the default) means no override — chat uses
        the same default chain as every other AI feature."""
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_model = ""
        mock_ai.complete = AsyncMock(return_value=_reply('{"reply": "ok", "grounded": true}'))

        answer_chat_message(self.session.id, "what should I know?")

        self.assertIsNone(mock_ai.complete.call_args.kwargs["model"])

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_two_tickers_two_blocks(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL", "MSFT"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply())

        answer_chat_message(self.session.id, "tell me about AAPL and MSFT")

        self.assertEqual(mock_ctx.call_count, 2)
        # multi-ticker turn: aux data off
        for call in mock_ctx.call_args_list:
            self.assertFalse(call.kwargs["include_news"])
        prompt = mock_ai.complete.call_args.kwargs["prompt"]
        self.assertIn('<context symbol="AAPL"', prompt)
        self.assertIn('<context symbol="MSFT"', prompt)

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_cold_ticker_goes_unavailable_not_abort(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["RIVN"], False)
        mock_ctx.side_effect = InsufficientDataError("no data for RIVN")
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply('{"reply": "No engine for RIVN.", "grounded": false}')
        )

        msg, grounded, focus, partial, unavailable = answer_chat_message(
            self.session.id, "what about RIVN"
        )

        self.assertEqual(unavailable, ["RIVN"])
        self.assertEqual(focus, [])
        self.assertFalse(grounded)
        self.assertIn(
            "<unavailable_symbols>RIVN</unavailable_symbols>",
            mock_ai.complete.call_args.kwargs["prompt"],
        )

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_unavailable_comparison_returns_clear_data_message(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL", "MSFT"], False)
        mock_ctx.side_effect = [
            InsufficientDataError("no data for AAPL"),
            InsufficientDataError("no data for MSFT"),
        ]
        mock_ai.is_available = AsyncMock(return_value=True)

        msg, grounded, focus, partial, unavailable = answer_chat_message(
            self.session.id, "Compare AAPL and MSFT"
        )

        self.assertEqual(msg.content, "I don't have enough verified data for AAPL, MSFT to compare them.")
        self.assertFalse(grounded)
        self.assertEqual(focus, [])
        self.assertEqual(partial, [])
        self.assertEqual(unavailable, ["AAPL", "MSFT"])
        mock_ai.complete.assert_not_called()

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_cold_engine_block_marked_and_pruned(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["RIVN"], False)
        mock_ctx.return_value.compact.return_value = dict(COLD_CTX)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply())

        answer_chat_message(self.session.id, "RIVN price?")

        prompt = mock_ai.complete.call_args.kwargs["prompt"]
        self.assertIn('engine_warm="false"', prompt)
        self.assertNotIn("market_structure", prompt)
        self.assertNotIn("trend_transition", prompt)
        self.assertIn('"price":12.0', prompt)  # raw price still there

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_mixed_available_and_unavailable(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL", "RIVN"], False)
        mock_ctx.side_effect = [
            MagicMock(compact=MagicMock(return_value=WARM_CTX)),
            InsufficientDataError("no RIVN"),
        ]
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply())

        msg, grounded, focus, partial, unavailable = answer_chat_message(
            self.session.id, "AAPL vs RIVN"
        )

        self.assertEqual(focus, ["AAPL"])
        self.assertEqual(unavailable, ["RIVN"])
        self.assertFalse(grounded)  # any unavailable -> not grounded
        prompt = mock_ai.complete.call_args.kwargs["prompt"]
        self.assertIn('<context symbol="AAPL"', prompt)

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_capped_note_added(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL", "MSFT", "NVDA"], True)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply())

        answer_chat_message(self.session.id, "tell me about five things")

        self.assertIn(
            "more tickers than I can dig into", mock_ai.complete.call_args.kwargs["prompt"]
        )


class TestDegradeContract(_Base):
    @patch("backend.ai.chat.ai_manager")
    def test_ai_disabled_stores_message(self, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.enabled = False
        mock_ai.is_available = AsyncMock(return_value=False)
        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")
        self.assertTrue(grounded)
        self.assertIn("unavailable", msg.content.lower())

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_malformed_reply_degrades(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=AIResponse(text="not json", provider="ollama", model="x")
        )
        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")
        self.assertFalse(grounded)

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_ai_exception_degrades(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(side_effect=RuntimeError("provider down"))
        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")
        self.assertFalse(grounded)

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_malformed_reply_retries_once_then_succeeds(self, mock_ctx, mock_ai):
        # 2026-09-16: this local model has shown intermittent malformed
        # JSON where an immediate identical retry succeeds — one retry
        # should turn that into a normal grounded answer instead of
        # "I couldn't process that".
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            side_effect=[
                AIResponse(text="not json", provider="ollama", model="x"),
                _reply('{"reply": "AAPL looks fine.", "grounded": true}'),
            ]
        )
        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")
        self.assertTrue(grounded)
        self.assertEqual(msg.content, "AAPL looks fine.")
        self.assertEqual(mock_ai.complete.call_count, 2)

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_malformed_reply_gives_up_after_retry_exhausted(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=AIResponse(text="not json", provider="ollama", model="x")
        )
        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")
        self.assertFalse(grounded)
        self.assertIn("couldn't process", msg.content.lower())
        self.assertEqual(mock_ai.complete.call_count, 2)  # first attempt + 1 retry, no more

    def test_unknown_session_raises(self):
        with self.assertRaises(ValueError):
            answer_chat_message(999999, "hi")

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_legacy_symbol_session_friendly_degrade(self, mock_ctx, mock_ai):
        """scope='symbol' session whose only ticker has no data keeps the
        pre-universal wording (not a generic market answer)."""
        s = self._make_session(symbol="AAPL", scope="symbol")
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.side_effect = InsufficientDataError("no data")
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000

        msg, grounded, focus, partial, unavailable = answer_chat_message(s.id, "what's the RSI?")

        self.assertFalse(grounded)
        self.assertIn("don't have enough data on AAPL", msg.content)
        mock_ai.complete.assert_not_called()


class TestTurnFailureStillPersistsOneReply(_Base):
    """BF-11: once the user row is saved, both transports end in exactly
    one persisted assistant row, even when generation or finishing raises."""

    def _roles(self):
        return [m.role for m in self._stored(self.session.id)]

    @patch("backend.ai.chat._generate_reply", side_effect=RuntimeError("boom"))
    def test_blocking_generation_exception(self, _gen):
        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")

        self.assertFalse(grounded)
        self.assertEqual(msg.content, "Something went wrong answering that — please try again.")
        self.assertEqual(self._roles(), ["user", "assistant"])

    @patch("backend.ai.chat.verify_answer", side_effect=RuntimeError("verifier crashed"))
    @patch("backend.ai.chat.ai_manager")
    def test_blocking_finalization_exception(self, mock_ai, _verify):
        mock_ai.enabled = False

        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")

        self.assertFalse(grounded)
        self.assertIn("couldn't finish verifying", msg.content)
        self.assertEqual(self._roles(), ["user", "assistant"])

    @patch("backend.ai.chat._generate_reply_streaming", side_effect=RuntimeError("boom"))
    def test_streaming_generation_exception(self, _gen):
        from backend.ai.chat import stream_chat_message

        events = list(stream_chat_message(self.session.id, "hi"))

        self.assertEqual(events[-1][0], "final")
        msg, grounded, *_ = events[-1][1]
        self.assertFalse(grounded)
        self.assertEqual(msg.content, "Something went wrong answering that — please try again.")
        self.assertEqual(self._roles(), ["user", "assistant"])


class TestAlertContext(_Base):
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_alert_facts_reach_prompt(self, mock_ctx, mock_ai):
        alert = Alert(name="T", symbol="AAPL", condition_type="price_above", parameter="100")
        self.db.add(alert)
        self.db.commit()
        trig = AlertTrigger(
            alert_id=alert.id,
            symbol="AAPL",
            message="AAPL crossed 100",
            ai_commentary="A bullish breakout.",
        )
        self.db.add(trig)
        self.db.commit()
        s = self._make_session(symbol="AAPL", alert_trigger_id=trig.id, scope="alert")

        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply())

        answer_chat_message(s.id, "explain this alert")

        prompt = mock_ai.complete.call_args.kwargs["prompt"]
        self.assertIn("AAPL crossed 100", prompt)
        self.assertIn("A bullish breakout.", prompt)


class TestReanalysisTool(_Base):
    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_single_symbol_reanalysis_runs(self, mock_ctx, mock_ai, mock_analyze):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "Let me check.", "grounded": true, "wants_reanalysis": true}'
            )
        )
        mock_analyze.return_value = AnalysisResponse(
            summary="Strong momentum.", trend="bullish", confidence=0.82
        )

        msg, grounded, *_ = answer_chat_message(self.session.id, "re-run the analysis")

        mock_analyze.assert_called_once_with("AAPL")
        self.assertTrue(grounded)
        self.assertIn("bullish", msg.content)
        self.assertIn("82%", msg.content)

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_reanalysis_surfaces_trade_plan(self, mock_ctx, mock_ai, mock_analyze):
        # A fresh analyze_symbol() run (advisory=True by default) normally
        # carries a trade_plan — the reply must not silently drop it.
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "Let me check.", "grounded": true, "wants_reanalysis": true}'
            )
        )
        mock_analyze.return_value = AnalysisResponse(
            summary="Strong momentum.",
            trend="bullish",
            confidence=0.82,
            trade_plan=TradePlan(
                recommendation="buy",
                conviction="high",
                time_horizon="swing",
                entry_zone_low=218.5,
                entry_zone_high=220.0,
                stop_loss=212.0,
                targets=[228.0, 235.5],
                thesis="Reclaimed the 20d SMA with rising volume.",
                invalidation="Close below 212 invalidates the setup.",
            ),
        )

        msg, grounded, *_ = answer_chat_message(self.session.id, "give me the full read")

        self.assertTrue(grounded)
        self.assertIn("BUY", msg.content)
        self.assertIn("218.5", msg.content)
        self.assertIn("212", msg.content)
        self.assertIn("228", msg.content)
        self.assertIn("Reclaimed the 20d SMA", msg.content)

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_multi_symbol_reanalysis_without_target_is_rejected(
        self, mock_ctx, mock_ai, mock_analyze
    ):
        self.mock_resolve.return_value = (["AAPL", "MSFT"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply('{"reply": "checking", "grounded": true, "wants_reanalysis": true}')
        )

        msg, grounded, *_ = answer_chat_message(self.session.id, "re-run it")

        mock_analyze.assert_not_called()
        self.assertIn("Which ticker", msg.content)

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_multi_symbol_reanalysis_with_target_runs(self, mock_ctx, mock_ai, mock_analyze):
        self.mock_resolve.return_value = (["AAPL", "MSFT"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply(
                '{"reply": "x", "grounded": true, "wants_reanalysis": true, '
                '"reanalysis_symbol": "MSFT"}'
            )
        )
        mock_analyze.return_value = AnalysisResponse(
            summary="Neutral and range-bound.", trend="neutral", confidence=0.5
        )

        answer_chat_message(self.session.id, "re-run MSFT officially")
        mock_analyze.assert_called_once_with("MSFT")

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_reanalysis_uncertainty_is_ungrounded(self, mock_ctx, mock_ai, mock_analyze):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(
            return_value=_reply('{"reply": "x", "grounded": true, "wants_reanalysis": true}')
        )
        mock_analyze.return_value = UncertaintyResponse(summary="AI analysis is disabled")

        msg, grounded, *_ = answer_chat_message(self.session.id, "re-run the analysis")
        self.assertFalse(grounded)

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_ordinary_reply_does_not_invoke_tool(self, mock_ctx, mock_ai, mock_analyze):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply('{"reply": "up.", "grounded": true}'))

        msg, *_ = answer_chat_message(self.session.id, "how's it doing?")
        mock_analyze.assert_not_called()
        self.assertEqual(msg.content, "up.")


class TestTurnIntent(_Base):
    """News / fundamentals / baseline are pulled only when the turn asks."""

    def _wire(self, mock_ctx, mock_ai, symbols=("AAPL",)):
        self.mock_resolve.return_value = (list(symbols), False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply())

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_plain_ticker_question_skips_news_and_fundamentals(self, mock_ctx, mock_ai):
        self._wire(mock_ctx, mock_ai)
        answer_chat_message(self.session.id, "how's AAPL trending")
        kw = mock_ctx.call_args.kwargs
        self.assertFalse(kw["include_news"])
        self.assertFalse(kw["include_fundamentals"])

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_news_question_pulls_news(self, mock_ctx, mock_ai):
        self._wire(mock_ctx, mock_ai)
        answer_chat_message(self.session.id, "any news on AAPL? why is it up")
        self.assertTrue(mock_ctx.call_args.kwargs["include_news"])

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_valuation_question_pulls_fundamentals(self, mock_ctx, mock_ai):
        self._wire(mock_ctx, mock_ai)
        answer_chat_message(self.session.id, "what's AAPL's P/E and revenue growth")
        self.assertTrue(mock_ctx.call_args.kwargs["include_fundamentals"])

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_focused_ticker_turn_omits_market_baseline(self, mock_ctx, mock_ai):
        self._wire(mock_ctx, mock_ai)
        answer_chat_message(self.session.id, "where is AAPL support")
        self.mock_baseline.assert_not_called()
        self.assertNotIn("<market>", mock_ai.complete.call_args.kwargs["prompt"])

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_market_intent_attaches_baseline(self, mock_ctx, mock_ai):
        self._wire(mock_ctx, mock_ai)
        answer_chat_message(self.session.id, "how does AAPL look vs the broader market")
        self.mock_baseline.assert_called()
        self.assertIn("<market>", mock_ai.complete.call_args.kwargs["prompt"])


class TestContextCache(_Base):
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_second_turn_same_symbol_served_from_cache(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply())

        answer_chat_message(self.session.id, "how's AAPL")
        answer_chat_message(self.session.id, "and the trend on AAPL")
        self.assertEqual(mock_ctx.call_count, 1)  # 2nd turn hit the cache


class TestStreamChatMessage(_Base):
    """stream_chat_message yields ('meta', ...) then N ('delta', ...) then
    one ('final', (msg, grounded, focus, partial, unavailable))."""

    def _drain(self, session_id, content):
        from backend.ai.chat import stream_chat_message

        return list(stream_chat_message(session_id, content))

    @patch("backend.ai.chat.ai_manager")
    def test_streams_deltas_then_final(self, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = True
        mock_ai.stream.return_value = _astream(
            [
                '```json\n{"reply": "The market ',
                "looks calm",
                '.", "grounded": true}\n```',
            ]
        )

        events = self._drain(self.session.id, "what should I know?")
        kinds = [e[0] for e in events]
        self.assertEqual(kinds[0], "meta")
        self.assertIn("delta", kinds)
        self.assertEqual(kinds[-1], "final")

        deltas = "".join(p for k, p in events if k == "delta")
        self.assertEqual(deltas, "The market looks calm.")
        msg, grounded, focus, partial, unavailable = events[-1][1]
        self.assertEqual(msg.role, "assistant")
        self.assertEqual(msg.content, "The market looks calm.")
        self.assertTrue(grounded)
        # persisted exactly once
        from backend.repositories.chat_repository import ChatRepository

        repo = ChatRepository()
        try:
            rows = repo.get_messages(self.session.id, 50)
        finally:
            repo.close()
        self.assertEqual([r.content for r in rows][-1], "The market looks calm.")

    @patch("backend.ai.chat.verify_answer", side_effect=RuntimeError("verifier crashed"))
    @patch("backend.ai.chat.ai_manager")
    def test_finalization_failure_still_persists_one_final_message(self, mock_ai, _verify):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = False
        mock_ai.complete = AsyncMock(return_value=_reply('{"reply": "one shot", "grounded": true}'))

        events = self._drain(self.session.id, "what should I know?")

        self.assertEqual(events[-1][0], "final")
        msg, grounded, *_ = events[-1][1]
        self.assertFalse(grounded)
        self.assertIn("couldn't finish verifying", msg.content)
        from backend.repositories.chat_repository import ChatRepository

        repo = ChatRepository()
        try:
            rows = repo.get_messages(self.session.id, 50)
        finally:
            repo.close()
        self.assertEqual([r.role for r in rows], ["user", "assistant"])

    @patch("backend.ai.chat.ai_manager")
    def test_non_streaming_mode_emits_one_delta(self, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = False
        mock_ai.complete = AsyncMock(return_value=_reply('{"reply": "one shot", "grounded": true}'))

        events = self._drain(self.session.id, "what should I know?")
        mock_ai.stream.assert_not_called()
        self.assertEqual([p for k, p in events if k == "delta"], ["one shot"])
        self.assertEqual(events[-1][1][0].content, "one shot")

    @patch("backend.ai.chat.ai_manager")
    def test_non_streaming_mode_retries_malformed_reply(self, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = False
        mock_ai.complete = AsyncMock(
            side_effect=[
                AIResponse(text="not json", provider="ollama", model="x"),
                _reply('{"reply": "one shot", "grounded": true}'),
            ]
        )

        events = self._drain(self.session.id, "what should I know?")
        self.assertEqual(mock_ai.complete.call_count, 2)
        msg, grounded, *_ = events[-1][1]
        self.assertTrue(grounded)
        self.assertEqual(msg.content, "one shot")

    @patch("backend.ai.chat.ai_manager")
    def test_streaming_mode_retries_malformed_reply(self, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = True
        mock_ai.stream.side_effect = [
            _astream(["not json at all"]),
            _astream(['```json\n{"reply": "recovered", "grounded": true}\n```']),
        ]

        events = self._drain(self.session.id, "what should I know?")
        self.assertEqual(mock_ai.stream.call_count, 2)
        msg, grounded, *_ = events[-1][1]
        self.assertTrue(grounded)
        self.assertEqual(msg.content, "recovered")

    @patch("backend.ai.chat.ai_manager")
    def test_ai_off_still_produces_final(self, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.enabled = False
        mock_ai.is_available = AsyncMock(return_value=False)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = True

        events = self._drain(self.session.id, "what should I know?")
        self.assertEqual(events[-1][0], "final")
        msg, grounded, *_ = events[-1][1]
        self.assertTrue(grounded)
        self.assertIn("unavailable", msg.content.lower())

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.build_context")
    @patch("backend.ai.chat.ai_manager")
    def test_reanalysis_final_overrides_streamed_text(self, mock_ai, mock_ctx, mock_analyze):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = True
        mock_ai.stream.return_value = _astream(
            [
                '{"reply": "hold on", "grounded": false, ',
                '"wants_reanalysis": true, "reanalysis_symbol": "AAPL"}',
            ]
        )
        mock_analyze.return_value = AnalysisResponse(
            symbol="AAPL",
            trend="bullish",
            confidence=0.8,
            summary="Fresh run done.",
        )

        events = self._drain(self.session.id, "re-run the full analysis on AAPL")
        final_msg = events[-1][1][0]
        self.assertIn("re-ran the analysis for AAPL", final_msg.content)
        self.assertNotEqual(final_msg.content, "hold on")


class TestTranscriptClip(_Base):
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_long_prior_message_is_clipped(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete = AsyncMock(return_value=_reply())
        from backend.repositories.chat_repository import ChatRepository

        repo = ChatRepository()
        try:
            repo.add_message(self.session.id, "user", "X" * 5000)
        finally:
            repo.close()

        answer_chat_message(self.session.id, "and now")
        prompt = mock_ai.complete.call_args.kwargs["prompt"]
        self.assertIn("…[truncated]", prompt)
        self.assertNotIn("X" * 1000, prompt)


class TestBrowserLocalChatData(unittest.TestCase):
    def test_persisted_reply_uses_aggregate_browser_data_only(self):
        payload = _browser_safe_reply_data(
            "get_risk_dashboard",
            {
                "available": True,
                "positions": [{"symbol": "PRIVATE1"}],
                "gross_exposure": 1000,
                "price_basis": "entry_price fallback",
            },
        )
        serialized = json.dumps(payload)
        self.assertNotIn("PRIVATE1", serialized)
        self.assertEqual(payload["position_count"], 1)
        self.assertEqual(payload["gross_exposure"], 1000)

    def test_browser_local_replies_are_human_readable_and_row_free(self):
        risk = _format_browser_local_reply(
            "get_risk_dashboard",
            _browser_safe_reply_data(
                "get_risk_dashboard",
                {
                    "available": True,
                    "positions": [{"symbol": "PRIVATE1"}],
                    "gross_exposure": 1000,
                    "net_exposure": 900,
                    "stop_loss_risk": 100,
                    "price_basis": "entry_price fallback used where current_price was not supplied",
                },
            ),
            "MarketLens calculator",
        )
        journal = _format_browser_local_reply(
            "get_trade_journal",
            _browser_safe_reply_data(
                "get_trade_journal",
                {"available": True, "total_entries": 1, "closed_entries": 1},
            ),
            "MarketLens local journal",
        )
        scans = _format_browser_local_reply(
            "get_saved_scans",
            _browser_safe_reply_data(
                "get_saved_scans",
                {"available": True, "presets": [{"name": "PRIVATE"}]},
            ),
            "MarketLens local scanner presets",
        )

        self.assertIn("gross exposure is", risk)
        self.assertIn("one position", risk)
        self.assertIn("$1,000.00", risk)
        self.assertNotIn("PRIVATE1", risk)
        self.assertNotIn("{", risk)
        self.assertIn("Your trade journal has 1 entry, and it's closed", journal)
        self.assertIn("You have 1 saved Scanner preset.", scans)

    def test_shared_preset_is_selected_only_by_explicit_name_or_scope(self):
        token = _TURN_BROWSER_DATA.set({
            "scan_presets": [{"name": "Breakout", "filters": [], "match": "AND"}],
        })
        try:
            self.assertEqual(_browser_preset_for_query("run my Breakout preset")["name"], "Breakout")
            self.assertIsNone(_browser_preset_for_query("what is the market doing today?"))
        finally:
            _TURN_BROWSER_DATA.reset(token)


class TestPruneContextStatsGating(unittest.TestCase):
    """historical_signal_stats and track_record are both verbose
    'stats' sections — dropped unless the turn asked about them
    (keep_stats), same as the rest of _prune_context's trimming."""

    def _ctx(self, **extra):
        base = {
            "price": 150.0,
            "historical_signal_stats": {"total": 10},
            "track_record": {"sample_size": 3, "win_rate": 0.67},
        }
        base.update(extra)
        return base

    def test_both_dropped_by_default(self):
        out = _prune_context(self._ctx(), {"engine_warm": True}, keep_stats=False)
        self.assertNotIn("historical_signal_stats", out)
        self.assertNotIn("track_record", out)
        self.assertIn("price", out)

    def test_both_kept_when_turn_asked(self):
        out = _prune_context(self._ctx(), {"engine_warm": True}, keep_stats=True)
        self.assertEqual(out["historical_signal_stats"], {"total": 10})
        self.assertEqual(out["track_record"], {"sample_size": 3, "win_rate": 0.67})

    def test_empty_track_record_dropped_either_way(self):
        out = _prune_context(self._ctx(track_record={}), {"engine_warm": True}, keep_stats=True)
        self.assertNotIn("track_record", out)  # empty-section rule applies first


if __name__ == "__main__":
    unittest.main()
