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

from ...models import ChatMessage
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
    # 5.7.8 feedback and correction loop — {"rating", "category", "comment",
    # "updated_at"} when the trader has rated this message, else None.
    # Only ever set on assistant messages.
    feedback: dict[str, Any] | None = None


class ChatPreferences(BaseModel):
    """The trader's personal operating preferences (5.7.3).

    Stored browser-local (no server table — same convention as Journal
    and Risk Dashboard) and sent with each turn so the deterministic
    suggested_followups block can be tailored to it. Informational only:
    never alters a verified calculation, tool argument, or
    evidence-derived conclusion — see build_response_blocks's
    preferences docstring.
    """

    model_config = {"extra": "forbid"}

    mode: Literal["day_trading", "swing_trading", "options", "long_term_investing"] | None = None
    preferred_timeframes: list[str] = Field(default_factory=list, max_length=10)
    default_session: Literal["premarket", "regular", "after_hours", "auto"] | None = None
    risk_per_trade_percent: float | None = Field(default=None, ge=0, le=100)
    primary_watchlist: str | None = Field(default=None, max_length=120)
    answer_detail_level: Literal["concise", "standard", "detailed"] | None = None
    preferred_units: Literal["percent", "dollars"] | None = None


class ChatChartState(BaseModel):
    """Explicit chart state supplied by the browser for one Chat turn."""

    model_config = {"extra": "forbid"}

    symbol: str = Field(..., min_length=1, max_length=20)
    timeframe: str = Field(default="1d", min_length=1, max_length=20)
    session: str = Field(default="all", min_length=1, max_length=20)
    chart_type: str | None = Field(default=None, max_length=30)
    active_indicators: list[str] = Field(default_factory=list, max_length=20)
    visible_range: dict[str, float] | None = None
    selected_candle: dict[str, Any] | None = None
    drawings: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    updated_at: str = Field(..., max_length=80)


ChatRegenerationMode = Literal[
    "again", "more_detail", "simpler", "bull_case", "bear_case",
    "calculations_only", "sources_only", "refresh", "rescope",
]


class BrowserPosition(BaseModel):
    """One Risk Dashboard position, as stored in the browser."""

    model_config = {"extra": "ignore"}

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    side: Literal["long", "short"] = "long"
    quantity: float = Field(..., gt=0)
    entry_price: float = Field(..., gt=0)
    current_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    sector: str | None = Field(default=None, max_length=100)


class BrowserJournalEntry(BaseModel):
    """Structured Journal fields only. Free text (thesis, notes, review) and
    screenshots are dropped here even if a client sends them."""

    model_config = {"extra": "ignore"}

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    side: Literal["long", "short"] = "long"
    status: Literal["planned", "open", "closed"] = "planned"
    quantity: float | None = Field(default=None, gt=0)
    entry_price: float | None = Field(default=None, gt=0)
    exit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    entry_date: str | None = Field(default=None, max_length=40)
    exit_date: str | None = Field(default=None, max_length=40)
    setup: str | None = Field(default=None, max_length=100)


class BrowserScanPreset(BaseModel):
    model_config = {"extra": "ignore"}

    name: str = Field(..., min_length=1, max_length=80)
    filters: list["BrowserScanFilter"] = Field(default_factory=list, max_length=40)
    match: Literal["AND", "OR"] = "AND"


class BrowserScanFilter(BaseModel):
    model_config = {"extra": "forbid"}

    type: str = Field(..., min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_\-]+$")
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=20)


class ChatBrowserData(BaseModel):
    """Browser-local data the trader opted in to sharing for this turn.

    Used only as explicit tool snapshots (never stored, never added to the
    prompt as raw data); see backend.ai.chat._apply_browser_data.
    """

    model_config = {"extra": "forbid"}

    positions: list[BrowserPosition] | None = Field(default=None, max_length=500)
    journal_entries: list[BrowserJournalEntry] | None = Field(default=None, max_length=1_000)
    scan_presets: list[BrowserScanPreset] | None = Field(default=None, max_length=50)


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    preferences: ChatPreferences | None = None
    browser_data: ChatBrowserData | None = None
    chart_state: ChatChartState | None = None
    regeneration_mode: ChatRegenerationMode | None = None
    regeneration_timeframe: str | None = Field(default=None, min_length=1, max_length=20)
    regeneration_session: Literal["premarket", "regular", "after_hours", "all", "auto"] | None = None


class SetFeedbackRequest(BaseModel):
    """5.7.8 feedback and correction loop. ``category`` is most
    meaningful for incorrect/not_useful but not restricted to them — a
    "correct" rating with a category is harmless and simpler than
    conditionally validating it server-side."""

    model_config = {"extra": "forbid"}

    rating: Literal["correct", "incorrect", "not_useful"]
    category: Literal[
        "wrong_data",
        "wrong_calculation",
        "misunderstood_intent",
        "stale_data",
        "poor_explanation",
        "unsafe_action",
    ] | None = None
    comment: str | None = Field(default=None, max_length=1000)


class RegressionFixtureResponse(BaseModel):
    id: int
    message_id: int
    prompt: str
    response: str
    rating: str
    category: str | None
    comment: str | None
    status: str
    created_at: str


class NotebookItemResponse(BaseModel):
    id: int
    notebook_id: int
    message_id: int
    question: str
    answer: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    content_types: list[str] = Field(default_factory=list)
    evidence_timestamps: list[str] = Field(default_factory=list)
    stale: bool
    material_change_detected: bool
    created_at: str


class NotebookResponse(BaseModel):
    id: int
    name: str
    created_at: str
    updated_at: str
    items: list[NotebookItemResponse] = Field(default_factory=list)


class CreateNotebookRequest(BaseModel):
    client_key: str = Field(..., min_length=8, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)


class RenameNotebookRequest(BaseModel):
    client_key: str = Field(..., min_length=8, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)


class SaveNotebookItemRequest(BaseModel):
    client_key: str = Field(..., min_length=8, max_length=80)
    message_id: int = Field(..., gt=0)
    question: str = Field(default="", max_length=2000)


def _json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _notebook_item_to_response(item) -> NotebookItemResponse:
    return NotebookItemResponse(
        id=item.id,
        notebook_id=item.notebook_id,
        message_id=item.message_id,
        question=item.question,
        answer=item.answer,
        blocks=_json_list(item.response_blocks),
        symbols=_json_list(item.symbols),
        content_types=_json_list(item.content_types),
        evidence_timestamps=_json_list(item.evidence_timestamps),
        stale=bool(item.stale),
        material_change_detected=bool(item.material_change_detected),
        created_at=item.created_at.isoformat() if item.created_at else "",
    )


def _notebook_to_response(notebook) -> NotebookResponse:
    return NotebookResponse(
        id=notebook.id,
        name=notebook.name,
        created_at=notebook.created_at.isoformat() if notebook.created_at else "",
        updated_at=notebook.updated_at.isoformat() if notebook.updated_at else "",
        items=[_notebook_item_to_response(item) for item in (notebook.items or [])],
    )


def _fixture_to_response(fixture) -> RegressionFixtureResponse:
    return RegressionFixtureResponse(
        id=fixture.id,
        message_id=fixture.message_id,
        prompt=fixture.prompt,
        response=fixture.response,
        rating=fixture.rating,
        category=fixture.category,
        comment=fixture.comment,
        status=fixture.status,
        created_at=fixture.created_at.isoformat() if fixture.created_at else "",
    )


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


def _feedback_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "rating": row.rating,
        "category": row.category,
        "comment": row.comment,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _message_to_response(
    m,
    grounded: bool | None = None,
    focus: list[str] | None = None,
    partial: list[str] | None = None,
    unavailable: list[str] | None = None,
    tools: list[dict[str, Any]] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    feedback=None,
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
        feedback=_feedback_to_dict(feedback),
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


class ResetMemoryResponse(BaseModel):
    session_id: int
    reset: bool


@router.delete("/sessions/{session_id}/memory", response_model=ResetMemoryResponse)
async def reset_session_memory(session_id: int):
    """Forget a session's structured memory but keep its messages (5.3.3).

    Clears remembered symbols, previous ticker, watchlist, timeframe,
    session, date range, last calculation inputs, last tool result, saved
    research assumptions, chart state, and any pending confirmation.
    """
    repo = ChatRepository()
    try:
        session = await asyncio.to_thread(repo.get_session, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        await asyncio.to_thread(repo.set_planner_state, session_id, "{}")
        return ResetMemoryResponse(session_id=session_id, reset=True)
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
        feedback_by_message = await asyncio.to_thread(
            repo.get_feedback_for_messages, [m.id for m in messages]
        )
        return [_message_to_response(m, feedback=feedback_by_message.get(m.id)) for m in messages]
    finally:
        repo.close()


@router.post("/messages/{message_id}/feedback", response_model=MessageResponse)
async def set_message_feedback(message_id: int, payload: SetFeedbackRequest):
    """Record Correct / Incorrect / Not Useful feedback on one assistant
    message (5.7.8). Upserts — a later call for the same message
    replaces the earlier rating rather than accumulating a history.
    Storage only: no automatic online model retraining happens from
    this. ``message_id`` is a global primary key, so this is not nested
    under a session id."""
    repo = ChatRepository()
    try:
        message = await asyncio.to_thread(repo.get_message, message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if message.role != "assistant":
            raise HTTPException(status_code=400, detail="Feedback can only be recorded on an assistant message")
        feedback = await asyncio.to_thread(
            repo.set_feedback, message_id, payload.rating, payload.category, payload.comment
        )
        return _message_to_response(message, feedback=feedback)
    finally:
        repo.close()


@router.post("/messages/{message_id}/regression-fixture", response_model=RegressionFixtureResponse)
async def create_regression_fixture(message_id: int):
    """Promote an explicitly negative feedback record to a regression fixture."""
    repo = ChatRepository()
    try:
        message = await asyncio.to_thread(repo.get_message, message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        fixture = await asyncio.to_thread(repo.create_regression_fixture, message_id)
        if fixture is None:
            raise HTTPException(
                status_code=400,
                detail="Only an incorrect or not-useful assistant message with feedback can become a fixture",
            )
        return _fixture_to_response(fixture)
    finally:
        repo.close()


@router.get("/regression-fixtures", response_model=list[RegressionFixtureResponse])
async def list_regression_fixtures(limit: int = Query(default=100, ge=1, le=500)):
    repo = ChatRepository()
    try:
        fixtures = await asyncio.to_thread(repo.get_regression_fixtures, limit)
        return [_fixture_to_response(fixture) for fixture in fixtures]
    finally:
        repo.close()


@router.get("/notebooks", response_model=list[NotebookResponse])
async def list_notebooks(client_key: str = Query(..., min_length=8, max_length=80)):
    repo = ChatRepository()
    try:
        notebooks = await asyncio.to_thread(repo.list_notebooks, client_key)
        return [_notebook_to_response(notebook) for notebook in notebooks]
    finally:
        repo.close()


@router.post("/notebooks", response_model=NotebookResponse)
async def create_notebook(payload: CreateNotebookRequest):
    repo = ChatRepository()
    try:
        notebook = await asyncio.to_thread(repo.create_notebook, payload.client_key, payload.name)
        return _notebook_to_response(notebook)
    finally:
        repo.close()


@router.patch("/notebooks/{notebook_id}", response_model=NotebookResponse)
async def rename_notebook(notebook_id: int, payload: RenameNotebookRequest):
    repo = ChatRepository()
    try:
        notebook = await asyncio.to_thread(
            repo.rename_notebook,
            notebook_id,
            payload.client_key,
            payload.name,
        )
        if notebook is None:
            raise HTTPException(status_code=404, detail="Notebook not found")
        return _notebook_to_response(notebook)
    finally:
        repo.close()


@router.delete("/notebooks/{notebook_id}")
async def delete_notebook(
    notebook_id: int,
    client_key: str = Query(..., min_length=8, max_length=80),
):
    repo = ChatRepository()
    try:
        deleted = await asyncio.to_thread(repo.delete_notebook, notebook_id, client_key)
        if not deleted:
            raise HTTPException(status_code=404, detail="Notebook not found")
        return {"deleted": True}
    finally:
        repo.close()


@router.post("/notebooks/{notebook_id}/items", response_model=NotebookItemResponse)
async def save_notebook_item(notebook_id: int, payload: SaveNotebookItemRequest):
    repo = ChatRepository()
    try:
        notebook = await asyncio.to_thread(repo.get_notebook, notebook_id, payload.client_key)
        if notebook is None:
            raise HTTPException(status_code=404, detail="Notebook not found")
        message = await asyncio.to_thread(repo.get_message, payload.message_id)
        if message is None or message.role != "assistant":
            raise HTTPException(status_code=404, detail="Assistant message not found")
        question = payload.question
        if not question:
            previous_user = (
                repo.db.query(ChatMessage)
                .filter(
                    ChatMessage.session_id == message.session_id,
                    ChatMessage.id < message.id,
                    ChatMessage.role == "user",
                )
                .order_by(type(message).id.desc())
                .first()
            )
            question = previous_user.content if previous_user is not None else "Saved answer"
        blocks = []
        try:
            parsed = json.loads(message.response_blocks or "[]")
            blocks = parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            blocks = []
        symbols: set[str] = set()
        content_types: set[str] = set()
        evidence_timestamps: set[str] = set()
        stale = False
        material_change = False
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if isinstance(block_type, str):
                content_types.add(block_type)
            data = block.get("data") if isinstance(block.get("data"), dict) else {}
            symbol_data = data.get("symbols") if isinstance(data.get("symbols"), dict) else {}
            for symbol in [*symbol_data.get("verified", []), *symbol_data.get("partial", []), *symbol_data.get("unavailable", [])]:
                if isinstance(symbol, str):
                    symbols.add(symbol.upper())
            if isinstance(data.get("symbol"), str):
                symbols.add(data["symbol"].upper())
            quality = block.get("quality") if isinstance(block.get("quality"), dict) else {}
            if quality.get("state") == "stale" or quality.get("freshness_status") == "stale":
                stale = True
            regeneration = data.get("regeneration") if isinstance(data.get("regeneration"), dict) else {}
            if quality.get("material_change_detected") or regeneration.get("material_change_detected"):
                material_change = True
            timestamp = quality.get("source_timestamp")
            if isinstance(timestamp, str):
                evidence_timestamps.add(timestamp)
            for evidence in data.get("items", []) if isinstance(data.get("items"), list) else []:
                if isinstance(evidence, dict) and isinstance(evidence.get("source_timestamp"), str):
                    evidence_timestamps.add(evidence["source_timestamp"])
        item = await asyncio.to_thread(
            repo.save_notebook_item,
            notebook_id,
            message_id=payload.message_id,
            question=question,
            answer=message.content,
            response_blocks=blocks,
            symbols=sorted(symbols),
            content_types=sorted(content_types),
            evidence_timestamps=sorted(evidence_timestamps),
            stale=stale,
            material_change_detected=material_change,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Notebook not found")
        return _notebook_item_to_response(item)
    finally:
        repo.close()


@router.delete("/notebooks/{notebook_id}/items/{item_id}")
async def delete_notebook_item(
    notebook_id: int,
    item_id: int,
    client_key: str = Query(..., min_length=8, max_length=80),
):
    repo = ChatRepository()
    try:
        deleted = await asyncio.to_thread(
            repo.delete_notebook_item,
            notebook_id,
            item_id,
            client_key,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Notebook item not found")
        return {"deleted": True}
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
        answer_chat_message, session_id, payload.content, **_turn_kwargs(payload)
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


def _turn_kwargs(payload: SendMessageRequest) -> dict[str, Any]:
    """Keyword arguments for answer_chat_message / stream_chat_message.

    Passed by name, only when the client sent them, so an optional field
    (e.g. a regeneration scope without a mode) can never land in another
    parameter's position.
    """
    kwargs: dict[str, Any] = {
        "preferences": payload.preferences.model_dump() if payload.preferences else None,
    }
    if payload.chart_state is not None:
        kwargs["chart_state"] = payload.chart_state.model_dump()
    if payload.regeneration_mode is not None:
        kwargs["regeneration_mode"] = payload.regeneration_mode
    if payload.regeneration_timeframe is not None or payload.regeneration_session is not None:
        kwargs["regeneration_scope"] = {
            "timeframe": payload.regeneration_timeframe,
            "session": payload.regeneration_session,
        }
    kwargs.update(_browser_data_kwargs(payload))
    return kwargs


def _browser_data_kwargs(payload: SendMessageRequest) -> dict[str, Any]:
    """Opted-in browser data, only when the client sent any."""
    if payload.browser_data is None:
        return {}
    data = payload.browser_data.model_dump(exclude_none=True)
    return {"browser_data": data} if data else {}


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
      ``event: error`` — ``{message, started}``; ``started`` is true when the
                         user message was already persisted, so the client
                         must not resend the turn

    The reply row is still persisted exactly once (at the end), even when the
    client disconnects mid-turn; the ``final`` frame is authoritative — for
    the reanalysis-tool path it differs from the streamed deltas and the
    client should overwrite.
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

        started = threading.Event()

        def drain():
            try:
                events = stream_chat_message(session_id, payload.content, **_turn_kwargs(payload))
                for ev in events:
                    if ev[0] == "meta":
                        # The user message is persisted from here on.
                        started.set()
                    # After a disconnect, keep running the turn to the end so
                    # the reply (and any action already under way) is saved;
                    # there is just no one left to send frames to.
                    if stop.is_set():
                        continue
                    if not _put_sse_item(loop, queue, ev):
                        stop.set()
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat stream drain failed: %s", exc)
                if not stop.is_set():
                    message = (
                        "The chat turn failed after it started; reload the conversation instead of resending."
                        if started.is_set()
                        else "The chat turn could not be started."
                    )
                    _put_sse_item(loop, queue, ("error", {"message": message, "started": started.is_set()}))
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
                    yield _sse("error", payload_)
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
