"""The streaming and blocking Chat reply paths (BF-08, BF-09, BF-13).

BF-09: both transports must give the same answer for the same turn, so the
parity tests run one model outcome through each path and compare.
BF-08: text the app will replace (an action turn) must not be streamed.
BF-13: a turn that fails before its reply starts leaves nothing to resend,
and a client disconnect doesn't abandon the turn.
"""

import asyncio
import threading
import unittest
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.ai import chat
from backend.ai.context import InsufficientDataError
from backend.ai.provider import AIResponse
from backend.models import ChatMessage, ChatSession, Watchlist, WatchlistSymbol
from backend.repositories.watchlist_repository import WatchlistRepository
from backend.tests.ai.test_chat import WARM_CTX, _astream, _Base

_COULDNT_PROCESS = "I couldn't process that — could you rephrase?"
_PROVIDER_ERROR = "Something went wrong reaching the AI provider — please try again."


def _turn(user_content: str, **overrides) -> chat._Turn:
    fields = dict(
        symbol_blocks=[],
        unavailable=[],
        market_baseline=None,
        transcript=[],
        user_content=user_content,
        alert_context=None,
        capped=False,
        base=[],
        planner_state={},
    )
    fields.update(overrides)
    return chat._Turn(**fields)


def _blocking(turn: chat._Turn):
    return chat._generate_reply(
        None, turn.symbol_blocks, turn.unavailable, turn.market_baseline, turn.transcript,
        turn.user_content, turn.alert_context, turn.capped, turn.base, turn.planner_state, [], None,
    )


def _streaming(turn: chat._Turn):
    events = list(chat._generate_reply_streaming(None, turn, []))
    results = [payload for kind, payload in events if kind == "result"]
    assert len(results) == 1, events
    return results[0], [payload for kind, payload in events if kind == "delta"]


def _model(monkeypatch, *, raw_replies: list[str | None | Exception], streaming: bool) -> MagicMock:
    """Every model attempt returns the next entry: text, None (no text), or raises."""
    mock_ai = MagicMock()
    mock_ai.enabled = True
    mock_ai.settings.max_tokens = 20000
    mock_ai.settings.chat_streaming = streaming
    mock_ai.settings.chat_model = ""
    replies = list(raw_replies)

    def next_reply():
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def complete(*args, **kwargs):
        return AIResponse(text=next_reply(), provider="ollama", model="x")

    def stream(*args, **kwargs):
        text = next_reply()
        return _astream([] if text is None else [text[: len(text) // 2], text[len(text) // 2 :]])

    mock_ai.complete = AsyncMock(side_effect=complete)
    mock_ai.stream = MagicMock(side_effect=stream)
    monkeypatch.setattr("backend.ai.chat_model.ai_manager", mock_ai)
    return mock_ai


# --- BF-09: one answer, whichever transport -------------------------------------


@pytest.mark.parametrize(
    ("raw_replies", "expected"),
    [
        # Unparseable twice: the stream path used to answer with the raw,
        # unverified text it had decoded from the broken JSON.
        (['{"reply": "The trend looks strong and you should buy', "not json"], _COULDNT_PROCESS),
        # No text twice: the two paths used different wording.
        ([None, None], _PROVIDER_ERROR),
        ([RuntimeError("down"), RuntimeError("down")], _PROVIDER_ERROR),
    ],
)
@pytest.mark.parametrize("streaming", [True, False])
def test_model_failures_read_the_same_on_both_paths(monkeypatch, raw_replies, expected, streaming) -> None:
    _model(monkeypatch, raw_replies=list(raw_replies), streaming=streaming)
    streamed_result, _ = _streaming(_turn("what should I know?"))

    _model(monkeypatch, raw_replies=list(raw_replies), streaming=streaming)
    blocking_result = _blocking(_turn("what should I know?"))

    assert streamed_result == blocking_result == (expected, False, [])


class TestLegacyNoDataTurnWatchlistAdd(unittest.TestCase):
    """BF-09: in a single-ticker session whose ticker has no data yet, the
    stream path refused "add it to my watchlist" while the blocking path
    added it."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        for model in (ChatSession, ChatMessage, Watchlist, WatchlistSymbol):
            model.__table__.create(engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        for target, kwargs in (
            ("backend.repositories.chat_repository.SessionLocal", {"new": self.Session}),
            ("backend.ai.chat.build_market_baseline", {"return_value": {}}),
            ("backend.ai.chat_actions._kickoff_backfill", {}),
            ("backend.ai.chat.extract_unresolved_explicit_symbols", {"return_value": []}),
            ("backend.ai.chat.resolve_turn_symbols", {"return_value": (["RIVN"], False)}),
            ("backend.ai.chat.build_context", {"side_effect": InsufficientDataError("no data")}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        db = self.Session()
        session = ChatSession(symbol="RIVN", scope="symbol")
        db.add(session)
        db.commit()
        self.session_id = session.id
        db.close()

    @patch("backend.ai.chat_model.ai_manager")
    def test_stream_adds_the_no_data_ticker_to_a_watchlist(self, mock_ai):
        mock_ai.enabled = False

        events = list(chat.stream_chat_message(self.session_id, "add RIVN to my watchlist"))

        self.assertIn("added RIVN", events[-1][1][0].content)
        db = self.Session()
        try:
            lists = WatchlistRepository(db).get_watchlists()
            self.assertEqual([s.symbol for s in lists[0].symbols], ["RIVN"])
        finally:
            db.close()


# --- BF-08: no streamed text the app will replace --------------------------------

_ACTION_REPLY = (
    '{"reply": "Done — alert set for AAPL above 200.", "grounded": true, "action": "create_alert", '
    '"action_symbol": "AAPL", "action_condition_type": "price_above", "action_parameter": "200"}'
)


class TestStreamedTextGate(_Base):
    def _drain(self, content):
        return list(chat.stream_chat_message(self.session.id, content))

    @patch("backend.ai.chat.build_context")
    @patch("backend.ai.chat_model.ai_manager")
    def test_an_action_request_streams_no_model_text(self, mock_ai, mock_ctx):
        """The model's placeholder ("Done — alert set…") is replaced by the
        app's own result; streaming it first showed a completion the app
        had not performed yet."""
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = True
        mock_ai.stream.return_value = _astream([_ACTION_REPLY[:40], _ACTION_REPLY[40:]])

        events = self._drain("can you set an alert for when AAPL goes above 200")

        self.assertEqual([payload for kind, payload in events if kind == "delta"], [])
        final = events[-1][1][0]
        self.assertIn("set for AAPL", final.content)
        self.assertNotIn("Done — alert set for AAPL above 200.", final.content)

    @patch("backend.ai.chat_model.ai_manager")
    def test_an_action_seen_early_in_the_json_stops_the_stream(self, mock_ai):
        self.mock_resolve.return_value = ([], False)
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = True
        raw = (
            '{"action": "get_application_help", "action_tool_arguments": {"query": "alerts"}, '
            '"reply": "Let me look that up for you.", "grounded": true}'
        )
        mock_ai.stream.return_value = _astream([raw[:30], raw[30:90], raw[90:]])

        events = self._drain("what should I know?")

        self.assertEqual([payload for kind, payload in events if kind == "delta"], [])

    @patch("backend.ai.chat.build_context")
    @patch("backend.ai.chat_model.ai_manager")
    def test_non_streaming_provider_emits_no_delta_for_an_action(self, mock_ai, mock_ctx):
        self.mock_resolve.return_value = (["AAPL"], False)
        mock_ctx.return_value.compact.return_value = WARM_CTX
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.settings.max_tokens = 20000
        mock_ai.settings.chat_streaming = False
        mock_ai.complete = AsyncMock(return_value=AIResponse(text=_ACTION_REPLY, provider="ollama", model="x"))

        events = self._drain("can you set an alert for when AAPL goes above 200")

        self.assertEqual([payload for kind, payload in events if kind == "delta"], [])


# --- BF-13: nothing to resend, nothing abandoned ------------------------------------


class TestTurnStartAndDisconnect(_Base):
    def _stored(self):
        return self.db.query(ChatMessage).filter(ChatMessage.session_id == self.session.id).all()

    def test_a_failure_before_the_reply_starts_persists_nothing(self):
        """The router reports such a failure as "not started", so the client
        resends; a user row saved before the failure was then duplicated."""
        self.mock_resolve.side_effect = RuntimeError("symbol resolution failed")

        with pytest.raises(RuntimeError):
            chat.answer_chat_message(self.session.id, "hi")
        with pytest.raises(RuntimeError):
            list(chat.stream_chat_message(self.session.id, "hi"))

        self.assertEqual(self._stored(), [])

    @patch("backend.ai.chat_model.ai_manager")
    def test_the_user_row_exists_by_meta_and_the_transcript_stays_in_order(self, mock_ai):
        mock_ai.enabled = False
        chat.answer_chat_message(self.session.id, "first question")
        for kind, _ in chat.stream_chat_message(self.session.id, "second question"):
            if kind == "meta":
                # Meta tells the client the turn started, so the row must exist.
                self.db.expire_all()
                self.assertEqual(self._stored()[-1].content, "second question")

        self.db.expire_all()
        rows = self._stored()
        self.assertEqual([m.role for m in rows], ["user", "assistant", "user", "assistant"])
        self.assertEqual([m.content for m in rows if m.role == "user"], ["first question", "second question"])


def test_a_client_disconnect_does_not_abandon_the_turn() -> None:
    """The drain loop broke on disconnect and left the turn generator
    suspended, so the reply was never persisted."""
    from backend.api.ai import chat_router

    release = threading.Event()
    completed = threading.Event()

    def fake_stream(session_id, content, **kwargs):
        yield ("meta", {"focus": [], "partial": [], "unavailable": []})
        release.wait(5)  # a slow model call; the client leaves meanwhile
        yield ("delta", "late text")
        yield ("final", (MagicMock(), True, [], [], []))
        completed.set()

    async def scenario():
        with (
            patch("backend.ai.chat.stream_chat_message", fake_stream),
            patch("backend.api.ai.chat_router.ChatRepository") as repo_cls,
        ):
            repo_cls.return_value.get_session.return_value = MagicMock()
            response = await chat_router.send_message_stream(1, chat_router.SendMessageRequest(content="hi"))
            frames = response.body_iterator
            first = await frames.__anext__()
            assert first.startswith("event: meta")
            pending = asyncio.ensure_future(frames.__anext__())
            await asyncio.sleep(0.05)
            pending.cancel()  # what Starlette does when the client disconnects
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
            release.set()
            return await asyncio.to_thread(completed.wait, 5)

    assert asyncio.run(scenario()) is True
