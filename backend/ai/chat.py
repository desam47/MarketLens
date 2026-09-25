"""
Version 5 — conversational AI chat panel: turn orchestration.

This module runs a turn end to end: context assembly (_prepare_turn), the
one generation path for both transports (_reply_events), and persistence
(_finish_turn, answer_chat_message, stream_chat_message). The rest of Chat
lives in sibling modules, each importing only from the ones after it:

- chat_routing — replies the server gives without the model.
- chat_actions — action handlers, market tools, multi-step turns.
- chat_model — model calls, streaming, retries, turn budgets; the only
  module holding ``ai_manager``.
- chat_intents — regex intents and text parsing, no I/O.
- chat_replies — plain-sentence replies from verified results.

History, in order:

Scope (see docs/plan): single-turn-per-message (build_context() is
rebuilt fresh per message, not held as server-side "memory"), no
streaming. Read-only relationship to features 2-3 — this module never
triggers a new digest or alert commentary; it only reads what already
exists.

Tool-calling was originally excluded entirely, deliberately deferred
as "a separate future decision" (see docs/Version_4/v4_plan.md). Later
resolved to a single, narrow tool: the AI can request a real
analyze_symbol() run (the same function AIAnalysisPanel's "Re-run"
button calls) when the trader explicitly asks for a fresh/official
analysis, via ChatReplyResponse.wants_reanalysis.

2026-09-11 onward: extended to a small closed set of further tools, via
ChatReplyResponse.action — create/delete an alert, add/remove a
watchlist ticker, create/delete a watchlist, and run a fresh on-demand
backtest or deterministic calculation (see chat_actions._run_action and its handlers). Still no open-ended
function-calling loop: exactly
one action, decided in the same completion call that would otherwise
produce a normal reply, executed synchronously before the turn's
assistant message is persisted. Destructive actions (delete_alert,
remove_from_watchlist, delete_watchlist, save_to_journal) get a hard,
backend-enforced confirm gate in chat_actions._finalize_parsed — a destructive action never runs
unless action_confirmed is set, regardless of what the model's prompt
compliance does; the confirmation question itself is server-authored
text, not trusted AI prose (same "never trust the AI for the actual
side effect" stance as TradePlan's model_validator re-deriving
risk:reward instead of the AI's own arithmetic).

2026-09-16: a single user message may ask for more than one of the
above ("create a watchlist called Tech and add NVDA to it"). Rather
than widen the flat JSON schema to carry a list of heterogeneous
actions (this codebase deliberately keeps that schema flat — a weak
local model mangles nested JSON far more often than an extra
top-level key), chat_actions._run_turn_actions chains multiple ONE-action
completion calls within a single turn: after a real action executes,
if the trader's own message hinted at more than one request (a cheap
"and"/"then"/"also"/";" regex gate — never spent on an ordinary
single-action turn), the model is asked once more, with a note on
what already ran, whether anything from the original message is
still undone. Bounded by the per-turn tool-call, planning-call,
token, and wall-clock budgets, and a destructive step
still stops the chain for its own confirmation exactly as before —
this only automates stringing together steps that individually
already needed no confirmation.

Follows analyze_symbol's "never raise for an expected failure mode"
contract: AI off, InsufficientDataError, or a malformed reply all
degrade to a stored assistant message explaining that (grounded=False)
rather than an HTTP error or a crashed request. Every tool call
inherits the same contract — none of them raise either.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.ai.answer_verifier import assign_evidence_ids, verify_answer
from backend.ai.chat_actions import (
    _TURN_BROWSER_DATA,
    _bounded_numeric_evidence,
    _regeneration_tool_scope,
    _resolve_named_watchlist,
    _rollback_quietly,
    _run_turn_actions,
    _trace_is_action_only,
    _watchlist_contents_reply,
    _watchlist_list_reply,
)
from backend.ai.chat_intents import (
    _ACTION_INTENT,
    _AFFIRM_INTENT,
    _AMBIGUOUS_RANKING_REFERENCE,
    _AMBIGUOUS_REFERENCE,
    _ASSUMPTION_SAVE_INTENT,
    _CALCULATION_HINT,
    _COMPARISON_INTENT,
    _DELETE_WATCHLIST_FALLBACK,
    _FUNDA_INTENT,
    _MARKET_INTENT,
    _NEWS_INTENT,
    _STATS_INTENT,
    _WATCHLIST_ADD_INTENT,
    _WATCHLIST_CONTENTS_INTENT,
    _WATCHLIST_CREATE_INTENT,
    _WATCHLIST_DELETE_INTENT,
    _WATCHLIST_LIST_INTENT,
    _WATCHLIST_REMOVE_FROM_INTENT,
    _calculation_followup,
    _extract_memory_value,
    _fallback_calculation,
)
from backend.ai.chat_model import (
    _ai_enabled,
    _ai_setting,
    _chat_route_model,
    _complete_and_parse,
    _prompt_token_budget,
    _stream_and_parse,
    _trace_model_route,
)
from backend.ai.chat_observability import (
    build_turn_observability,
)
from backend.ai.chat_routing import (
    _confirm_pending_action,
    _run_deterministic_shortcircuit,
)
from backend.ai.chat_symbols import extract_unresolved_explicit_symbols, resolve_turn_symbols
from backend.ai.context import InsufficientDataError, build_context
from backend.ai.market_baseline import build_market_baseline
from backend.ai.prompt import (
    CHAT_SYSTEM_PROMPT,
    build_chat_prompt,
)
from backend.ai.response_blocks import build_response_blocks, evidence_fingerprint
from backend.ai.semantic_router import route_semantic_intent

# Chat runs its sync generator helpers on loop-less worker threads
# (ThreadPoolExecutor / asyncio.to_thread), so the async AI calls are
# bridged with run_sync/stream_sync rather than awaited.
from backend.ai.tool_registry import (
    resolve_relative_date,
)
from backend.config.settings import settings
from backend.models import Alert, AlertTrigger, ChatMessage
from backend.models.chat import UNIVERSAL_SYMBOL
from backend.repositories.chat_repository import ChatRepository
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)

# How many prior turns (user + assistant messages combined) to include
# as transcript text in the prompt.
_TRANSCRIPT_TURNS = 6
_TRANSCRIPT_MSG_CHARS = 600  # per-message clip inside the transcript


def _context_freshness_seconds(timestamp: object) -> float | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Naive timestamps are America/New_York by project convention.
            from backend.utils.timezone import NY

            parsed = parsed.replace(tzinfo=NY)
        return round(max(0.0, (datetime.now(UTC) - parsed).total_seconds()), 3)
    except (TypeError, ValueError):
        return None


def _context_source_timestamp(payload: dict) -> str | None:
    for key in ("source_timestamp", "timestamp", "as_of", "generated_at"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _context_labels(payload: dict) -> dict[str, str]:
    labels: dict[str, str] = {}
    for key in ("direction", "trend", "classification", "regime", "market_regime", "volatility_state"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            labels[key] = value
    return labels


def _append_context_evidence(trace: list[dict], turn: _Turn) -> None:
    """Expose bounded server-owned context to the verifier as evidence.

    Prompt context is not automatically evidence: the verifier only trusts
    trace entries. These entries contain bounded numeric/qualitative fields,
    never the full private market baseline or raw prompt payload.
    """
    for block in turn.symbol_blocks:
        context = block.get("context") if isinstance(block, dict) else None
        if not isinstance(context, dict):
            continue
        item = {
            "tool": "chat_symbol_context",
            "ok": True,
            "provider": "MarketLens context",
            "symbol": str(block.get("symbol") or "").upper(),
            "arguments": {"symbol": str(block.get("symbol") or "").upper()},
        }
        values = _bounded_numeric_evidence(context)
        if values:
            item["evidence_values"] = values
        labels = _context_labels(context)
        if labels:
            item["evidence_labels"] = labels
        source_timestamp = _context_source_timestamp(context)
        if source_timestamp:
            item["source_timestamp"] = source_timestamp
            item["freshness_seconds"] = _context_freshness_seconds(source_timestamp)
        trace.append(item)

    baseline = turn.market_baseline
    if not isinstance(baseline, dict):
        return
    regime = baseline.get("regime_live")
    regime_payload = regime if isinstance(regime, dict) else {}
    baseline_values = _bounded_numeric_evidence(regime_payload)
    baseline_labels = _context_labels({**baseline, **regime_payload})
    source_timestamp = (
        _context_source_timestamp(regime_payload)
        or _context_source_timestamp(baseline)
    )
    item = {
        "tool": "chat_market_baseline",
        "ok": True,
        "provider": "MarketLens baseline",
        "arguments": {},
    }
    if baseline_values:
        item["evidence_values"] = baseline_values
    if baseline_labels:
        item["evidence_labels"] = baseline_labels
    if source_timestamp:
        item["source_timestamp"] = source_timestamp
        item["freshness_seconds"] = _context_freshness_seconds(source_timestamp)
    trace.append(item)


# Short-lived per-symbol context cache — a burst of follow-ups about one
# name rebuilt the whole scan + aux-data each turn.
_CTX_TTL = 12.0
_CTX_CAP = 64
_ctx_lock = threading.Lock()
_ctx_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()


def _clear_context_cache() -> None:
    with _ctx_lock:
        _ctx_cache.clear()


def _has_fresh_context_cache(symbols: list[str], *, news: bool, funda: bool, diverg: bool) -> bool:
    now = time.monotonic()
    with _ctx_lock:
        return any(
            (hit := _ctx_cache.get((symbol, news, funda, diverg))) is not None
            and now - hit[0] < _CTX_TTL
            for symbol in symbols
        )


def _clip(text: str) -> str:
    text = text or ""
    if len(text) <= _TRANSCRIPT_MSG_CHARS:
        return text
    return text[:_TRANSCRIPT_MSG_CHARS] + " …[truncated]"


def _build_context_cached(sym: str, *, news: bool, funda: bool, diverg: bool) -> dict:
    """``build_context(...).to_dict()`` behind a 12s TTL cache.

    A failed build (InsufficientDataError etc.) is NOT cached — it
    propagates so a transient miss retries on the next turn.
    """
    key = (sym, news, funda, diverg)
    now = time.monotonic()
    with _ctx_lock:
        hit = _ctx_cache.get(key)
        if hit is not None and now - hit[0] < _CTX_TTL:
            _ctx_cache.move_to_end(key)
            return hit[1]
    ctx = build_context(
        sym,
        include_news=news,
        include_fundamentals=funda,
        include_divergence=diverg,
    ).compact()
    with _ctx_lock:
        _ctx_cache[key] = (now, ctx)
        _ctx_cache.move_to_end(key)
        while len(_ctx_cache) > _CTX_CAP:
            _ctx_cache.popitem(last=False)
    return ctx


def _build_alert_context(db, alert_trigger_id: int | None) -> dict | None:
    """Load the alert/trigger this chat was opened from, if any.

    Read-only — never re-triggers alert commentary generation, just
    reads what's already on the row (message/observed_value/
    ai_commentary if it's been generated by then).
    """
    if alert_trigger_id is None:
        return None
    trigger = db.query(AlertTrigger).filter(AlertTrigger.id == alert_trigger_id).first()
    if trigger is None:
        return None
    alert = db.query(Alert).filter(Alert.id == trigger.alert_id).first()
    recent_triggers = (
        db.query(AlertTrigger)
        .filter(AlertTrigger.alert_id == trigger.alert_id)
        .order_by(AlertTrigger.triggered_at.desc())
        .limit(10)
        .all()
    )
    return {
        # Keep the flat aliases for older prompt traces while the nested
        # sections make the evidence/fact boundary explicit for new turns.
        "symbol": trigger.symbol,
        "message": trigger.message,
        "observed_value": trigger.observed_value,
        "ai_commentary": trigger.ai_commentary,
        "triggered_at": str(trigger.triggered_at) if trigger.triggered_at else None,
        "alert": {
            "id": alert.id if alert is not None else trigger.alert_id,
            "name": alert.name if alert is not None else None,
            "symbol": alert.symbol if alert is not None else trigger.symbol,
            "condition_type": alert.condition_type if alert is not None else None,
            "parameter": alert.parameter if alert is not None else None,
            "is_enabled": alert.is_enabled if alert is not None else None,
        },
        "trigger": {
            "id": trigger.id,
            "symbol": trigger.symbol,
            "message": trigger.message,
            "observed_value": trigger.observed_value,
            "ai_commentary": trigger.ai_commentary,
            "triggered_at": str(trigger.triggered_at) if trigger.triggered_at else None,
        },
        "recent_triggers": [
            {
                "id": row.id,
                "symbol": row.symbol,
                "message": row.message,
                "observed_value": row.observed_value,
                "triggered_at": str(row.triggered_at) if row.triggered_at else None,
            }
            for row in recent_triggers
        ],
    }


@dataclass
class _Turn:
    """Everything a chat turn needs after context assembly — shared by
    the blocking (:func:`answer_chat_message`) and streaming
    (:func:`stream_chat_message`) paths."""

    symbol_blocks: list[dict]
    unavailable: list[str]
    market_baseline: dict | None
    transcript: list[tuple[str, str]]
    user_content: str
    alert_context: dict | None
    capped: bool
    base: list[str]
    planner_state: dict
    chart_state: dict | None = None
    regeneration_mode: str | None = None
    regeneration_scope: dict | None = None
    reused_context: bool = False
    preferences: dict | None = None
    previous_evidence_fingerprint: str | None = None
    # The confirmation request carried in from the previous turn. It may be
    # answered by this turn only; see _expire_carried_confirmation.
    carried_confirmation: dict | None = None

    @property
    def focus(self) -> list[str]:
        return [b["symbol"] for b in self.symbol_blocks if b["availability"]["engine_warm"]]

    @property
    def partial(self) -> list[str]:
        return [b["symbol"] for b in self.symbol_blocks if not b["availability"]["engine_warm"]]


def _prepare_turn(
    repo: ChatRepository,
    session_id: int,
    user_content: str,
    chart_state: dict | None = None,
    regeneration_mode: str | None = None,
    preferences: dict | None = None,
    regeneration_scope: dict | None = None,
) -> _Turn:
    """Assemble the turn's quant context, then persist the user message.

    Resolves the turn's tickers from the text (0..N, capped), attaches a
    cheap market-wide baseline when relevant, and fans the per-symbol
    context builds out over a thread pool.

    The user message is saved last (BF-13). Streaming reports the turn as
    started once this returns, so a failure while assembling context leaves
    nothing behind and the client can safely resend; saving first made that
    resend a duplicate.
    """
    session = repo.get_session(session_id)
    if session is None:
        raise ValueError(f"chat session {session_id} not found")

    if regeneration_mode == "refresh":
        _clear_context_cache()
        from backend.ai.market_baseline import invalidate_cache
        invalidate_cache()

    try:
        planner_state = json.loads(session.planner_state or "{}")
        if not isinstance(planner_state, dict):
            planner_state = {}
    except (TypeError, ValueError):
        planner_state = {}

    # The new message isn't saved yet, so history is exactly the prior
    # turns; it's passed separately as `new_message`, not duplicated into
    # the transcript. Long prior replies are clipped so the transcript
    # can't dominate.
    history = repo.get_messages(session_id, limit=_TRANSCRIPT_TURNS + 1)
    prior_assistant = next((m for m in reversed(history) if m.role == "assistant"), None)
    previous_fingerprint = None
    if prior_assistant is not None:
        try:
            previous_blocks = json.loads(prior_assistant.response_blocks or "[]")
            previous_fingerprint = next(
                (
                    block.get("quality", {}).get("evidence_fingerprint")
                    for block in previous_blocks
                    if isinstance(block, dict)
                    and isinstance(block.get("quality"), dict)
                    and block.get("quality", {}).get("evidence_fingerprint")
                ),
                None,
            )
        except (TypeError, ValueError):
            previous_fingerprint = None
    transcript = [(m.role, _clip(m.content)) for m in history][-_TRANSCRIPT_TURNS:]

    alert_context = _build_alert_context(repo.db, session.alert_trigger_id)

    base = (
        [session.symbol]
        if session.scope in ("symbol", "alert")
        and session.symbol
        and session.symbol != UNIVERSAL_SYMBOL
        else []
    )
    symbols, capped = resolve_turn_symbols(user_content, transcript, base)
    ambiguous_ranking = bool(_AMBIGUOUS_RANKING_REFERENCE.search(user_content))
    if ambiguous_ranking:
        # Do not inherit the prior chart ticker for an unscoped ranking
        # follow-up. It asks for a choice of ranking scope, not a symbol
        # overview, and must reach the clarification path without context or
        # model latency.
        symbols = []
        capped = False
    rejected_symbols = extract_unresolved_explicit_symbols(user_content, symbols)
    remembered_watchlist = planner_state.get("watchlist")

    # A specific, real, named watchlist ("analyze my X watchlist") gets
    # its member symbols folded in as if they'd been named individually
    # — but not when a cheaper, more specific intent already owns this
    # phrasing (_WATCHLIST_CONTENTS_INTENT's "what tickers are in X" /
    # _WATCHLIST_LIST_INTENT's "which watchlists do I have" both get
    # answered deterministically in _generate_reply without an AI call
    # or per-symbol context build at all — skip here so this doesn't
    # silently upgrade those into a full multi-ticker AI turn instead).
    if not _WATCHLIST_CONTENTS_INTENT.search(user_content) and not _WATCHLIST_LIST_INTENT.search(
        user_content
    ):
        named_watchlist = _resolve_named_watchlist(repo.db, user_content)
        named_wl_symbols = named_watchlist[1] if named_watchlist else []
        if named_watchlist:
            remembered_watchlist = named_watchlist[0]
        if named_wl_symbols:
            seen = {s.upper() for s in symbols}
            for sym in named_wl_symbols:
                sym = sym.upper()
                if sym not in seen:
                    seen.add(sym)
                    symbols.append(sym)
            cap = settings.ai.chat_max_tickers
            if len(symbols) > cap:
                symbols = symbols[:cap]
                capped = True

    single = len(symbols) == 1

    # Resolve a typed route before assembling optional context. Watchlist
    # intelligence is already deterministic and scanner-backed, so it does
    # not need the unrelated market baseline. This also keeps a scoped
    # timeframe follow-up on the fast semantic path.
    pre_route = route_semantic_intent(
        user_content,
        focus_symbols=symbols,
        planner_state=planner_state,
    )

    # What this turn actually asks for — skip the rest.
    want_news = single and bool(_NEWS_INTENT.search(user_content))
    want_funda = single and bool(_FUNDA_INTENT.search(user_content))
    want_stats = bool(_STATS_INTENT.search(user_content))
    want_baseline = (
        (not symbols and not rejected_symbols and not ambiguous_ranking and not _CALCULATION_HINT.search(user_content)
         and not (pre_route is not None and pre_route.action == "get_watchlist_intelligence"))
        or bool(_MARKET_INTENT.search(user_content))
        or bool(_ACTION_INTENT.search(user_content))
    )
    reused_context = regeneration_mode != "refresh" and _has_fresh_context_cache(
        symbols, news=want_news, funda=want_funda, diverg=single
    )

    def _ctx(sym: str) -> dict:
        return _build_context_cached(sym, news=want_news, funda=want_funda, diverg=single)

    # The market baseline and each per-symbol context are independent
    # blocking calls (DB reads, a scan, aux-data HTTP) — fan them out.
    symbol_blocks: list[dict] = []
    unavailable: list[str] = list(rejected_symbols)
    market_baseline: dict | None = None
    with ThreadPoolExecutor(max_workers=4) as ex:
        baseline_fut = ex.submit(build_market_baseline) if want_baseline else None
        ctx_futs = {sym: ex.submit(_ctx, sym) for sym in symbols}
        if baseline_fut is not None:
            try:
                market_baseline = baseline_fut.result()
            except Exception as e:  # noqa: BLE001 — baseline never blocks the turn
                logger.warning("chat market baseline failed: %s", e)
        for sym in symbols:  # preserve the resolved order
            try:
                ctx = ctx_futs[sym].result()
            except InsufficientDataError:
                unavailable.append(sym)
                continue
            except Exception as e:  # noqa: BLE001 — per-symbol, never abort the turn
                logger.warning("chat build_context(%s) failed: %s", sym, e)
                unavailable.append(sym)
                continue
            avail = _availability(ctx)
            symbol_blocks.append(
                {
                    "symbol": sym,
                    "context": _prune_context(ctx, avail, keep_stats=want_stats),
                    "availability": avail,
                }
            )

    # Persist only bounded, structured state. Never store tool payloads or
    # private prompt text here; the transcript remains the source for prose.
    remembered_symbols = [str(symbol).upper() for symbol in planner_state.get("current_symbols", []) if symbol]
    current_symbols = [block["symbol"] for block in symbol_blocks] or remembered_symbols[: settings.ai.chat_max_tickers]
    previous_ticker = planner_state.get("previous_ticker")
    if symbol_blocks and remembered_symbols and current_symbols != remembered_symbols:
        previous_ticker = remembered_symbols[0]
    elif previous_ticker:
        previous_ticker = str(previous_ticker).upper()
    turn_date_range = resolve_relative_date(user_content)
    current_calculation = _fallback_calculation(user_content) or _calculation_followup(
        user_content, planner_state.get("last_calculation_inputs")
    )
    saved_assumptions = planner_state.get("research_assumptions", [])
    if not isinstance(saved_assumptions, list):
        saved_assumptions = []
    effective_chart_state = dict(chart_state or planner_state.get("chart_state") or {})
    if regeneration_scope:
        if regeneration_scope.get("timeframe"):
            effective_chart_state["timeframe"] = regeneration_scope["timeframe"]
        if regeneration_scope.get("session"):
            effective_chart_state["session"] = regeneration_scope["session"]
    next_state = {
        "current_symbols": current_symbols,
        "rejected_symbols": rejected_symbols,
        "previous_ticker": previous_ticker,
        "watchlist": remembered_watchlist,
        # Carry forward only the bounded, server-resolved watchlist scope so
        # follow-up timeframe questions cannot broaden or guess the scope.
        "watchlist_scope": planner_state.get("watchlist_scope"),
        "date_range": turn_date_range or planner_state.get("date_range"),
        # Only a date named in this message scopes tools; the remembered
        # date_range above is context, not a silent filter on later turns.
        "active_date_range": turn_date_range,
        "timeframe": _extract_memory_value(user_content, r"\b(1m|2m|3m|5m|15m|30m|1h|4h|1d|1wk)\b"),
        "session": _extract_memory_value(user_content, r"\b(premarket|regular|after[- ]hours?|extended)\b"),
        "last_user_question": user_content[:300],
        "last_calculation_inputs": (
            current_calculation.model_dump(mode="json")
            if current_calculation is not None
            else planner_state.get("last_calculation_inputs")
        ),
        "last_tool_result": planner_state.get("last_tool_result"),
        "pending_confirmation": planner_state.get("pending_confirmation"),
        "research_assumptions": saved_assumptions[:200],
        "chart_state": effective_chart_state or None,
        "updated_at": now_ny().isoformat(),
    }
    if next_state["timeframe"] is None:
        next_state["timeframe"] = planner_state.get("timeframe")
    if next_state["session"] is None:
        next_state["session"] = planner_state.get("session")
    # A regeneration scope applies to that regeneration only; it is not
    # written into the remembered timeframe/session. Transient per-turn
    # request, overwritten (or cleared) every turn.
    active_scope = _regeneration_tool_scope(regeneration_scope)
    next_state["active_regeneration"] = (
        {"mode": regeneration_mode, "scope": active_scope}
        if regeneration_mode or active_scope
        else None
    )
    repo.set_planner_state(session_id, json.dumps(next_state, sort_keys=True))
    repo.add_message(session_id, "user", user_content)

    return _Turn(
        symbol_blocks=symbol_blocks,
        unavailable=unavailable,
        market_baseline=market_baseline,
        transcript=transcript,
        user_content=user_content,
        alert_context=alert_context,
        capped=capped,
        base=base,
        planner_state=next_state,
        chart_state=next_state.get("chart_state"),
        regeneration_mode=regeneration_mode,
        regeneration_scope=regeneration_scope,
        reused_context=reused_context,
        preferences=preferences,
        previous_evidence_fingerprint=previous_fingerprint,
        carried_confirmation=next_state.get("pending_confirmation"),
    )


def _baseline_symbols(value: object, *, depth: int = 0) -> list[str]:
    """Symbols named in the server-built market baseline (bounded walk).

    The baseline is prompt context the model may legitimately cite, so its
    symbols (index ETFs, scored watchlist names, alerts) are turn symbols
    for answer verification.
    """
    if depth > 4:
        return []
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in list(value.items())[:200]:
            if key in ("symbol", "ticker") and isinstance(child, str) and child:
                found.append(child.upper())
            else:
                found.extend(_baseline_symbols(child, depth=depth + 1))
    elif isinstance(value, list):
        for child in value[:200]:
            if isinstance(child, str) and child.isupper() and len(child) <= 6:
                found.append(child)
            else:
                found.extend(_baseline_symbols(child, depth=depth + 1))
    return found


def _material_change(turn: _Turn, current_fingerprint: str | None) -> bool:
    """Whether a regenerated answer's evidence differs from the original.

    Only a regeneration re-asks the same question; comparing an unrelated
    follow-up's evidence with the previous answer would flag every new topic.
    """
    return bool(
        turn.regeneration_mode
        and turn.previous_evidence_fingerprint
        and current_fingerprint
        and turn.previous_evidence_fingerprint != current_fingerprint
    )


def _expire_carried_confirmation(turn: _Turn) -> None:
    """Drop a confirmation request this turn did not answer.

    A server-authored confirmation prompt is valid for the very next user
    turn only. If that turn declined, changed the subject, or asked
    something else, a later unrelated "ok"/"yes" must not execute the old
    destructive action. A prompt created during this turn is a new dict,
    so identity distinguishes it from the carried-over one.
    """
    carried = turn.carried_confirmation
    if carried is not None and turn.planner_state.get("pending_confirmation") is carried:
        turn.planner_state["pending_confirmation"] = None


def _append_turn_observability(trace: list[dict], started_at: float, user_content: str) -> None:
    prompt_chars = max(
        [int(item.get("prompt_chars", 0) or 0) for item in trace if item.get("kind") == "model_call"]
        or [len(user_content)]
    )
    trace.append(build_turn_observability(trace, started_at=started_at, prompt_chars=prompt_chars))


def answer_chat_message(
    session_id: int,
    user_content: str,
    preferences: dict | None = None,
    chart_state: dict | None = None,
    regeneration_mode: str | None = None,
    regeneration_scope: dict | None = None,
    *,
    browser_data: dict | None = None,
) -> tuple[ChatMessage, bool, list[str], list[str], list[str]]:
    """Persist ``user_content``, generate a reply, persist the assistant
    ChatMessage, and return
    ``(message, grounded, focus, partial, unavailable)``.

    Universal chat (2026-09-10): the turn resolves its own tickers from
    the message text (0..N, capped), always attaches a cheap
    market-wide baseline, and builds per-ticker quant context for each
    resolved symbol. ``focus`` is the tickers that got a full
    (warm-engine) context block; ``partial`` the ones with only live
    price / indicators (not in a watchlist); ``unavailable`` the ones
    that were named but had no data at all.

    ``grounded`` / ``focus`` / ``unavailable`` aren't persisted columns
    — they're per-turn hints for the router's response.

    ``preferences`` (5.7.3) is the trader's browser-local operating
    preferences. They may tailor prompt terminology, emphasis, and
    suggested follow-ups, but never change a verified calculation, tool
    argument, or evidence-derived conclusion.

    Never raises for an expected failure mode — AI-off, a per-symbol
    context failure, or a malformed AI reply all produce a stored
    assistant message explaining that, not an exception. An unexpected
    failure (e.g. the session doesn't exist) still raises.
    """
    started_at = time.perf_counter()
    repo = ChatRepository()
    browser_token = _TURN_BROWSER_DATA.set(browser_data or None)
    try:
        turn = _prepare_turn(repo, session_id, user_content, chart_state, regeneration_mode, preferences, regeneration_scope)
        trace: list[dict] = []
        if turn.regeneration_mode:
            trace.append({"kind": "regeneration", "mode": turn.regeneration_mode, "reused_context": turn.reused_context})
        _append_context_evidence(trace, turn)
        try:
            reply_text, grounded, screened = _generate_reply(
                repo.db,
                turn.symbol_blocks,
                turn.unavailable,
                turn.market_baseline,
                turn.transcript,
                turn.user_content,
                turn.alert_context,
                turn.capped,
                turn.base,
                turn.planner_state,
                trace,
                preferences,
            )
        except Exception as e:  # noqa: BLE001 — the user row is already persisted
            logger.warning("Chat generation raised: %s", e)
            _rollback_quietly(repo.db)
            reply_text, grounded, screened = _TURN_FAILED_REPLY, False, []
        assistant_message, grounded, focus = _finish_turn(
            repo, session_id, turn, trace, reply_text, grounded, screened, started_at=started_at
        )
        return assistant_message, grounded, focus, turn.partial, turn.unavailable
    finally:
        _TURN_BROWSER_DATA.reset(browser_token)
        repo.close()


# The generation step raised after the user row was persisted. The same
# wording is used by both transports.
_TURN_FAILED_REPLY = "Something went wrong answering that — please try again."
_FINALIZE_FAILED_REPLY = (
    "I couldn't finish verifying that answer. Nothing was retried; please ask again if you still need it."
)


def _finish_turn(
    repo: ChatRepository,
    session_id: int,
    turn: _Turn,
    trace: list[dict],
    final_text: str | None,
    grounded: bool,
    screened: list[str],
    *,
    started_at: float,
) -> tuple[ChatMessage, bool, list[str]]:
    """Verify, build blocks, and persist exactly one assistant row.

    Shared by :func:`answer_chat_message` and :func:`stream_chat_message`.
    The user message is already persisted and actions may have run, so a
    failure while finishing still ends in one persisted assistant row;
    otherwise the client would retry and re-run the whole turn. Returns
    ``(message, grounded, focus)``.
    """
    try:
        if final_text is None:
            final_text, grounded = "AI is currently unavailable, so I can't answer that right now.", False
        if trace:
            turn.planner_state["last_tool_result"] = {
                "tool": trace[-1].get("tool"),
                "ok": trace[-1].get("ok"),
                "provider": trace[-1].get("provider"),
                "source_timestamp": trace[-1].get("source_timestamp"),
            }
        _expire_carried_confirmation(turn)
        repo.set_planner_state(session_id, json.dumps(turn.planner_state, sort_keys=True))
        if not _trace_is_action_only(trace):
            grounded = grounded and not turn.unavailable  # deterministic fail-safe
        # `screened` is populated only by the run_screen tool — tickers the
        # turn's own message never named, so turn.focus (derived from the
        # user's text) wouldn't otherwise include them, and the frontend's
        # per-ticker quick-action buttons (add to watchlist / create alert)
        # key off `focus`/`partial` alone.
        focus = list(dict.fromkeys([*turn.focus, *screened]))
        assign_evidence_ids(trace)
        verification = verify_answer(
            final_text,
            trace,
            allowed_symbols=[*turn.base, *focus, *turn.partial, *turn.unavailable, *_baseline_symbols(turn.market_baseline)],
            unavailable_symbols=turn.unavailable,
            user_content=turn.user_content,
        )
        if verification.safe_content:
            final_text = verification.safe_content
            grounded = False
        _append_turn_observability(trace, started_at, turn.user_content)
        current_fingerprint = evidence_fingerprint(trace)
        material_change = _material_change(turn, current_fingerprint)
        regeneration = {"mode": turn.regeneration_mode, "reused_context": turn.reused_context} if turn.regeneration_mode else None
        if regeneration is not None and turn.regeneration_scope:
            regeneration["scope"] = turn.regeneration_scope
        if regeneration is not None and material_change:
            regeneration["material_change_detected"] = True
        blocks = build_response_blocks(
            content=final_text,
            grounded=grounded,
            focus=focus,
            partial=turn.partial,
            unavailable=turn.unavailable,
            trace=trace,
            preferences=turn.preferences,
            chart_state=turn.chart_state,
            regeneration=regeneration,
            material_change_detected=material_change,
            verification=verification.model_dump(),
        )
        msg = repo.add_message(session_id, "assistant", final_text, response_blocks=blocks)
        # Transient per-turn payloads for the API; not persisted columns.
        msg.planner_trace = trace
        msg.response_blocks_payload = blocks
        return msg, grounded, focus
    except Exception as e:  # noqa: BLE001
        logger.warning("Chat turn finalization failed: %s", e)
        _rollback_quietly(repo.db)
        msg = repo.add_message(session_id, "assistant", _FINALIZE_FAILED_REPLY)
        msg.planner_trace = []
        msg.response_blocks_payload = []
        return msg, False, list(dict.fromkeys(turn.focus))


def stream_chat_message(
    session_id: int,
    user_content: str,
    preferences: dict | None = None,
    chart_state: dict | None = None,
    regeneration_mode: str | None = None,
    regeneration_scope: dict | None = None,
    *,
    browser_data: dict | None = None,
) -> Iterator[tuple]:
    """Streaming sibling of :func:`answer_chat_message`.

    Yields, in order:
      ``("meta", {"focus", "partial", "unavailable"})``  — once, after prep
      ``("delta", text)``                                — 0..N incremental reply chunks
      ``("final", (message, grounded, focus, partial, unavailable))``  — once

    Same never-raise-for-an-expected-failure contract: AI-off / a
    malformed reply / a provider error all still end in a persisted
    assistant row and a ``final`` event. An unexpected failure before
    prep completes (e.g. bad session id) propagates on first iteration.
    """
    started_at = time.perf_counter()
    repo = ChatRepository()
    # Set in the consumer's context; the stream is drained by one thread.
    browser_token = _TURN_BROWSER_DATA.set(browser_data or None)
    try:
        turn = _prepare_turn(repo, session_id, user_content, chart_state, regeneration_mode, preferences, regeneration_scope)
        yield (
            "meta",
            {
                "focus": turn.focus,
                "partial": turn.partial,
                "unavailable": turn.unavailable,
            },
        )

        final_text: str | None = None
        grounded = False
        screened: list[str] = []
        trace: list[dict] = []
        if turn.regeneration_mode:
            trace.append({"kind": "regeneration", "mode": turn.regeneration_mode, "reused_context": turn.reused_context})
        _append_context_evidence(trace, turn)
        try:
            for kind, payload in _generate_reply_streaming(repo.db, turn, trace):
                if kind == "delta":
                    yield ("delta", payload)
                else:  # "result"
                    final_text, grounded, screened = payload
        except Exception as e:  # noqa: BLE001 — the user row is already persisted
            logger.warning("Chat stream generation raised: %s", e)
            _rollback_quietly(repo.db)
            final_text, grounded, screened = _TURN_FAILED_REPLY, False, []

        msg, grounded, focus = _finish_turn(
            repo, session_id, turn, trace, final_text, grounded, screened, started_at=started_at
        )
        yield ("final", (msg, grounded, focus, turn.partial, turn.unavailable))
    finally:
        try:
            _TURN_BROWSER_DATA.reset(browser_token)
        except ValueError:  # generator finalized from another context
            _TURN_BROWSER_DATA.set(None)
        repo.close()


def _availability(ctx: dict) -> dict:
    """Classify how much live quant data a context dict actually carries.

    ``engine_warm`` is the key signal: a ticker not in a watchlist gets
    a live price + best-effort indicators from ``build_context`` but its
    trend engine was never fed, so its multi-timeframe scores are all
    'unknown'/0.
    """
    tf_scores = ctx.get("timeframe_scores") or {}
    warm = bool(ctx.get("trend_state")) or any(
        (s or {}).get("direction") not in (None, "unknown", "neutral")
        and (s or {}).get("confidence", 0) > 0
        for s in tf_scores.values()
    )
    momentum = ctx.get("momentum") or {}
    return {
        "engine_warm": warm,
        "has_price": ctx.get("price") is not None,
        "has_rsi": momentum.get("rsi") is not None,
        "has_support_resistance": bool(ctx.get("support_resistance")),
        "note": ""
        if warm
        else (
            "not in your watchlist — live price / indicators only, "
            "no Trend by Timeframe or confidence data"
        ),
    }


def _prune_context(ctx: dict, avail: dict, *, keep_stats: bool = False) -> dict:
    """Trim ``build_context``'s dict down to what a chat turn needs:

    - drop empty sections,
    - for a cold engine, drop the composite scores (market_structure /
      trend_transition) that read like a confidence number the model
      must not quote for an untracked name,
    - drop the verbose ``historical_signal_stats`` / ``track_record``
      sections unless the turn asked about win rates / track record,
    - cap ``news`` to the 4 most recent items.
    """
    cold_only = {"market_structure", "trend_transition"}
    stats_only = {"historical_signal_stats", "track_record"}
    out: dict = {}
    for k, v in ctx.items():
        if v is None or v == {} or v == []:
            continue
        if not avail["engine_warm"] and k in cold_only:
            continue
        if k in stats_only and not keep_stats:
            continue
        if k == "news" and isinstance(v, list):
            v = v[:4]
        out[k] = v
    return out
# Requests whose answer is usually the app's own text rather than the model's.
_MUTATING_REQUEST = re.compile(
    r"\b(?:add|create|delete|remove|cancel|save|modify|update|relabel|set\s+(?:up\s+)?an?)\b", re.I
)

# One wording per model failure, whichever transport ran the turn.
_MODEL_FAILURE_REPLIES = {
    "ai_error": "Something went wrong reaching the AI provider — please try again.",
    "parse_error": "I couldn't process that — could you rephrase?",
}


def _holds_streamed_text(user_content: str) -> bool:
    """Whether this turn's model text must not be streamed live (BF-08).

    For these requests the app usually replaces the model's "reply" with its
    own result (an action, a calculation, a confirmation or a clarification),
    and a model placeholder can read like a completion ("Done — alert set")
    before anything has happened. Holding it only loses the typing effect:
    the final frame carries the answer.
    """
    return any(
        pattern.search(user_content)
        for pattern in (
            _ACTION_INTENT,
            _MUTATING_REQUEST,
            _WATCHLIST_ADD_INTENT,
            _WATCHLIST_CREATE_INTENT,
            _WATCHLIST_REMOVE_FROM_INTENT,
            _WATCHLIST_DELETE_INTENT,
            _DELETE_WATCHLIST_FALLBACK,
            _CALCULATION_HINT,
            _ASSUMPTION_SAVE_INTENT,
            _AFFIRM_INTENT,
        )
    )


def _reply_without_model(
    db,
    symbol_blocks: list[dict],
    unavailable: list[str],
    market_baseline: dict | None,
    transcript: list[tuple[str, str]],
    user_content: str,
    alert_context: dict | None,
    base_symbols: list[str],
    planner_state: dict | None,
    trace: list[dict] | None,
    preferences: dict | None,
    *,
    started_at: float,
) -> tuple[str, bool, list[str]] | None:
    """A reply the server can give without the model, or ``None``.

    Confirmations, clarifications, no-data answers, deterministic routes and
    the AI-off fallback, in that order.
    """
    confirmed = _confirm_pending_action(
        db, user_content, symbol_blocks, unavailable, market_baseline,
        transcript, alert_context, trace, planner_state, preferences,
    )
    if confirmed is not None:
        return confirmed
    if _AMBIGUOUS_RANKING_REFERENCE.search(user_content):
        if trace is not None:
            trace.append({"kind": "server_reply", "trusted": True})
        return (
            "Do you mean the weakest name from your last watchlist result, "
            "or the weakest name across the market? Please specify a watchlist "
            "or say market-wide.",
            False,
            [],
        )
    # Friendly degrade for a legacy single-symbol session whose only
    # ticker has no data (keeps the pre-universal wording). A watchlist
    # request still goes through: adding the ticker is how it gets data.
    if (
        not symbol_blocks
        and unavailable
        and base_symbols
        and set(unavailable) == {s.upper() for s in base_symbols}
        and not any(
            pattern.search(user_content)
            for pattern in (
                _WATCHLIST_ADD_INTENT,
                _WATCHLIST_CREATE_INTENT,
                _WATCHLIST_REMOVE_FROM_INTENT,
                _WATCHLIST_DELETE_INTENT,
            )
        )
    ):
        return f"I don't have enough data on {unavailable[0]} yet to answer that.", False, []

    if not symbol_blocks and unavailable and _COMPARISON_INTENT.search(user_content):
        names = ", ".join(unavailable)
        return f"I don't have enough verified data for {names} to compare them.", False, []

    if not symbol_blocks:
        m = _WATCHLIST_CONTENTS_INTENT.search(user_content)
        if m:
            reply = _watchlist_contents_reply(db, m.group("name1") or m.group("name2") or "")
            if reply:
                if trace is not None:
                    trace.append({"kind": "server_reply", "trusted": True})
                return reply, True, []
        if _WATCHLIST_LIST_INTENT.search(user_content):
            if trace is not None:
                trace.append({"kind": "server_reply", "trusted": True})
            return _watchlist_list_reply(db), True, []
        if unavailable and (planner_state or {}).get("rejected_symbols"):
            names = ", ".join(unavailable)
            if trace is not None:
                trace.append({"kind": "server_reply", "trusted": True})
            return f"I couldn't find current verified market data for {names}. Please check the ticker and try again.", False, []
        remembered = (planner_state or {}).get("current_symbols", [])
        if _AMBIGUOUS_REFERENCE.search(user_content) and len(remembered) > 1:
            return f"Which ticker do you mean: {', '.join(remembered[:settings.ai.chat_max_tickers])}?", False, []

    det = _run_deterministic_shortcircuit(
        db, user_content, symbol_blocks, unavailable, market_baseline,
        transcript, alert_context, trace, planner_state,
        preferences=preferences, started_at=started_at,
    )
    if det is not None:
        return det

    if not _ai_enabled():
        _trace_model_route(trace, "fallback", "deterministic")
        if trace is not None:
            trace.append({"kind": "server_reply", "trusted": True})
        return _deterministic_context_reply(symbol_blocks, unavailable, market_baseline)
    return None


def _reply_events(
    db,
    symbol_blocks: list[dict],
    unavailable: list[str],
    market_baseline: dict | None,
    transcript: list[tuple[str, str]],
    user_content: str,
    alert_context: dict | None,
    capped: bool,
    base_symbols: list[str],
    planner_state: dict | None = None,
    trace: list[dict] | None = None,
    preferences: dict | None = None,
    *,
    stream_model: bool = False,
) -> Iterator[tuple]:
    """The one generation path for a Chat turn, for both transports (BF-09).

    Yields ``("delta", text)`` 0..N times, then exactly one
    ``("result", (text, grounded, screened))``; ``screened`` is the tickers a
    run_screen call surfaced, for the caller to fold into ``focus``.

    ``stream_model`` only changes how the model is called: streamed with live
    deltas (when ``chat_streaming`` is on) or as one completion. Routing,
    fallbacks and failure wording are shared, so the transports can't drift
    apart again. Expected failures become a reply; an unexpected exception
    propagates to the caller, which persists a failure reply (BF-11).
    """
    started_at = time.monotonic()
    early = _reply_without_model(
        db, symbol_blocks, unavailable, market_baseline, transcript, user_content,
        alert_context, base_symbols, planner_state, trace, preferences, started_at=started_at,
    )
    if early is not None:
        yield ("result", early)
        return

    budget = _prompt_token_budget(CHAT_SYSTEM_PROMPT, 500)
    prompt = build_chat_prompt(
        symbol_blocks,
        unavailable,
        market_baseline,
        transcript,
        user_content,
        alert_context,
        capped_note=_capped_note(capped, symbol_blocks),
        token_budget=budget,
        chart_state=(planner_state or {}).get("chart_state"),
        preferences=preferences,
        regeneration=(planner_state or {}).get("active_regeneration"),
    )
    synthesis_model = _chat_route_model("synthesis", _chat_route_model("planning"))
    repair_model = _chat_route_model("repair")
    _trace_model_route(trace, "synthesis", synthesis_model)
    hold = _holds_streamed_text(user_content)
    if stream_model and _ai_setting("chat_streaming"):
        parsed, failure_reason = yield from _stream_and_parse(
            prompt, synthesis_model, repair_model, trace=trace, hold=hold
        )
    else:
        parsed, failure_reason = _complete_and_parse(
            prompt,
            CHAT_SYSTEM_PROMPT,
            500,
            synthesis_model,
            repair_model,
            trace=trace,
            role="synthesis",
        )
        if (
            stream_model
            and parsed is not None
            and not hold
            and parsed.action == "none"
            and not parsed.wants_reanalysis
            and parsed.reply
        ):
            # A one-shot provider returns the reply whole: one delta, and only
            # once it is known to be the model's own answer.
            yield ("delta", parsed.reply)
    if parsed is None:
        yield ("result", (_MODEL_FAILURE_REPLIES[failure_reason or "ai_error"], False, []))
        return
    yield (
        "result",
        _run_turn_actions(
            db,
            parsed,
            symbol_blocks,
            unavailable,
            market_baseline,
            transcript,
            user_content,
            alert_context,
            trace=trace,
            planner_state=planner_state,
            preferences=preferences,
        ),
    )


def _generate_reply(
    db,
    symbol_blocks: list[dict],
    unavailable: list[str],
    market_baseline: dict | None,
    transcript: list[tuple[str, str]],
    user_content: str,
    alert_context: dict | None,
    capped: bool,
    base_symbols: list[str],
    planner_state: dict | None = None,
    trace: list[dict] | None = None,
    preferences: dict | None = None,
) -> tuple[str, bool, list[str]]:
    """Blocking transport: :func:`_reply_events` drained to its result.

    Returns ``(text, grounded, screened)``. When the model asks for
    ``wants_reanalysis``, the reply is a real analyze_symbol() run instead.
    """
    for kind, payload in _reply_events(
        db,
        symbol_blocks,
        unavailable,
        market_baseline,
        transcript,
        user_content,
        alert_context,
        capped,
        base_symbols,
        planner_state,
        trace,
        preferences,
        stream_model=False,
    ):
        if kind == "result":
            return payload
    raise RuntimeError("chat reply generation ended without a result")  # pragma: no cover


def _capped_note(capped: bool, symbol_blocks: list[dict]) -> str | None:
    if not capped:
        return None
    shown = ", ".join(b["symbol"] for b in symbol_blocks) or "the first few"
    return f"(You named more tickers than I can dig into at once — I looked at {shown}.)"


def _deterministic_context_reply(
    symbol_blocks: list[dict], unavailable: list[str], market_baseline: dict | None = None
) -> tuple[str, bool, list[str]]:
    """Answer from already-built context when AI is unavailable.

    This intentionally reports only fields present in the verified context;
    it never invents a trend, catalyst, or recommendation.
    """
    if not symbol_blocks:
        regime = (market_baseline or {}).get("regime_live")
        if isinstance(regime, dict) and regime:
            details = []
            if regime.get("regime") or regime.get("market_regime"):
                details.append(f"regime {str(regime.get('regime') or regime.get('market_regime')).replace('_', ' ')}")
            if isinstance(regime.get("volatility_state"), str):
                details.append(f"volatility {regime['volatility_state']}")
            if isinstance(regime.get("momentum"), (int, float)):
                details.append(f"momentum {float(regime['momentum']):+.2f}")
            if details:
                return "AI is unavailable, so here is a verified market-context snapshot: " + "; ".join(details) + ".", True, []
        return "AI is unavailable and no verified market or symbol data is loaded for this question.", False, []
    rows: list[str] = []
    focus: list[str] = []
    for block in symbol_blocks:
        symbol = block.get("symbol", "?")
        context = block.get("context") or {}
        price = context.get("price")
        change = context.get("change_percent")
        parts = [symbol]
        if isinstance(price, (int, float)):
            parts.append(f"price ${price:,.4f}")
        if isinstance(change, (int, float)):
            parts.append(f"change {change:+.2f}%")
        rows.append(" — ".join(parts))
        focus.append(symbol)
    unavailable_note = f" Unavailable: {', '.join(unavailable)}." if unavailable else ""
    return (
        "AI is unavailable, so here is a verified context-only snapshot: "
        + "; ".join(rows)
        + ". No narrative or recommendation was generated."
        + unavailable_note,
        True,
        focus,
    )


def _generate_reply_streaming(
    db, turn: _Turn, trace: list[dict] | None = None
) -> Iterator[tuple]:
    """Streaming transport: :func:`_reply_events` with live model deltas.

    Yields ``("delta", text)`` 0..N times, then exactly one
    ``("result", (final_text, grounded, screened))``. The result is
    authoritative: the client replaces any streamed text with it.
    """
    yield from _reply_events(
        db,
        turn.symbol_blocks,
        turn.unavailable,
        turn.market_baseline,
        turn.transcript,
        turn.user_content,
        turn.alert_context,
        turn.capped,
        turn.base,
        turn.planner_state,
        trace,
        turn.preferences,
        stream_model=True,
    )
