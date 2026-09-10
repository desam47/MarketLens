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

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ...models.chat import UNIVERSAL_SYMBOL
from ...repositories.chat_repository import ChatRepository

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
    # Tickers this turn actually pulled quant context for / could not
    # (universal chat) — drives the frontend provenance row. Empty on
    # historical rows and user messages.
    focus: list[str] = []
    unavailable: list[str] = []


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
    unavailable: list[str] | None = None,
) -> MessageResponse:
    return MessageResponse(
        id=m.id,
        session_id=m.session_id,
        role=m.role,
        content=m.content,
        created_at=m.created_at.isoformat() if m.created_at else "",
        grounded=grounded,
        focus=focus or [],
        unavailable=unavailable or [],
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
            query = (
                lambda: repo.db.query(ChatSession)
                .filter(ChatSession.scope == "universal")
                .order_by(ChatSession.updated_at.desc())
                .first()
            )
        else:
            sym = symbol.upper()
            query = (
                lambda: repo.db.query(ChatSession)
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
