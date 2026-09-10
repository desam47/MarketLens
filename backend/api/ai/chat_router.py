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

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...repositories.chat_repository import ChatRepository

router = APIRouter(prefix="/api/ai/chat", tags=["ai-chat"])


class CreateSessionRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    alert_trigger_id: int | None = None
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
    symbol: str
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


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


def _session_to_response(s) -> SessionResponse:
    return SessionResponse(
        id=s.id,
        symbol=s.symbol,
        alert_trigger_id=s.alert_trigger_id,
        created_at=s.created_at.isoformat() if s.created_at else "",
        updated_at=s.updated_at.isoformat() if s.updated_at else "",
    )


def _message_to_response(m, grounded: bool | None = None) -> MessageResponse:
    return MessageResponse(
        id=m.id,
        session_id=m.session_id,
        role=m.role,
        content=m.content,
        created_at=m.created_at.isoformat() if m.created_at else "",
        grounded=grounded,
    )


@router.post("/sessions", response_model=SessionResponse)
async def create_or_get_session(payload: CreateSessionRequest):
    """Open (or reuse) a chat session for ``payload.symbol``.

    One open thread per symbol for v1 — see
    ChatRepository.get_or_create_open_session. ``force_new=True``
    (the "Clear conversation" button) always creates a fresh session
    instead of reusing the existing one.
    """
    repo = ChatRepository()
    try:
        if payload.force_new:
            session = await asyncio.to_thread(
                repo.create_session, payload.symbol, payload.alert_trigger_id,
            )
        else:
            session = await asyncio.to_thread(
                repo.get_or_create_open_session, payload.symbol, payload.alert_trigger_id,
            )
        return _session_to_response(session)
    finally:
        repo.close()


@router.get("/sessions", response_model=SessionResponse)
async def get_session_for_symbol(symbol: str = Query(..., min_length=1, max_length=10)):
    """Look up the existing session for ``symbol`` without creating one."""
    repo = ChatRepository()
    try:
        from backend.models import ChatSession

        session = await asyncio.to_thread(
            lambda: repo.db.query(ChatSession)
            .filter(ChatSession.symbol == symbol.upper())
            .order_by(ChatSession.updated_at.desc())
            .first()
        )
        if session is None:
            raise HTTPException(status_code=404, detail=f"No chat session for {symbol.upper()}")
        return _session_to_response(session)
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

    message, grounded = await asyncio.to_thread(
        answer_chat_message, session_id, payload.content,
    )
    return _message_to_response(message, grounded=grounded)


__all__ = ["router"]
