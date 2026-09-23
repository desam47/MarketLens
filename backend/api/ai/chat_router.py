"""
Version 4, AI feature 4 — conversational AI chat panel REST endpoints.

``POST /sessions`` — open (or reuse) a chat session for a symbol.
``GET /sessions`` — look up the existing session for a symbol.
``GET /sessions/{id}/messages`` — full transcript for a session.
``POST /sessions/{id}/messages`` — send a message, get the AI's reply.

The completion call inside answer_chat_message() is synchronous
(httpx.Client, not AsyncClient) — wrapped in asyncio.to_thread here,
same fixed pattern used by every other AI call site in this app (this
exact bug class — a sync AI call blocking the whole event loop — was
found and fixed in NL search earlier this session; not reintroducing
it here).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from ...models.chat import UNIVERSAL_SYMBOL
from ...repositories.chat_repository import ChatRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai/chat", tags=["ai-chat"])


class CreateSessionRequest(BaseModel):
    # Universal AI Hub chat (2026-09-10): symbol is now optional. Omit
    # it (or send null) for a universal session — one that isn't tied
    # to any ticker and resolves symbols per-message from the text.
    symbol: str | None = Field(default=None, max_length=20)
    scope: Literal["universal", "symbol", "alert"] | None = None
    alert_trigger_id: int | None = None

    @field_validator("symbol")
    @classmethod
    def _blank_symbol_is_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    # "Clear conversation" support: force a brand-new session instead
    # of reusing the most recent one for this symbol. The old session
    # and its messages are left untouched (not deleted) — same
    # non-destructive convention as the rest of the app (alert
    # triggers, digests) — just no longer the one a plain re-open
    # returns, since get_or_create_open_session always picks the most
    # recently updated session.
    force_new: bool = False


class SessionResponse(BaseModel):
    id: int
    # None for a universal session (the "*" sentinel is an internal
    # storage detail, not something the frontend should see).
    symbol: str | None
    scope: str
    alert_trigger_id: int | None
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    created_at: str
    # Only meaningful on the assistant message returned by POST
    # /messages — None for user messages and for GET /messages'
    # historical rows (not persisted, see backend.ai.chat's docstring).
    grounded: bool | None = None
    # Universal chat — drives the frontend provenance row. Empty on
    # historical rows and user messages. focus = full (warm-engine)
    # coverage; partial = live price / indicators only (not watchlisted);
    # unavailable = named but no data.
    focus: list[str] = []
    partial: list[str] = []
    unavailable: list[str] = []
    # Transient execution trace for the current assistant response. Historical
    # messages do not carry this because it is intentionally not stored in the
    # prose message table.
    tools: list[dict[str, Any]] = []
    # Version 5.7 typed blocks.  Empty for old/user rows; historical assistant
    # rows carry the persisted envelope without rerunning tools.
    blocks: list[dict[str, Any]] = []


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


def _session_to_response(s) -> SessionResponse:
    scope = getattr(s, "scope", None) or "symbol"
    return SessionResponse(
        id=s.id,
        symbol=None if scope == "universal" or s.symbol == UNIVERSAL_SYMBOL else s.symbol,
        scope=scope,
        alert_trigger_id=s.alert_trigger_id,
        created_at=s.created_at.isoformat() if s.created_at else "",
        updated_at=s.updated_at.isoformat() if s.updated_at else "",
    )


def _message_to_response(
    m,
    grounded: bool | None = None,
    focus: list[str] | None = None,
    partial: list[str] | None = None,
    unavailable: list[str] | None = None,
    tools: list[dict[str, Any]] | None = None,
    blocks: list[dict[str, Any]] | None = None,
) -> MessageResponse:
    stored_blocks = blocks
    if stored_blocks is None:
        raw_blocks = getattr(m, "response_blocks", None)
        if raw_blocks:
            try:
                parsed_blocks = json.loads(raw_blocks)
                stored_blocks = parsed_blocks if isinstance(parsed_blocks, list) else []
            except (TypeError, ValueError):
                stored_blocks = []
    return MessageResponse(
        id=m.id,
        session_id=m.session_id,
        role=m.role,
        content=m.content,
        created_at=m.created_at.isoformat() if m.created_at else "",
        grounded=grounded,
        focus=focus or [],
        partial=partial or [],
        unavailable=unavailable or [],
        tools=tools if tools is not None else list(getattr(m, "planner_trace", []) or []),
        blocks=stored_blocks or [],
    )


@router.post("/sessions", response_model=SessionResponse)
async def create_or_get_session(payload: CreateSessionRequest):
    """Open (or reuse) a chat session.

    With no ``symbol`` this opens (or reuses) the single *universal*
    chat thread — the AI Hub's chat, which resolves tickers per
    message. With a ``symbol`` it's one open thread per ticker (or per
    alert trigger). ``force_new=True`` (the "Clear conversation"
    button) always creates a fresh session instead of reusing one.
    """
    repo = ChatRepository()
    try:
        if payload.force_new:
            session = await asyncio.to_thread(
                repo.create_session,
                payload.symbol,
                payload.alert_trigger_id,
                payload.scope,
            )
        else:
            session = await asyncio.to_thread(
                repo.get_or_create_open_session,
                payload.symbol,
                payload.alert_trigger_id,
                payload.scope,
            )
        return _session_to_response(session)
    finally:
        repo.close()


@router.get("/sessions", response_model=SessionResponse)
async def get_session_for_symbol(
    symbol: str | None = Query(default=None, max_length=20),
):
    """Look up an existing session without creating one.

    No ``symbol`` → the universal thread; a ``symbol`` → that ticker's
    symbol-scoped thread (never the universal one).
    """
    repo = ChatRepository()
    try:
        from backend.models import ChatSession

        if symbol is None:

            def query():
                return (
                    repo.db.query(ChatSession)
                    .filter(ChatSession.scope == "universal")
                    .order_by(ChatSession.updated_at.desc())
                    .first()
                )
        else:
            sym = symbol.upper()

            def query():
                return (
                    repo.db.query(ChatSession)
                    .filter(ChatSession.symbol == sym, ChatSession.scope == "symbol")
                    .order_by(ChatSession.updated_at.desc())
                    .first()
                )

        session = await asyncio.to_thread(query)
        if session is None:
            where = symbol.upper() if symbol else "the universal chat"
            raise HTTPException(status_code=404, detail=f"No chat session for {where}")
        return _session_to_response(session)
    finally:
        repo.close()


class ClearHistoryResponse(BaseModel):
    deleted_sessions: int
    deleted_messages: int


@router.delete("/sessions", response_model=ClearHistoryResponse)
async def clear_sessions(
    scope: Literal["universal", "symbol", "alert"] | None = Query(default=None),
    alert_trigger_id: int | None = Query(default=None),
):
    """Delete chat sessions and every message under them.

    The "Clear" button in the AI Hub chat calls this with
    ``scope=universal`` so a clear actually flushes the thread instead
    of leaving orphaned sessions behind. With no filter it wipes all
    chat history; ``alert_trigger_id`` narrows it to an alert-opened
    thread.
    """
    repo = ChatRepository()
    try:
        sessions, messages = await asyncio.to_thread(
            repo.delete_sessions,
            scope=scope,
            alert_trigger_id=alert_trigger_id,
        )
        return ClearHistoryResponse(deleted_sessions=sessions, deleted_messages=messages)
    finally:
        repo.close()


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def get_messages(session_id: int, limit: int = Query(default=50, ge=1, le=200)):
    repo = ChatRepository()
    try:
        session = await asyncio.to_thread(repo.get_session, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        messages = await asyncio.to_thread(repo.get_messages, session_id, limit)
        return [_message_to_response(m) for m in messages]
    finally:
        repo.close()


@router.post("/sessions/{session_id}/messages", response_model=MessageResponse)
async def send_message(session_id: int, payload: SendMessageRequest):
    """Send a message and get the AI's reply.

    Returns only the assistant's reply message (the caller already
    has the user's own message — it just sent it) with a ``grounded``
    hint the plain GET /messages transcript doesn't carry.
    """
    from ...ai.chat import answer_chat_message

    repo = ChatRepository()
    try:
        session = await asyncio.to_thread(repo.get_session, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
    finally:
        repo.close()

    message, grounded, focus, partial, unavailable = await asyncio.to_thread(
        answer_chat_message,
        session_id,
        payload.content,
    )
    return _message_to_response(
        message,
        grounded=grounded,
        focus=focus,
        partial=partial,
        unavailable=unavailable,
        tools=getattr(message, "planner_trace", []),
        blocks=getattr(message, "response_blocks_payload", None),
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


_SSE_QUEUE_MAXSIZE = 1


def _put_sse_item(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[Any],
    item: Any,
) -> bool:
    future = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
    try:
        future.result()
    except (asyncio.CancelledError, RuntimeError):
        return False
    return True


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(session_id: int, payload: SendMessageRequest):
    """Send a message and stream the assistant's reply over SSE.

    Frames (``text/event-stream``):
      ``event: meta``  — ``{focus, partial, unavailable}``, once, up front
      ``event: delta`` — ``{text}``, the reply as it's generated
      ``event: final`` — the full ``MessageResponse`` (identical shape to
                         ``POST /messages``), once, after persistence
      ``event: error`` — ``{message}`` if the turn couldn't even start

    The reply row is still persisted exactly once (at the end); the
    ``final`` frame is authoritative — for the reanalysis-tool path it
    differs from the streamed deltas and the client should overwrite.
    """
    from ...ai.chat import stream_chat_message

    repo = ChatRepository()
    try:
        session = await asyncio.to_thread(repo.get_session, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
    finally:
        repo.close()

    async def event_stream():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
        stop = threading.Event()
        _DONE = object()

        def drain():
            try:
                for ev in stream_chat_message(session_id, payload.content):
                    if stop.is_set():
                        break
                    if not _put_sse_item(loop, queue, ev):
                        break
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat stream drain failed: %s", exc)
                if not stop.is_set():
                    _put_sse_item(loop, queue, ("error", "The chat turn could not be started."))
            finally:
                if not stop.is_set():
                    _put_sse_item(loop, queue, _DONE)

        executor_future = loop.run_in_executor(None, drain)

        def reap_executor(future):
            try:
                future.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat stream executor failed: %s", exc)

        executor_future.add_done_callback(reap_executor)

        try:
            while True:
                ev = await queue.get()
                if ev is _DONE:
                    break
                kind, payload_ = cast(tuple[str, Any], ev)
                if kind == "meta":
                    yield _sse("meta", payload_)
                elif kind == "delta":
                    yield _sse("delta", {"text": payload_})
                elif kind == "final":
                    message, grounded, focus, partial, unavailable = payload_
                    yield _sse(
                        "final",
                        _message_to_response(
                            message,
                            grounded=grounded,
                            focus=focus,
                            partial=partial,
                            unavailable=unavailable,
                            tools=getattr(message, "planner_trace", []),
                            blocks=getattr(message, "response_blocks_payload", None),
                        ).model_dump(),
                    )
                elif kind == "error":
                    yield _sse("error", {"message": payload_})
        except asyncio.CancelledError:
            stop.set()
            try:
                await asyncio.wait_for(queue.get(), timeout=0.1)
            except (TimeoutError, asyncio.CancelledError):
                pass
            raise
        finally:
            stop.set()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
