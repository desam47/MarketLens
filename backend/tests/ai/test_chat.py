"""
Tests for backend.ai.chat.answer_chat_message — Version 4, AI feature 4.

answer_chat_message() must never raise for an expected failure mode:
AI off, a context-building failure (InsufficientDataError), or a
malformed AI reply all produce a stored assistant message
(grounded=False) rather than an exception — same contract as
analyze_symbol's uncertainty-response fallback.

Fresh in-memory SQLite per test (same convention as
backend/tests/repositories/test_bar_repository.py /
test_signal_repository.py) so this never touches the real
marketlens.db — ChatRepository (and, when exercised, Alert/
AlertTrigger queries) default to SessionLocal(), so SessionLocal is
patched at its import site in chat_repository to hand out sessions
bound to the in-memory engine instead.
"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.ai.chat import answer_chat_message
from backend.ai.context import InsufficientDataError
from backend.ai.prompt import AnalysisResponse, UncertaintyResponse
from backend.ai.provider import AIResponse
from backend.models import Alert, AlertTrigger, ChatMessage, ChatSession


class TestAnswerChatMessage(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
        )
        for model in (ChatSession, ChatMessage, Alert, AlertTrigger):
            model.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        self._session_local_patch = patch(
            "backend.repositories.chat_repository.SessionLocal", self.Session,
        )
        self._session_local_patch.start()

        self.db = self.Session()
        self.session = ChatSession(symbol="AAPL")
        self.db.add(self.session)
        self.db.commit()
        self.db.refresh(self.session)

    def tearDown(self):
        self._session_local_patch.stop()
        self.db.close()
        self.engine.dispose()

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_full_round_trip_persists_both_messages(self, mock_build_context, mock_ai):
        mock_build_context.return_value.to_dict.return_value = {"price": 150.0}
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = AIResponse(
            text='```json\n{"reply": "AAPL is trending up.", "grounded": true}\n```',
            provider="ollama", model="llama3.2",
        )

        message, grounded = answer_chat_message(self.session.id, "How is AAPL doing?")

        self.assertTrue(grounded)
        self.assertEqual(message.role, "assistant")
        self.assertEqual(message.content, "AAPL is trending up.")

        stored = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_id == self.session.id)
            .order_by(ChatMessage.created_at)
            .all()
        )
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[0].role, "user")
        self.assertEqual(stored[0].content, "How is AAPL doing?")
        self.assertEqual(stored[1].role, "assistant")

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_ai_disabled_stores_message_not_http_error(self, mock_build_context, mock_ai):
        mock_build_context.return_value.to_dict.return_value = {}
        mock_ai.is_available.return_value = False

        message, grounded = answer_chat_message(self.session.id, "Hi")

        self.assertFalse(grounded)
        self.assertEqual(message.role, "assistant")
        self.assertIn("unavailable", message.content.lower())

    @patch("backend.ai.chat.build_context")
    def test_insufficient_data_produces_ungrounded_reply(self, mock_build_context):
        mock_build_context.side_effect = InsufficientDataError("no data for AAPL")

        message, grounded = answer_chat_message(self.session.id, "What's the RSI?")

        self.assertFalse(grounded)
        self.assertIn("AAPL", message.content)
        self.assertEqual(message.role, "assistant")

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_malformed_ai_reply_degrades_gracefully(self, mock_build_context, mock_ai):
        mock_build_context.return_value.to_dict.return_value = {}
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = AIResponse(
            text="not json", provider="ollama", model="llama3.2",
        )

        message, grounded = answer_chat_message(self.session.id, "Hi")

        self.assertFalse(grounded)
        self.assertEqual(message.role, "assistant")

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_ai_call_exception_degrades_gracefully(self, mock_build_context, mock_ai):
        mock_build_context.return_value.to_dict.return_value = {}
        mock_ai.is_available.return_value = True
        mock_ai.complete.side_effect = RuntimeError("provider down")

        message, grounded = answer_chat_message(self.session.id, "Hi")

        self.assertFalse(grounded)

    def test_unknown_session_raises(self):
        """Missing session is a caller bug, not an 'AI couldn't
        answer' case — this one should raise, not degrade."""
        with self.assertRaises(ValueError):
            answer_chat_message(999999, "Hi")

    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_alert_context_included_when_session_scoped_to_trigger(
        self, mock_build_context, mock_ai
    ):
        alert = Alert(name="Test", symbol="AAPL", condition_type="price_above", parameter="100")
        self.db.add(alert)
        self.db.commit()
        trigger = AlertTrigger(
            alert_id=alert.id, symbol="AAPL", message="AAPL crossed 100",
            ai_commentary="A bullish breakout.",
        )
        self.db.add(trigger)
        self.db.commit()

        scoped_session = ChatSession(symbol="AAPL", alert_trigger_id=trigger.id)
        self.db.add(scoped_session)
        self.db.commit()
        self.db.refresh(scoped_session)

        mock_build_context.return_value.to_dict.return_value = {}
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = AIResponse(
            text='```json\n{"reply": "ok", "grounded": true}\n```',
            provider="ollama", model="llama3.2",
        )

        answer_chat_message(scoped_session.id, "Explain this alert")

        # The prompt sent to the AI must include the trigger's own facts.
        call_kwargs = mock_ai.complete.call_args.kwargs
        self.assertIn("AAPL crossed 100", call_kwargs["prompt"])
        self.assertIn("A bullish breakout.", call_kwargs["prompt"])


class TestChatReanalysisTool(unittest.TestCase):
    """The chat's one tool call: wants_reanalysis triggers a real
    analyze_symbol() run instead of the AI's own free-form reply.

    Same fixture shape as TestAnswerChatMessage — kept as its own
    class since every test here also patches analyze_symbol.
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
        )
        for model in (ChatSession, ChatMessage, Alert, AlertTrigger):
            model.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        self._session_local_patch = patch(
            "backend.repositories.chat_repository.SessionLocal", self.Session,
        )
        self._session_local_patch.start()

        self.db = self.Session()
        self.session = ChatSession(symbol="AAPL")
        self.db.add(self.session)
        self.db.commit()
        self.db.refresh(self.session)

    def tearDown(self):
        self._session_local_patch.stop()
        self.db.close()
        self.engine.dispose()

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_wants_reanalysis_runs_analyze_symbol_instead_of_reply(
        self, mock_build_context, mock_ai, mock_analyze,
    ):
        mock_build_context.return_value.to_dict.return_value = {"price": 150.0}
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = AIResponse(
            text=(
                '```json\n{"reply": "Let me check.", "grounded": true, '
                '"wants_reanalysis": true}\n```'
            ),
            provider="ollama", model="llama3.2",
        )
        mock_analyze.return_value = AnalysisResponse(
            summary="AAPL is showing strong upward momentum today.",
            trend="bullish",
            confidence=0.82,
        )

        message, grounded = answer_chat_message(self.session.id, "Re-run the analysis")

        mock_analyze.assert_called_once_with("AAPL")
        self.assertTrue(grounded)
        # The tool's own description replaces the AI's free-form reply —
        # "Let me check." must not be what got persisted.
        self.assertNotEqual(message.content, "Let me check.")
        self.assertIn("bullish", message.content)
        self.assertIn("82%", message.content)
        self.assertIn("strong upward momentum", message.content)

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_wants_reanalysis_uncertainty_result_is_ungrounded(
        self, mock_build_context, mock_ai, mock_analyze,
    ):
        mock_build_context.return_value.to_dict.return_value = {}
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = AIResponse(
            text='```json\n{"reply": "ok", "grounded": true, "wants_reanalysis": true}\n```',
            provider="ollama", model="llama3.2",
        )
        mock_analyze.return_value = UncertaintyResponse(
            summary="AI analysis is disabled (set AI_ENABLED=true to enable)",
        )

        message, grounded = answer_chat_message(self.session.id, "Re-run the analysis")

        self.assertFalse(grounded)
        self.assertIn("AAPL", message.content)

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_wants_reanalysis_exception_degrades_gracefully(
        self, mock_build_context, mock_ai, mock_analyze,
    ):
        mock_build_context.return_value.to_dict.return_value = {}
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = AIResponse(
            text='```json\n{"reply": "ok", "grounded": true, "wants_reanalysis": true}\n```',
            provider="ollama", model="llama3.2",
        )
        mock_analyze.side_effect = RuntimeError("db down")

        message, grounded = answer_chat_message(self.session.id, "Re-run the analysis")

        self.assertFalse(grounded)
        self.assertEqual(message.role, "assistant")

    @patch("backend.ai.chat.analyze_symbol")
    @patch("backend.ai.chat.ai_manager")
    @patch("backend.ai.chat.build_context")
    def test_ordinary_reply_does_not_invoke_the_tool(
        self, mock_build_context, mock_ai, mock_analyze,
    ):
        """The default (wants_reanalysis omitted, defaults False) must
        not touch analyze_symbol at all — regression against the tool
        firing on every turn."""
        mock_build_context.return_value.to_dict.return_value = {"price": 150.0}
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = AIResponse(
            text='```json\n{"reply": "AAPL is trending up.", "grounded": true}\n```',
            provider="ollama", model="llama3.2",
        )

        message, grounded = answer_chat_message(self.session.id, "How's it doing?")

        mock_analyze.assert_not_called()
        self.assertEqual(message.content, "AAPL is trending up.")


if __name__ == "__main__":
    unittest.main()
