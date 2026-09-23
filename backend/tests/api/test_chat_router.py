"""
Tests for /api/ai/chat/* — Version 4, AI feature 4 conversational chat
panel endpoints.
"""

import asyncio
import json
import threading
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.api.main import app


def _mock_session(id=1, symbol="AAPL", alert_trigger_id=None, scope=None):
    s = MagicMock()
    s.id = id
    s.symbol = symbol
    if scope is None:
        scope = "alert" if alert_trigger_id is not None else ("symbol" if symbol else "universal")
    s.scope = scope
    s.alert_trigger_id = alert_trigger_id
    s.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    s.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return s


def _mock_message(id=1, session_id=1, role="user", content="hi", response_blocks=None):
    m = MagicMock()
    m.id = id
    m.session_id = session_id
    m.role = role
    m.content = content
    m.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    # Explicit default (rather than leaving the MagicMock auto-attribute in
    # place) so tests can distinguish "no persisted blocks" from "persisted
    # blocks" instead of every unset access silently returning a truthy
    # MagicMock that _message_to_response's JSON parse would reject anyway.
    m.response_blocks = response_blocks
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
        self.assertEqual(data["scope"], "symbol")
        mock_repo.get_or_create_open_session.assert_called_once_with("aapl", None, None)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_creates_universal_session_when_symbol_omitted(self, mock_repo_cls):
        """No symbol in the body -> the single universal chat thread."""
        mock_repo = MagicMock()
        mock_repo.get_or_create_open_session.return_value = _mock_session(
            id=7,
            symbol="*",
            scope="universal",
        )
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post("/api/ai/chat/sessions", json={})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["symbol"])
        self.assertEqual(data["scope"], "universal")
        mock_repo.get_or_create_open_session.assert_called_once_with(None, None, None)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_creates_session_scoped_to_alert_trigger(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_or_create_open_session.return_value = _mock_session(
            symbol="AAPL",
            alert_trigger_id=42,
        )
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post(
            "/api/ai/chat/sessions",
            json={"symbol": "AAPL", "alert_trigger_id": 42},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["alert_trigger_id"], 42)
        self.assertEqual(resp.json()["scope"], "alert")
        mock_repo.get_or_create_open_session.assert_called_once_with("AAPL", 42, None)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_force_new_calls_create_session_not_get_or_create(self, mock_repo_cls):
        """The 'Clear conversation' path: force_new=True must always
        create a fresh session, never reuse the existing one — the old
        session/messages are left untouched (not deleted), same
        non-destructive convention as the rest of the app."""
        mock_repo = MagicMock()
        mock_repo.create_session.return_value = _mock_session(id=99, symbol="AAPL")
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post(
            "/api/ai/chat/sessions",
            json={"symbol": "AAPL", "force_new": True},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], 99)
        mock_repo.create_session.assert_called_once_with("AAPL", None, None)
        mock_repo.get_or_create_open_session.assert_not_called()

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_force_new_false_by_default(self, mock_repo_cls):
        """Regression: omitting force_new entirely must keep the
        existing reuse behavior (it defaults to False)."""
        mock_repo = MagicMock()
        mock_repo.get_or_create_open_session.return_value = _mock_session(symbol="AAPL")
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post("/api/ai/chat/sessions", json={"symbol": "AAPL"})

        self.assertEqual(resp.status_code, 200)
        mock_repo.create_session.assert_not_called()
        mock_repo.get_or_create_open_session.assert_called_once_with("AAPL", None, None)


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

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_persisted_blocks_round_trip_from_stored_json(self, mock_repo_cls):
        """5.7.1 contract test: a historical row's response_blocks column
        (a JSON string, as ChatRepository actually persists it) must come
        back out of GET .../messages as the same typed block list — not
        just today's freshly-generated response. Before this test, every
        _mock_message() in this file left response_blocks as an
        auto-created MagicMock attribute, which _message_to_response's
        json.loads() rejects and silently swallows via its except clause
        — so the real persisted-JSON path had zero coverage; every
        historical-message test was unknowingly only exercising the
        parse-failure fallback."""
        stored_blocks = [
            {
                "id": "prose-1",
                "type": "prose",
                "data": {"text": "AAPL is a moderate uptrend."},
                "quality": {"state": "verified", "grounded": True, "confidence": 1.0},
            },
            {
                "id": "ranked-1",
                "type": "ranked_results",
                "data": {"title": "AAPL relative strength", "items": [{"name": "vs QQQ", "score": 4.5}]},
                "quality": {"state": "verified", "grounded": True, "confidence": 1.0},
            },
        ]
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo.get_messages.return_value = [
            _mock_message(id=1, role="user", content="how's AAPL doing?"),
            _mock_message(
                id=2,
                role="assistant",
                content="AAPL is a moderate uptrend.",
                response_blocks=json.dumps(stored_blocks),
            ),
        ]
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/chat/sessions/1/messages")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data[0]["blocks"], [])  # user message never carries blocks
        self.assertEqual(data[1]["blocks"], stored_blocks)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_missing_response_blocks_returns_empty_list_not_error(self, mock_repo_cls):
        """A pre-5.7.1 historical row has no response_blocks column value
        at all (NULL) — must degrade to an empty list, not 500."""
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo.get_messages.return_value = [
            _mock_message(id=1, role="assistant", content="Old reply.", response_blocks=None),
        ]
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/chat/sessions/1/messages")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["blocks"], [])

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_corrupted_response_blocks_json_degrades_to_empty_list(self, mock_repo_cls):
        """A truncated/corrupted response_blocks string must not break the
        whole transcript — degrade that one message's blocks to []."""
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo.get_messages.return_value = [
            _mock_message(id=1, role="assistant", content="Reply.", response_blocks="{not valid json"),
        ]
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/chat/sessions/1/messages")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["blocks"], [])


class TestSendMessage(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.ai.chat.answer_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_sends_message_and_returns_assistant_reply(self, mock_repo_cls, mock_answer):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo
        mock_answer.return_value = (
            _mock_message(id=2, role="assistant", content="AAPL looks bullish."),
            True,
            ["AAPL"],
            [],
            [],
        )

        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages",
            json={"content": "How's AAPL?"},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["role"], "assistant")
        self.assertEqual(data["content"], "AAPL looks bullish.")
        self.assertTrue(data["grounded"])
        self.assertEqual(data["focus"], ["AAPL"])
        mock_answer.assert_called_once_with(1, "How's AAPL?")

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_404_when_session_missing(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = None
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post(
            "/api/ai/chat/sessions/999/messages",
            json={"content": "hi"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_empty_content_rejected(self):
        with patch("backend.api.ai.chat_router.ChatRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_session.return_value = _mock_session()
            mock_repo_cls.return_value = mock_repo
            resp = self.client.post(
                "/api/ai/chat/sessions/1/messages",
                json={"content": ""},
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
            [],
            [],
            ["RIVN"],
        )

        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages",
            json={"content": "What's the RSI?"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["grounded"])
        self.assertEqual(resp.json()["unavailable"], ["RIVN"])


class TestSSEBackpressure(unittest.TestCase):
    def test_bounded_queue_blocks_producer_until_consumer_drains(self):
        from backend.api.ai.chat_router import _SSE_QUEUE_MAXSIZE, _put_sse_item

        async def scenario():
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
            queue.put_nowait("first")
            result = []
            producer = threading.Thread(
                target=lambda: result.append(_put_sse_item(loop, queue, "second")),
            )
            producer.start()
            await asyncio.sleep(0.05)
            self.assertTrue(producer.is_alive())
            await queue.get()
            await asyncio.sleep(0)
            await asyncio.to_thread(producer.join, 1)
            self.assertFalse(producer.is_alive())
            self.assertEqual(result, [True])

        asyncio.run(scenario())


class TestSendMessageStream(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @staticmethod
    def _frames(text):
        """Parse an SSE body into [(event, data_str), ...]."""
        out = []
        for block in text.strip().split("\n\n"):
            ev = dat = None
            for line in block.splitlines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    dat = line[5:].strip()
            if ev:
                out.append((ev, dat))
        return out

    @patch("backend.ai.chat.stream_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_streams_meta_delta_final(self, mock_repo_cls, mock_stream):
        import json as _json

        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo
        mock_stream.return_value = iter(
            [
                ("meta", {"focus": ["AAPL"], "partial": [], "unavailable": []}),
                ("delta", "AAPL looks "),
                ("delta", "bullish."),
                (
                    "final",
                    (
                        _mock_message(id=2, role="assistant", content="AAPL looks bullish."),
                        True,
                        ["AAPL"],
                        [],
                        [],
                    ),
                ),
            ]
        )

        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages/stream",
            json={"content": "How's AAPL?"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers["content-type"])

        frames = self._frames(resp.text)
        kinds = [e for e, _ in frames]
        self.assertEqual(kinds, ["meta", "delta", "delta", "final"])
        self.assertEqual(_json.loads(frames[1][1])["text"], "AAPL looks ")
        final = _json.loads(frames[-1][1])
        self.assertEqual(final["content"], "AAPL looks bullish.")
        self.assertTrue(final["grounded"])
        self.assertEqual(final["focus"], ["AAPL"])
        mock_stream.assert_called_once_with(1, "How's AAPL?")

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_stream_404_when_session_missing(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = None
        mock_repo_cls.return_value = mock_repo
        resp = self.client.post(
            "/api/ai/chat/sessions/999/messages/stream",
            json={"content": "hi"},
        )
        self.assertEqual(resp.status_code, 404)


class TestClearSessions(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_clear_universal_history(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.delete_sessions.return_value = (9, 150)
        mock_repo_cls.return_value = mock_repo

        resp = self.client.delete("/api/ai/chat/sessions?scope=universal")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"deleted_sessions": 9, "deleted_messages": 150})
        mock_repo.delete_sessions.assert_called_once_with(scope="universal", alert_trigger_id=None)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_clear_all_history_no_filter(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.delete_sessions.return_value = (0, 0)
        mock_repo_cls.return_value = mock_repo

        resp = self.client.delete("/api/ai/chat/sessions")

        self.assertEqual(resp.status_code, 200)
        mock_repo.delete_sessions.assert_called_once_with(scope=None, alert_trigger_id=None)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_clear_by_alert_trigger(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.delete_sessions.return_value = (1, 4)
        mock_repo_cls.return_value = mock_repo

        resp = self.client.delete("/api/ai/chat/sessions?alert_trigger_id=7")

        self.assertEqual(resp.status_code, 200)
        mock_repo.delete_sessions.assert_called_once_with(scope=None, alert_trigger_id=7)


if __name__ == "__main__":
    unittest.main()
