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
        mock_repo.get_feedback_for_messages.return_value = {}
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/chat/sessions/1/messages")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["role"], "user")
        self.assertEqual(data[1]["role"], "assistant")
        # Historical rows don't carry a grounded hint.
        self.assertIsNone(data[0]["grounded"])
        self.assertIsNone(data[1]["feedback"])

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_attaches_feedback_to_its_message_only(self, mock_repo_cls):
        """5.7.8: GET /messages must batch-fetch feedback and attach each
        record to its own message, not leak onto neighboring messages."""
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        m1 = _mock_message(id=1, role="assistant", content="first")
        m2 = _mock_message(id=2, role="assistant", content="second")
        mock_repo.get_messages.return_value = [m1, m2]
        feedback_row = MagicMock(rating="incorrect", category="wrong_data", comment="stale", updated_at=None)
        mock_repo.get_feedback_for_messages.return_value = {1: feedback_row}
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/chat/sessions/1/messages")

        data = resp.json()
        self.assertEqual(data[0]["feedback"], {"rating": "incorrect", "category": "wrong_data", "comment": "stale", "updated_at": None})
        self.assertIsNone(data[1]["feedback"])

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
        mock_repo.get_feedback_for_messages.return_value = {}
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
        mock_repo.get_feedback_for_messages.return_value = {}
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
        mock_repo.get_feedback_for_messages.return_value = {}
        mock_repo_cls.return_value = mock_repo

        resp = self.client.get("/api/ai/chat/sessions/1/messages")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["blocks"], [])


class TestSetFeedback(unittest.TestCase):
    """5.7.8 feedback and correction loop: POST /messages/{id}/feedback."""

    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_records_feedback_on_an_assistant_message(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_message.return_value = _mock_message(id=2, role="assistant", content="AAPL looks bullish.")
        mock_repo.set_feedback.return_value = MagicMock(
            rating="incorrect", category="wrong_data", comment="Price is stale.", updated_at=None,
        )
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post(
            "/api/ai/chat/messages/2/feedback",
            json={"rating": "incorrect", "category": "wrong_data", "comment": "Price is stale."},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["feedback"], {"rating": "incorrect", "category": "wrong_data", "comment": "Price is stale.", "updated_at": None})
        mock_repo.set_feedback.assert_called_once_with(2, "incorrect", "wrong_data", "Price is stale.")

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_category_and_comment_are_optional(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_message.return_value = _mock_message(id=2, role="assistant", content="ok")
        mock_repo.set_feedback.return_value = MagicMock(rating="correct", category=None, comment=None, updated_at=None)
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post("/api/ai/chat/messages/2/feedback", json={"rating": "correct"})

        self.assertEqual(resp.status_code, 200)
        mock_repo.set_feedback.assert_called_once_with(2, "correct", None, None)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_404_when_message_missing(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_message.return_value = None
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post("/api/ai/chat/messages/999/feedback", json={"rating": "correct"})
        self.assertEqual(resp.status_code, 404)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_rejects_feedback_on_a_user_message(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_message.return_value = _mock_message(id=1, role="user", content="how's AAPL?")
        mock_repo_cls.return_value = mock_repo

        resp = self.client.post("/api/ai/chat/messages/1/feedback", json={"rating": "correct"})

        self.assertEqual(resp.status_code, 400)
        mock_repo.set_feedback.assert_not_called()

    def test_rejects_invalid_rating(self):
        resp = self.client.post("/api/ai/chat/messages/1/feedback", json={"rating": "meh"})
        self.assertEqual(resp.status_code, 422)

    def test_rejects_invalid_category(self):
        resp = self.client.post(
            "/api/ai/chat/messages/1/feedback", json={"rating": "incorrect", "category": "not_a_real_category"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_rejects_unknown_field(self):
        resp = self.client.post("/api/ai/chat/messages/1/feedback", json={"rating": "correct", "typo_field": 1})
        self.assertEqual(resp.status_code, 422)


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
        mock_answer.assert_called_once_with(1, "How's AAPL?", None)

    @patch("backend.ai.chat.answer_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_forwards_preferences_as_a_plain_dict(self, mock_repo_cls, mock_answer):
        """5.7.3: preferences sent with the turn must reach
        answer_chat_message as a plain dict (model_dump()), not the
        Pydantic model itself — chat.py's build_response_blocks does a
        plain .get('mode') on it."""
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo
        mock_answer.return_value = (
            _mock_message(id=2, role="assistant", content="AAPL looks bullish."),
            True, ["AAPL"], [], [],
        )

        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages",
            json={"content": "How's AAPL?", "preferences": {"mode": "day_trading", "risk_per_trade_percent": 1.5}},
        )

        self.assertEqual(resp.status_code, 200)
        sent_preferences = mock_answer.call_args.args[2]
        self.assertIsInstance(sent_preferences, dict)
        self.assertEqual(sent_preferences["mode"], "day_trading")
        self.assertEqual(sent_preferences["risk_per_trade_percent"], 1.5)

    def test_rejects_invalid_mode(self):
        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages",
            json={"content": "How's AAPL?", "preferences": {"mode": "not_a_real_mode"}},
        )
        self.assertEqual(resp.status_code, 422)

    def test_rejects_risk_per_trade_percent_out_of_range(self):
        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages",
            json={"content": "How's AAPL?", "preferences": {"risk_per_trade_percent": 500}},
        )
        self.assertEqual(resp.status_code, 422)

    def test_rejects_unknown_preference_field(self):
        """extra='forbid' — a typo'd or stale field must be rejected, not
        silently ignored, so a frontend/backend contract drift is loud."""
        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages",
            json={"content": "How's AAPL?", "preferences": {"moode": "day_trading"}},
        )
        self.assertEqual(resp.status_code, 422)

    @patch("backend.ai.chat.answer_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_forwards_regeneration_mode_with_explicit_scope(self, mock_repo_cls, mock_answer):
        """5.7.9: regeneration is a typed request field, so refresh can
        invalidate context caches instead of relying only on a prose marker."""
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo
        mock_answer.return_value = (_mock_message(id=2, role="assistant", content="fresh"), True, ["AAPL"], [], [])

        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages",
            json={"content": "How's AAPL?", "regeneration_mode": "refresh"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_answer.call_args.args[3], None)
        self.assertEqual(mock_answer.call_args.args[4], "refresh")

    def test_rejects_unknown_regeneration_mode(self):
        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages",
            json={"content": "How's AAPL?", "regeneration_mode": "live_magic"},
        )
        self.assertEqual(resp.status_code, 422)

    @patch("backend.ai.chat.answer_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_forwards_typed_regeneration_scope(self, mock_repo_cls, mock_answer):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo
        mock_answer.return_value = (_mock_message(id=2, role="assistant", content="scoped"), True, ["AAPL"], [], [])

        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages",
            json={
                "content": "How's AAPL?",
                "regeneration_mode": "more_detail",
                "regeneration_timeframe": "4h",
                "regeneration_session": "regular",
            },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_answer.call_args.args[5], {"timeframe": "4h", "session": "regular"})

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
        mock_stream.assert_called_once_with(1, "How's AAPL?", None)

    @patch("backend.ai.chat.stream_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_forwards_preferences_as_a_plain_dict(self, mock_repo_cls, mock_stream):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo
        mock_stream.return_value = iter(
            [
                ("meta", {"focus": ["AAPL"], "partial": [], "unavailable": []}),
                ("final", (_mock_message(id=2, role="assistant", content="ok"), True, ["AAPL"], [], [])),
            ]
        )

        resp = self.client.post(
            "/api/ai/chat/sessions/1/messages/stream",
            json={"content": "How's AAPL?", "preferences": {"mode": "swing_trading"}},
        )

        self.assertEqual(resp.status_code, 200)
        mock_stream.assert_called_once_with(1, "How's AAPL?", {"mode": "swing_trading", "preferred_timeframes": [], "default_session": None, "risk_per_trade_percent": None, "primary_watchlist": None, "answer_detail_level": None, "preferred_units": None})

    @patch("backend.ai.chat.stream_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_error_after_meta_reports_the_turn_as_started(self, mock_repo_cls, mock_stream):
        import json as _json

        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo

        def events():
            yield ("meta", {"focus": [], "partial": [], "unavailable": []})
            raise RuntimeError("db write failed")

        mock_stream.return_value = events()
        resp = self.client.post("/api/ai/chat/sessions/1/messages/stream", json={"content": "hi"})

        frames = self._frames(resp.text)
        self.assertEqual([e for e, _ in frames], ["meta", "error"])
        self.assertTrue(_json.loads(frames[1][1])["started"])

    @patch("backend.ai.chat.stream_chat_message")
    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_error_before_meta_reports_the_turn_as_not_started(self, mock_repo_cls, mock_stream):
        import json as _json

        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo
        mock_stream.side_effect = RuntimeError("prep failed")
        resp = self.client.post("/api/ai/chat/sessions/1/messages/stream", json={"content": "hi"})

        frames = self._frames(resp.text)
        self.assertEqual([e for e, _ in frames], ["error"])
        self.assertFalse(_json.loads(frames[0][1])["started"])

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


class TestResetMemory(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_reset_clears_planner_state_only(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = _mock_session()
        mock_repo_cls.return_value = mock_repo

        resp = self.client.delete("/api/ai/chat/sessions/1/memory")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"session_id": 1, "reset": True})
        mock_repo.set_planner_state.assert_called_once_with(1, "{}")
        mock_repo.delete_sessions.assert_not_called()

    @patch("backend.api.ai.chat_router.ChatRepository")
    def test_reset_404_when_session_missing(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.get_session.return_value = None
        mock_repo_cls.return_value = mock_repo

        resp = self.client.delete("/api/ai/chat/sessions/999/memory")

        self.assertEqual(resp.status_code, 404)
        mock_repo.set_planner_state.assert_not_called()


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
