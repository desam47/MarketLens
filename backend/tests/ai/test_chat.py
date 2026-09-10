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
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.ai.chat import answer_chat_message
from backend.ai.context import InsufficientDataError
from backend.ai.prompt import AnalysisResponse, UncertaintyResponse
from backend.ai.provider import AIResponse
from backend.models import Alert, AlertTrigger, ChatMessage, ChatSession

WARM_CTX = {"price": 150.0, "trend_state": {"direction": "up"}, "momentum": {"rsi": 55}}
COLD_CTX = {"price": 12.0, "momentum": {"rsi": 60}, "market_structure": {"score": 7},
            "trend_transition": {"delta": 3}}


def _reply(text='{"reply": "ok", "grounded": true}'):
    return AIResponse(text=f"```json\n{text}\n```", provider="ollama", model="llama3.2")


class _Base(unittest.TestCase):
    def setUp(self):
        # chat.py now keeps a 12s per-symbol context cache — reset it so
        # a patched build_context isn't shadowed by an earlier test's result
        from backend.ai import chat as _chat_mod
        _chat_mod._ctx_cache.clear()

        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
        )
        for model in (ChatSession, ChatMessage, Alert, AlertTrigger):
            model.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        p = patch("backend.repositories.chat_repository.SessionLocal", self.Session)
        p.start()
        self.addCleanup(p.stop)

        # Default stubs — each test overrides what it needs.
        self.mock_resolve = patch(
            "backend.ai.chat.resolve_turn_symbols", return_value=([], False)).start()
        self.addCleanup(patch.stopall)
        self.mock_baseline = patch(
            "backend.ai.chat.build_market_baseline",
            return_value={"regime_live": {"regime": "risk_on"}}).start()

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
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply('{"reply": "AAPL up.", "grounded": true}')

        msg, grounded, focus, partial, unavailable = answer_chat_message(self.session.id, "how's AAPL")

        self.assertTrue(grounded)
        self.assertEqual(focus, ["AAPL"])
        self.assertEqual(unavailable, [])
        self.assertEqual(msg.content, "AAPL up.")
        self.assertEqual([m.role for m in self._stored(self.session.id)], ["user", "assistant"])

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_no_ticker_turn_is_market_only(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply('{"reply": "Risk-on tape.", "grounded": true}')

        msg, grounded, focus, partial, unavailable = answer_chat_message(self.session.id, "how's the market")

        mock_ctx.assert_not_called()
        self.assertEqual(focus, [])
        prompt = mock_ai.complete.call_args.kwargs["prompt"]
        self.assertIn("<market>", prompt)
        self.assertNotIn("<context ", prompt)
        self.assertIn("No ticker resolved for this turn", prompt)

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_two_tickers_two_blocks(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL", "MSFT"], False)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply()

        answer_chat_message(self.session.id, "compare AAPL and MSFT")

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
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply('{"reply": "No engine for RIVN.", "grounded": false}')

        msg, grounded, focus, partial, unavailable = answer_chat_message(self.session.id, "what about RIVN")

        self.assertEqual(unavailable, ["RIVN"])
        self.assertEqual(focus, [])
        self.assertFalse(grounded)
        self.assertIn("<unavailable_symbols>RIVN</unavailable_symbols>",
                      mock_ai.complete.call_args.kwargs["prompt"])

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_cold_engine_block_marked_and_pruned(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["RIVN"], False)
        mock_ctx.return_value.to_dict.return_value = dict(COLD_CTX)
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply()

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
            MagicMock(to_dict=MagicMock(return_value=WARM_CTX)),
            InsufficientDataError("no RIVN"),
        ]
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply()

        msg, grounded, focus, partial, unavailable = answer_chat_message(self.session.id, "AAPL vs RIVN")

        self.assertEqual(focus, ["AAPL"])
        self.assertEqual(unavailable, ["RIVN"])
        self.assertFalse(grounded)  # any unavailable -> not grounded
        prompt = mock_ai.complete.call_args.kwargs["prompt"]
        self.assertIn('<context symbol="AAPL"', prompt)

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_capped_note_added(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL", "MSFT", "NVDA"], True)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply()

        answer_chat_message(self.session.id, "compare five things")

        self.assertIn("more tickers than I can dig into",
                      mock_ai.complete.call_args.kwargs["prompt"])


class TestDegradeContract(_Base):
    @patch("backend.ai.chat.ai_manager")
    def test_ai_disabled_stores_message(self, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available.return_value = False
        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")
        self.assertFalse(grounded)
        self.assertIn("unavailable", msg.content.lower())

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_malformed_reply_degrades(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = AIResponse(text="not json", provider="ollama", model="x")
        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")
        self.assertFalse(grounded)

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_ai_exception_degrades(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.side_effect = RuntimeError("provider down")
        msg, grounded, *_ = answer_chat_message(self.session.id, "hi")
        self.assertFalse(grounded)

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
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000

        msg, grounded, focus, partial, unavailable = answer_chat_message(s.id, "what's the RSI?")

        self.assertFalse(grounded)
        self.assertIn("don't have enough data on AAPL", msg.content)
        mock_ai.complete.assert_not_called()


class TestAlertContext(_Base):
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_alert_facts_reach_prompt(self, mock_ctx, mock_ai):
        alert = Alert(name="T", symbol="AAPL", condition_type="price_above", parameter="100")
        self.db.add(alert)
        self.db.commit()
        trig = AlertTrigger(alert_id=alert.id, symbol="AAPL", message="AAPL crossed 100",
                            ai_commentary="A bullish breakout.")
        self.db.add(trig)
        self.db.commit()
        s = self._make_session(symbol="AAPL", alert_trigger_id=trig.id, scope="alert")

        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply()

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
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply(
            '{"reply": "Let me check.", "grounded": true, "wants_reanalysis": true}')
        mock_analyze.return_value = AnalysisResponse(
            summary="Strong momentum.", trend="bullish", confidence=0.82)

        msg, grounded, *_ = answer_chat_message(self.session.id, "re-run the analysis")

        mock_analyze.assert_called_once_with("AAPL")
        self.assertTrue(grounded)
        self.assertIn("bullish", msg.content)
        self.assertIn("82%", msg.content)

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_multi_symbol_reanalysis_without_target_is_rejected(self, mock_ctx, mock_ai, mock_analyze):
        self.mock_resolve.return_value = (["AAPL", "MSFT"], False)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply(
            '{"reply": "checking", "grounded": true, "wants_reanalysis": true}')

        msg, grounded, *_ = answer_chat_message(self.session.id, "re-run it")

        mock_analyze.assert_not_called()
        self.assertIn("Which ticker", msg.content)

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_multi_symbol_reanalysis_with_target_runs(self, mock_ctx, mock_ai, mock_analyze):
        self.mock_resolve.return_value = (["AAPL", "MSFT"], False)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply(
            '{"reply": "x", "grounded": true, "wants_reanalysis": true, "reanalysis_symbol": "MSFT"}')
        mock_analyze.return_value = AnalysisResponse(
            summary="Neutral and range-bound.", trend="neutral", confidence=0.5)

        answer_chat_message(self.session.id, "re-run MSFT officially")
        mock_analyze.assert_called_once_with("MSFT")

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_reanalysis_uncertainty_is_ungrounded(self, mock_ctx, mock_ai, mock_analyze):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply(
            '{"reply": "x", "grounded": true, "wants_reanalysis": true}')
        mock_analyze.return_value = UncertaintyResponse(summary="AI analysis is disabled")

        msg, grounded, *_ = answer_chat_message(self.session.id, "re-run the analysis")
        self.assertFalse(grounded)

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_ordinary_reply_does_not_invoke_tool(self, mock_ctx, mock_ai, mock_analyze):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply('{"reply": "up.", "grounded": true}')

        msg, *_ = answer_chat_message(self.session.id, "how's it doing?")
        mock_analyze.assert_not_called()
        self.assertEqual(msg.content, "up.")


class TestTurnIntent(_Base):
    """News / fundamentals / baseline are pulled only when the turn asks."""

    def _wire(self, mock_ctx, mock_ai, symbols=("AAPL",)):
        self.mock_resolve.return_value = (list(symbols), False)
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply()

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
        mock_ctx.return_value.to_dict.return_value = WARM_CTX
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply()

        answer_chat_message(self.session.id, "how's AAPL")
        answer_chat_message(self.session.id, "and the trend on AAPL")
        self.assertEqual(mock_ctx.call_count, 1)  # 2nd turn hit the cache


class TestTranscriptClip(_Base):
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_long_prior_message_is_clipped(self, mock_ctx, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available.return_value = True
        mock_ai.settings.max_tokens = 20000
        mock_ai.complete.return_value = _reply()
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


if __name__ == "__main__":
    unittest.main()
