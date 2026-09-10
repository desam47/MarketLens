"""
Tests for /api/ai/chat/* — Version 4, AI feature 4 conversational chat
panel endpoints.
"""
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.api.main import app


def _mock_session(id=1, symbol="AAPL", alert_trigger_id=None):
    s = MagicMock()
    s.id = id
    s.symbol = symbol
    s.alert_trigger_id = alert_trigger_id
    s.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    s.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return s


def _mock_message(id=1, session_id=1, role="user", content="hi"):
    m = MagicMock()
    m.id = id
    m.session_id = session_id
    m.role = role
    m.content = content
    m.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    return m


class TestCreateOrGetSession(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_creates_session_for_symbol(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_or_create_open_session.return_value = _mock_session(symbol="AAPL")
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post("/api/ai/chat/sessions", json={"symbol": "aapl"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["symbol"], "AAPL")
        mock_repo.get_or_create_open_session.assert_called_once_with("aapl", None)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_creates_session_scoped_to_alert_trigger(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_or_create_open_session.return_value = _mock_session(
            symbol="AAPL", alert_trigger_id=42,
        )
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post(
            "/api/ai/chat/sessions", json={"symbol": "AAPL", "alert_trigger_id": 42},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["alert_trigger_id"], 42)
        mock_repo.get_or_create_open_session.assert_called_once_with("AAPL", 42)


class TestGetMessages(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_returns_transcript(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo.get_messages.return_value = [
            _mock_message(id=1, role="user", content="hi"),
            _mock_message(id=2, role="assistant", content="hello"),
        ]
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/chat/sessions/1/messages")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["role"], "user")
        self.assertEqual(data[1]["role"], "assistant")
        # Historical rows don't carry a grounded hint.
        self.assertIsNone(data[0]["grounded"])

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_404_when_session_missing(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = None
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/chat/sessions/999/messages")
        self.assertEqual(resp.status_code, 404)


class TestSendMessage(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.ai.chat.answer_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_sends_message_and_returns_assistant_reply(
        self, mock_repo_cls, mock_answer
    ):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo
        mock_answer.return_value = (
            _mock_message(id=2, role="assistant", content="AAPL looks bullish."),
            True,
        )

        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages", json={"content": "How's AAPL?"},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["role"], "assistant")
        self.assertEqual(data["content"], "AAPL looks bullish.")
        self.assertTrue(data["grounded"])
        mock_answer.assert_called_once_with(1, "How's AAPL?")

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_404_when_session_missing(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = None
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post(
            "/api/ai/chat/sessions/999/messages", json={"content": "hi"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_empty_content_rejected(self):
        with patch("backend.api.ai.chat_router.ChatRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_session.return_value = _mock_session()
            mock_repo_cls.return_value = mock_repo
            resp = self.client.post(
                "/api/ai/chat/sessions/1/messages", json={"content": ""},
            )
        self.assertEqual(resp.status_code, 422)

    @patch("backend.ai.chat.answer_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_ungrounded_reply_surfaced(self, mock_repo_cls, mock_answer):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo
        mock_answer.return_value = (
            _mock_message(id=2, role="assistant", content="I don't have enough data."),
            False,
        )

        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages", json={"content": "What's the RSI?"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["grounded"])


if __name__ == "__main__":
    unittest.main()
