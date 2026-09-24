"""
Version 4, AI feature 4 — conversational AI chat panel.

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
backtest or deterministic calculation (see _run_action and its handlers below). Still no open-ended
function-calling loop: exactly
one action, decided in the same completion call that would otherwise
produce a normal reply, executed synchronously before the turn's
assistant message is persisted. Destructive actions (delete_alert,
remove_from_watchlist, delete_watchlist, save_to_journal) get a hard,
backend-enforced confirm gate in _finalize_parsed — a destructive action never runs
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
top-level key), _run_turn_actions chains multiple ONE-action
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

import contextvars
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.ai.analyze import analyze_symbol
from backend.ai.answer_verifier import assign_evidence_ids, verify_answer
from backend.ai.calculator import CalculationRequest
from backend.ai.chat_observability import build_turn_observability, sanitize_arguments
from backend.ai.chat_symbols import extract_unresolved_explicit_symbols, resolve_turn_symbols
from backend.ai.context import InsufficientDataError, build_context
from backend.ai.manager import ai_manager
from backend.ai.market_baseline import build_market_baseline
from backend.ai.prompt import (
    CHAT_CONTINUATION_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    ChatReplyResponse,
    UncertaintyResponse,
    build_chat_prompt,
    parse_chat_reply,
)
from backend.ai.provider import StreamAttribution
from backend.ai.reply_stream import ReplyExtractor
from backend.ai.response_blocks import build_response_blocks, evidence_fingerprint
from backend.ai.semantic_router import route_semantic_intent

# Chat runs its sync generator helpers on loop-less worker threads
# (ThreadPoolExecutor / asyncio.to_thread), so the async AI calls are
# bridged with run_sync/stream_sync rather than awaited.
from backend.ai.sync_bridge import run_sync, stream_sync
from backend.ai.tool_registry import (
    ToolRequest,
    default_registry,
    normalize_session,
    normalize_timeframe,
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

# Multi-step actions (see module docstring, 2026-09-16). A cheap
# deterministic gate on the trader's OWN message — chaining is only
# ever attempted when this matches, so an ordinary single-action turn
# never pays for the extra completion call.
_MULTI_STEP_HINT = re.compile(
    r"(?:\bthen\b|\balso\b|;|\band\s+(?:add|remove|create|delete|modify|"
    r"show|get|compare|check|run|calculate|explain|review|save|export|screen)\b)",
    re.I,
)
_AMBIGUOUS_REFERENCE = re.compile(
    r"\b(it|that stock|that ticker|the previous ticker|this one|that one)\b", re.I
)
_AMBIGUOUS_RANKING_REFERENCE = re.compile(
    r"\b(?:which one|which name|who)\b.*\b(?:weakest|strongest|best|worst|laggard|leader)\b",
    re.I,
)
# Per-turn budget defaults (plan 5.3.1); settings override each one.
_MAX_TOOL_CALLS = 5
_MAX_PLANNING_CALLS = 2
_MAX_TURN_TOKENS = 60_000
_MAX_TURN_SECONDS = 30.0
_CONTINUATION_MAX_TOKENS = 300


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(int(getattr(settings.ai, name, default)), high))
    except (TypeError, ValueError):
        return default


def _tool_call_limit() -> int:
    """Actions one turn may execute, the first included."""
    limit = _bounded_int("chat_max_tool_calls", _MAX_TOOL_CALLS, 1, 10)
    legacy = getattr(settings.ai, "chat_max_chain_steps", None)
    if isinstance(legacy, int) and legacy > 0:
        # Deprecated AI_CHAT_MAX_CHAIN_STEPS still caps an existing setup.
        limit = min(limit, legacy)
    return limit


def _planning_call_limit() -> int:
    """Follow-up model calls one turn may spend choosing the next step."""
    return _bounded_int("chat_max_planning_calls", _MAX_PLANNING_CALLS, 0, 8)


def _turn_token_budget() -> int:
    """Estimated tokens one turn may spend across all model calls."""
    return _bounded_int("chat_max_turn_tokens", _MAX_TURN_TOKENS, 4_000, 400_000)


def _estimate_tokens(*texts: str | None) -> int:
    """The same chars/4 estimate the prompt builder uses for its size guard."""
    return sum(len(text or "") for text in texts) // 4 + 1


def _turn_tokens_used(trace: list[dict] | None) -> int:
    return sum(
        int(item.get("estimated_tokens") or 0)
        for item in (trace or [])
        if item.get("kind") == "model_call"
    )


def _prompt_token_budget(system: str, max_output_tokens: int) -> int:
    """Prompt-size budget for one model call.

    Keeps the historical ``max_tokens - 500`` sizing, but never lets a single
    call's prompt, system text, and reply exceed the whole turn's budget.
    """
    historical = max(2000, ai_manager.settings.max_tokens - 500)
    remaining = _turn_token_budget() - _estimate_tokens(system) - max_output_tokens
    return max(1000, min(historical, remaining))


def _turn_budget_seconds() -> float:
    """Read the bounded wall-clock budget for chained planning safely."""
    configured = getattr(settings.ai, "chat_max_turn_seconds", _MAX_TURN_SECONDS)
    try:
        return max(1.0, min(float(configured), 120.0))
    except (TypeError, ValueError):
        return _MAX_TURN_SECONDS


def _action_signature(parsed) -> str:
    """Return a stable, privacy-safe key for one planned action.

    The key is used only for per-turn duplicate suppression; it never leaves
    the process and does not include the user's prose or provider payload.
    """
    arguments = {
        key: value
        for key, value in {
            "symbol": parsed.action_symbol,
            "watchlist": parsed.action_watchlist,
            "target_id": parsed.action_target_id,
            "condition_type": parsed.action_condition_type,
            "parameter": parsed.action_parameter,
            "label": parsed.action_label,
            "entity_type": parsed.action_entity_type,
            "query": parsed.action_query,
            "calculation": parsed.action_calculation.model_dump(mode="json") if parsed.action_calculation else None,
            "tool_arguments": parsed.action_tool_arguments,
        }.items()
        if value is not None
    }
    return f"{parsed.action}:{json.dumps(arguments, sort_keys=True, default=str)}"


def _action_trace_arguments(parsed) -> dict:
    """Build a bounded, non-secret action argument snapshot for observability."""
    arguments = dict(parsed.action_tool_arguments or {})
    for key, value in {
        "symbol": parsed.action_symbol,
        "watchlist": parsed.action_watchlist,
        "target_id": parsed.action_target_id,
        "condition_type": parsed.action_condition_type,
        "parameter": parsed.action_parameter,
        "label": parsed.action_label,
    }.items():
        if value is not None and key not in arguments:
            arguments[key] = value
    if parsed.action_calculation is not None:
        arguments["calculation"] = parsed.action_calculation.model_dump(mode="json")
    return sanitize_arguments(arguments)


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


def _cacheable_chat_action(action: str) -> bool:
    """Return whether an action is safe to reuse within one turn."""
    return action in {
        "calculate",
        "get_quote",
        "get_bars",
        "get_indicator",
        "get_support_resistance",
        "get_market_regime",
        "get_market_context",
        "get_news",
        "get_fundamentals",
        "get_options_snapshot",
        "get_watchlist",
        "get_watchlist_intelligence",
        "get_risk_dashboard",
        "get_trade_journal",
        "get_application_help",
        "get_alerts",
        "get_signal_history",
        "get_saved_scans",
        "get_sector_data",
        "get_trend",
        "get_confluence",
        "get_relative_strength",
        "get_tape_state",
        "get_session_stats",
        "get_calendar",
        "why_did_it_move",
        "what_changed",
        "compare_symbols",
        "scenario_analysis",
        "historical_similarity",
        "signal_explanation",
        "counterargument_review",
        "sensitivity_analysis",
        "market_event_timeline",
        "anomaly_analysis",
        "run_screen",
    }

# Per-turn intent — keeps aux-data HTTP and prompt tokens off turns that
# don't ask for that material. Each pattern is deliberately generous:
# a false positive just adds a section, a false negative omits one the
# trader can ask for again.
_NEWS_INTENT = re.compile(
    r"\b(news|headline|catalyst|announc\w+|report\w*|filing|earnings|upgrade|"
    r"downgrade|analyst|rating|price target|what happened|sell[- ]?off|selloff|"
    r"rall\w+|spik\w+|plung\w+|surg\w+|why (is|did|are|has|s|'s)\b|"
    r"mov\w+ (on|because|after|due))\b",
    re.I,
)
_FUNDA_INTENT = re.compile(
    r"\b(fundamental\w*|valuation|p/?e\b|pe ratio|peg\b|eps\b|revenue|sales|"
    r"profit\w*|margin\w*|balance sheet|debt|cash ?flow|fcf\b|dividend|yield|"
    r"market ?cap|book value|financ\w+|forward pe|multiple|buyback)\b",
    re.I,
)
_MARKET_INTENT = re.compile(
    r"\b(market|markets|s&p|spx|spy\b|nasdaq|dow\b|russell|indices|index|"
    r"regime|risk[- ]?on|risk[- ]?off|breadth|rotation|macro|the fed|rates|"
    r"vix\b|sentiment|overall|broad(er)?|environment|backdrop|my (watchlist|"
    r"names|book|portfolio))\b",
    re.I,
)
_STATS_INTENT = re.compile(
    r"\b(win[- ]?rate|hit[- ]?rate|historical\w*|backtest|track record|"
    r"how often|batting average|expectancy|sample size|base rate)\b",
    re.I,
)
# A turn that might use the alert/watchlist action tools needs
# active_alerts (for delete_alert to find a real id) even when it's a
# focused single-ticker question that would otherwise skip the market
# baseline — force it on regardless of the market-wide gate below.
_ACTION_INTENT = re.compile(r"\b(alert\w*|notify|remind\w*|watch ?list\w*|track\w*)\b", re.I)
_OPTIONS_TOOL_INTENT = re.compile(r"\b(options?|calls?|puts?|option chain|implied volatility|open interest|put[/-]?call)\b", re.I)
_HISTORICAL_TOOL_INTENT = re.compile(r"\b(historical|history|past bars?|candles?|price history|ohlc|replay)\b", re.I)
_SCANNER_TOOL_INTENT = re.compile(r"\b(screen|scanner|scan|find stocks?|find tickers?|filter my watchlist)\b", re.I)
_RISK_TOOL_INTENT = re.compile(r"\b(risk dashboard|portfolio risk|position risk|my positions|exposure|drawdown)\b", re.I)
_SIGNAL_HISTORY_INTENT = re.compile(
    r"\b(signal history|past signals?|recent signals?|previous signals?|signal log|recorded signals?|"
    r"signals? (?:from|since|over|on|last)\b|when did .{1,40} (?:flip|turn) (?:bullish|bearish))",
    re.I,
)
_SAVED_SCANS_INTENT = re.compile(r"\b(saved scans?|saved (?:scanner )?presets?|scan(?:ner)? presets?|my presets?)\b", re.I)
_JOURNAL_TOOL_INTENT = re.compile(r"\b(trade journal|journal entries?|trading journal|mistakes? review)\b", re.I)
_ALERTS_TOOL_INTENT = re.compile(r"\b(my alerts?|active alerts?|alert rules?|notifications?)\b", re.I)
_WHY_MOVE_INTENT = re.compile(r"\b(why did .* move|why is .* (up|down)|what caused .* (move|drop|surge)|explain .* move)\b", re.I)
_WHAT_CHANGED_INTENT = re.compile(r"\b(what changed|what has changed|since yesterday|since my last visit|changed since)\b", re.I)
_COMPARISON_INTENT = re.compile(r"\b(compare|comparison|rank|ranking|strongest|weakest|best performing|worst performing|which .* (higher|lower|stronger|weaker))\b", re.I)
_SCENARIO_INTENT = re.compile(r"\b(what if|scenario|under a sell[- ]?off|drops?\b|falls?\b|rises?\b|stop (?:moves?|changes?)|shock)\b", re.I)
_SIMILARITY_INTENT = re.compile(r"\b(similar (?:setup|pattern|situation)|prior situations?|historical pattern|historical similarity|lookalike)\b", re.I)
_SIGNAL_EXPLANATION_INTENT = re.compile(r"\b(explain (?:the )?(?:signal|setup)|why (?:is|was) .* signal|signal explanation|which indicators triggered|what confirms .* signal)\b", re.I)
_COUNTERARGUMENT_INTENT = re.compile(r"\b(what invalidates|what would invalidate|invalidation|counterargument|counter-argument|opposing evidence|what could prove .* wrong|what would break)\b", re.I)
_SENSITIVITY_INTENT = re.compile(r"\b(sensitivity|how sensitive|vary (?:the )?(?:entry|stop|target|position size)|assumption impact)\b", re.I)
_TIMELINE_INTENT = re.compile(r"\b(event timeline|timeline|what happened (?:before|after|around)|before the breakout|after earnings|between .* and)\b", re.I)
_ANOMALY_INTENT = re.compile(r"\b(anomal(?:y|ies)|unusual|abnormal|outlier|z[- ]?score|spike|surge|spread widening|unusual activity)\b", re.I)
_ASSUMPTION_INTENT = re.compile(
    r"\b(assumption\w*|thesis|invalidation condition\w*|research premise\w*|"
    r"(?:save|remember|record|store|track|keep)\b[^.!?]{0,80}\b(?:stop|growth|catalyst|volatility|invalidation))\b",
    re.I,
)
_ASSUMPTION_SAVE_INTENT = re.compile(
    r"\b(save|remember|record|store|track|keep)\b[^.!?]{0,80}\b(assumption\w*|thesis|premise|stop|growth|catalyst|volatility|invalidation)\b",
    re.I,
)


def _parse_assumption_records(user_content: str, symbol: str | None) -> list[dict]:
    """Parse the common compact save form without asking the model to do math.

    The model remains available for richer prose, but these typed patterns make
    the normal ``save ... growth 10%, stop $210`` workflow deterministic and
    auditable before any state is written.
    """
    text = user_content.strip()
    records: list[dict] = []
    patterns = (
        ("growth", r"\b(?:expected\s+)?growth(?:\s+assumption)?\s*(?:of|is|at|=|:)??\s*(-?\d+(?:\.\d+)?)\s*%", "%"),
        ("stop", r"\bstop(?:\s+price)?\s*(?:at|is|=|:)??\s*\$?(-?\d+(?:\.\d+)?)", "price"),
        ("volatility", r"\bvolatility\s*(?:of|is|at|=|:)??\s*(-?\d+(?:\.\d+)?)\s*%", "%"),
        ("catalyst_date", r"\bcatalyst(?:\s+date)?\s*(?:on|is|=|:)??\s*(\d{4}-\d{2}-\d{2})", "date"),
    )
    for category, pattern, unit in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        value = match.group(1)
        numeric = float(value) if category != "catalyst_date" else value
        records.append({
            "symbol": symbol,
            "category": category,
            "statement": f"{category.replace('_', ' ')} assumption: {value}{'%' if unit == '%' else ''}",
            "expected_value": numeric,
            "unit": unit,
            "source": "user",
        })
    invalidation = re.search(r"\binvalidation(?:\s+condition)?\s*(?:is|when|if|=|:)??\s*(.+?)(?:[.;]|$)", text, re.I)
    if invalidation:
        statement = invalidation.group(1).strip()
        if statement:
            records.append({
                "symbol": symbol,
                "category": "invalidation",
                "statement": statement,
                "source": "user",
            })
    if not records:
        generic = re.search(r"\b(?:assumption|thesis|premise)\s*(?:is|:)?\s*(.+)$", text, re.I)
        if generic and generic.group(1).strip():
            records.append({
                "symbol": symbol,
                "category": "custom",
                "statement": generic.group(1).strip().rstrip("."),
                "source": "user",
            })
    return records

# Deterministic safety net for delete_watchlist intent the model leaves
# untagged (action="none", prose reply instead). Confirmed live
# 2026-09-11 that the configured chat model still does this on some
# turns even after two rewrites of CHAT_SYSTEM_PROMPT rule 10/11 — a
# model instruction-following gap, not a prompt-wording one, so it
# needs a backstop that doesn't depend on the model cooperating.
# Deliberately narrow: only "delete/remove/clear ... watchlist" with no
# ticker resolved this turn (see ``_fallback_action``), so it never
# misfires on remove_from_watchlist ("remove AAPL from my watchlist")
# or a non-destructive mention ("what's on my watchlist").
_DELETE_WATCHLIST_FALLBACK = re.compile(
    r"\b(delete|remove|clear|trash|get rid of)\b[^.!?]{0,20}\bwatch ?list\b", re.I
)

# Same rationale, for the turn AFTER a server-authored confirmation
# question (``_confirm_prompt``): confirmed live 2026-09-11 that the
# model can answer its own "yes" turn by narrating a fake completion in
# "reply" while still leaving action="none" instead of re-proposing the
# action with action_confirmed=true. These parse the exact, fixed-format
# questions _confirm_prompt composes back out of the prior assistant
# turn, so the follow-up executes deterministically instead of trusting
# the model to remember and restate the pending action correctly.
_AFFIRM_INTENT = re.compile(
    r"^\s*(yes\b|yep\b|yeah\b|yup\b|confirm(ed)?\b|do it\b|go ahead\b|sure\b|ok(ay)?\b)", re.I
)
_CONFIRM_DELETE_WATCHLIST_RE = re.compile(r'^Delete the watchlist "(?P<name>.+)"\? This removes')
_CONFIRM_REMOVE_FROM_WATCHLIST_RE = re.compile(
    r'^Remove (?P<sym>\S+)(?: from "(?P<wl>[^"]+)")?\? Say yes to confirm\.$'
)

_CALC_NUMBER_RE = re.compile(r"(?:\$|USD\s*)?([0-9][0-9,]*(?:\.\d+)?)", re.I)
_CALCULATION_HINT = re.compile(
    r"\b(allocation|cagr|correlation|drawdown|position size|risk[- ]reward|"
    r"return|volatility|expected move|breakeven|calculate|percent(?:age)? change)\b",
    re.I,
)
_REUSE_MEMORY_HINT = re.compile(r"\b(previous|prior|same|those|last|it)\b", re.I)
# Position-risk wording: "buy 200 AAPL at $220, stop $212", "300 shares at
# 50 with a stop at 47". Each field is read from its own labelled phrase,
# never from number order, so a missing label means a missing input.
# A follow-up that changes remembered calculation inputs ("use the same stop
# but 100 shares"). Narrower than _REUSE_MEMORY_HINT: "it" alone is not
# enough ("is it still above the stop at 212?" is a question, not a re-run).
_CALC_FOLLOWUP_HINT = re.compile(r"\b(same|previous|prior|instead|but|change|use|make it)\b", re.I)
_NUM = r"\$?\s*([0-9][0-9,]*(?:\.\d+)?)"
_SHARES_RE = re.compile(
    r"\b(?:buy|bought|sell|sold|short|long)\s+" + _NUM[4:] + r"\s+(?:shares?\s+(?:of\s+)?)?(?:\$?[A-Za-z]{1,6}\b)?"
    r"|\b([0-9][0-9,]*)\s+(?:shares?|sh)\b",
    re.I,
)
_ENTRY_RE = re.compile(r"\bentry(?:\s+price)?\s*(?:at|of|is|=|:)?\s*" + _NUM, re.I)
_AT_PRICE_RE = re.compile(r"\bat\s*" + _NUM, re.I)
_NOT_ENTRY_BEFORE_AT = re.compile(r"\b(?:stop|stop[- ]?loss|target|take[- ]profit|account|portfolio)\W*$", re.I)
_STOP_RE = re.compile(r"\bstop(?:[- ]?loss)?\s*(?:price\s*)?(?:at|of|is|=|:|to)?\s*" + _NUM, re.I)
_TARGET_RE = re.compile(r"\b(?:target|take[- ]profit)\s*(?:price\s*)?(?:at|of|is|=|:|to)?\s*" + _NUM, re.I)
_ACCOUNT_RE = re.compile(r"\b(?:account|portfolio)\s*(?:value|size|balance)?\s*(?:of|is|=|:)?\s*" + _NUM, re.I)


def _numbers_from_text(text: str) -> list[float]:
    return [float(raw.replace(",", "")) for raw in _CALC_NUMBER_RE.findall(text)]


def _labelled_number(pattern: re.Pattern, text: str) -> float | None:
    match = pattern.search(text)
    if not match:
        return None
    raw = next((group for group in match.groups() if group), None)
    return float(raw.replace(",", "")) if raw else None


def _entry_price(user_content: str) -> float | None:
    """"entry 220" or the first "at 220" that is not "stop at"/"target at"."""
    labelled = _labelled_number(_ENTRY_RE, user_content)
    if labelled is not None:
        return labelled
    for match in _AT_PRICE_RE.finditer(user_content):
        if not _NOT_ENTRY_BEFORE_AT.search(user_content[max(0, match.start() - 16):match.start()]):
            return float(match.group(1).replace(",", ""))
    return None


def _position_risk_fields(user_content: str) -> dict[str, float]:
    """Labelled position-risk inputs present in ``user_content``."""
    fields = {
        "shares": _labelled_number(_SHARES_RE, user_content),
        "entry_price": _entry_price(user_content),
        "stop_price": _labelled_number(_STOP_RE, user_content),
        "target_price": _labelled_number(_TARGET_RE, user_content),
        "account_value": _labelled_number(_ACCOUNT_RE, user_content),
    }
    return {key: value for key, value in fields.items() if value is not None and value > 0}


def _position_risk_calculation(user_content: str) -> CalculationRequest | None:
    fields = _position_risk_fields(user_content)
    if not {"shares", "entry_price", "stop_price"} <= fields.keys():
        return None
    try:
        return CalculationRequest(calculation="position_risk", **fields)
    except (TypeError, ValueError):
        return None


def _calculation_followup(user_content: str, prior: dict | None) -> CalculationRequest | None:
    """Re-run the previous calculation with explicitly changed inputs.

    "Use the same stop but 100 shares" keeps every remembered input and
    replaces only the labelled ones named in this message.
    """
    if not isinstance(prior, dict) or not _CALC_FOLLOWUP_HINT.search(user_content):
        return None
    overrides = _position_risk_fields(user_content)
    if not overrides:
        return None
    if prior.get("calculation") not in {"position_risk", "position_size", "risk_reward"}:
        return None
    try:
        return CalculationRequest(**{**prior, **overrides})
    except (TypeError, ValueError):
        return None


def _fallback_calculation(user_content: str) -> CalculationRequest | None:
    """Build a calculator request for simple, unambiguous user wording.

    This is intentionally conservative: it requires an operation keyword and
    the exact number of inputs needed, otherwise the model gets a chance to
    clarify the request normally.
    """
    position_risk = _position_risk_calculation(user_content)
    if position_risk is not None:
        return position_risk
    text = user_content.lower()
    numbers = _numbers_from_text(user_content)
    if len(numbers) == 2 and "allocation" in text and "portfolio" in text:
        return CalculationRequest(
            calculation="allocation",
            position_value=numbers[0],
            portfolio_value=numbers[1],
        )
    if len(numbers) == 2 and re.search(r"\b(from|change|return)\b", text):
        operation = "percentage_change" if "percent" in text or "%" in text or "return" in text else "dollar_change"
        return CalculationRequest(
            calculation=operation,
            old_value=numbers[0],
            new_value=numbers[1],
        )
    if len(numbers) == 3 and all(word in text for word in ("entry", "stop", "target")):
        return CalculationRequest(
            calculation="risk_reward",
            entry_price=numbers[0],
            stop_price=numbers[1],
            target_price=numbers[2],
        )
    if len(numbers) == 4 and "position" in text and "risk" in text:
        return CalculationRequest(
            calculation="position_size",
            entry_price=numbers[0],
            stop_price=numbers[1],
            account_value=numbers[2],
            risk_percent=numbers[3],
        )
    return None

# "How many watchlists do I have" / "what are my watchlists" / "what's
# on my watchlist" — the model is never told the trader's actual
# watchlists (no such data reaches build_chat_prompt), so it can only
# guess or, correctly per rule 11, decline. Confirmed live 2026-09-11
# it does the latter ("I don't have visibility..."), which is honest
# but unhelpful when the app has the real answer one query away.
# Answered deterministically, before the (slow) AI call, so accuracy
# never depends on the model and the trader isn't waiting on a round
# trip for something the DB already knows.
_WATCHLIST_LIST_INTENT = re.compile(
    r"how many watchlists?\b|"
    r"\b(which|what)\b.{0,20}\bwatchlists?\b|"
    r"\blist\b.{0,10}\bwatchlists?\b",
    re.I,
)

# "What tickers/symbols are in <name>" / "what's in the <name> watchlist" —
# same rationale as _WATCHLIST_LIST_INTENT, but for a *specific* named
# watchlist rather than "list them all". Confirmed live 2026-09-12: asked
# "what tickers are in Market Context" (a real watchlist name that also
# happens to collide with the unrelated market-context regime feature),
# the model had no watchlist data at all and fabricated a plausible-looking
# answer — the union of every real watchlist's symbols, deduped and
# alphabetized, presented as if it were that one list's real contents.
# Answered deterministically from the DB before the (slow, guessable) AI
# call, same as _WATCHLIST_LIST_INTENT. Falls through to the model (which
# should decline per system-prompt rule 11) when the captured name doesn't
# match any real watchlist, rather than claiming "no such watchlist" itself
# — a typo'd or partial name is still worth a human-facing clarification,
# not a flat DB-driven "not found".
_WATCHLIST_CONTENTS_INTENT = re.compile(
    r"\b(?:what|which)\b[^.!?\n]{0,25}\b(?:tickers?|symbols?|stocks?)\b[^.!?\n]{0,10}\b(?:in|on)\b\s+"
    r"(?:the\s+|my\s+)?(?P<name1>.*?)\s*(?:watch ?lists?|lists?)?[\?\.!]*$|"
    r"what'?s\s+(?:in|on)\s+(?:the\s+|my\s+)?(?P<name2>.*?)\s*(?:watch ?lists?|lists?)?[\?\.!]*$",
    re.I,
)

# "Analyze my X watchlist" / "how's the X watchlist doing" / etc. — any
# message naming a REAL watchlist by name, in a way that isn't already
# _WATCHLIST_CONTENTS_INTENT's "list its tickers" ask. Each alternative
# anchors WHERE the name starts (a quote, "my"/"the"/"a", or the start
# of the message) so the lazy capture can't run backward across the
# whole sentence ("how is my Swing Setups watchlist doing" must capture
# "Swing Setups", not "how is my Swing Setups"). The capture itself is
# still deliberately loose beyond that anchor (it'll happily match "my
# favorite watchlist" -> name "favorite") because it's never trusted
# directly — see ``_resolve_named_watchlist_symbols``, which only acts
# on it after a real, case-insensitive DB lookup succeeds. That DB check
# is the actual safety net, not this regex.
_NAMED_WATCHLIST_RE = re.compile(
    r'["“](?P<name1>[A-Za-z][A-Za-z0-9 &\'.\-]{0,40}?)["”]?\s+watch\s?lists?\b'
    r"|(?:\bmy\s+|\bthe\s+)(?P<name2>[A-Za-z][A-Za-z0-9 &\'.\-]{0,40}?)\s+watch\s?lists?\b"
    r'|\bwatch\s?lists?\s+(?:called|named)\s+["“]?(?P<name3>[A-Za-z][A-Za-z0-9 &\'.\-]{0,40}?)["”]?(?=[\s,;:\?\.!]|$)',
    re.I,
)
# Fallback for a message that leads with the watchlist name itself, no
# "my"/"the"/quote in front ("Swing Setups watchlist status?") — a
# separate, `.match`-only (start-of-string-only) pattern, not folded
# into the alternation above: combining a bare `^` alternative with the
# anchored ones there made `re.search` lock onto position 0 immediately
# (an anchor is a zero-width, always-successful match), capturing the
# ENTIRE prefix up to "watchlist" — e.g. "how is my Swing Setups
# watchlist" captured "how is my Swing Setups" instead of "Swing
# Setups" — since search tries alternatives in order at the first
# position they succeed, never backtracking to a later, better one.
_NAMED_WATCHLIST_LEADING_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9 &\'.\-]{0,40}?)\s+watch\s?lists?\b",
    re.I,
)


def _resolve_named_watchlist_symbols(db, user_content: str) -> list[str]:
    resolved = _resolve_named_watchlist(db, user_content)
    return resolved[1] if resolved else []


def _resolve_named_watchlist(db, user_content: str) -> tuple[str, list[str]] | None:
    """Deterministically resolve a specific, real watchlist the trader
    NAMED in this message (e.g. 'analyze "My Watch" watchlist') to its
    real member symbols, so the turn builds genuine <context> blocks for
    them instead of the model declining with "no visibility" (see
    CHAT_SYSTEM_PROMPT rule 10) — that rule is for when no real
    watchlist can be resolved this way, not this case.

    Returns ``(name, symbols)``, or ``None`` (a silent no-op) whenever the
    captured name doesn't match a real watchlist — never a guess.
    """
    m = _NAMED_WATCHLIST_RE.search(user_content)
    if m:
        name = m.group("name1") or m.group("name2") or m.group("name3") or ""
    else:
        m2 = _NAMED_WATCHLIST_LEADING_RE.match(user_content)
        name = m2.group("name") if m2 else ""
    name = name.strip(" \"'“”")
    if not name:
        return None
    from backend.repositories.watchlist_repository import WatchlistRepository

    repo = WatchlistRepository(db)
    wl = repo.get_watchlist_by_name(name)
    if wl is None:
        lowered = name.lower()
        wl = next(
            (w for w in repo.get_watchlists(active_only=True) if w.name.lower() == lowered),
            None,
        )
    if wl is None:
        return None
    return wl.name, [s.symbol for s in wl.symbols if s.is_enabled]


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


@dataclass
class _PlannerState:
    """Per-turn orchestration state; never persisted as user prose."""

    original_request: str
    executed_signatures: set[str]
    completed_steps: list[str]
    errors: list[str]
    max_tool_calls: int
    max_planning_calls: int
    planning_calls: int = 0


def _prepare_turn(
    repo: ChatRepository,
    session_id: int,
    user_content: str,
    chart_state: dict | None = None,
    regeneration_mode: str | None = None,
    preferences: dict | None = None,
    regeneration_scope: dict | None = None,
) -> _Turn:
    """Persist the user message and assemble the turn's quant context.

    Resolves the turn's tickers from the text (0..N, capped), attaches a
    cheap market-wide baseline when relevant, and fans the per-symbol
    context builds out over a thread pool.
    """
    session = repo.get_session(session_id)
    if session is None:
        raise ValueError(f"chat session {session_id} not found")

    if regeneration_mode == "refresh":
        _clear_context_cache()
        from backend.ai.market_baseline import invalidate_cache
        invalidate_cache()

    repo.add_message(session_id, "user", user_content)

    try:
        planner_state = json.loads(session.planner_state or "{}")
        if not isinstance(planner_state, dict):
            planner_state = {}
    except (TypeError, ValueError):
        planner_state = {}

    history = repo.get_messages(session_id, limit=_TRANSCRIPT_TURNS + 2)
    # Exclude the message we just added — it's passed separately as
    # `new_message`, not duplicated into the transcript. Long prior
    # replies are clipped so the transcript can't dominate.
    prior_assistant = next((m for m in reversed(history[:-1]) if m.role == "assistant"), None)
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
    transcript = [(m.role, _clip(m.content)) for m in history[:-1]][-_TRANSCRIPT_TURNS:]

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


def _regeneration_tool_scope(scope: dict | None) -> dict[str, str]:
    """Normalize a regeneration timeframe/session request for tool calls."""
    result: dict[str, str] = {}
    if not scope:
        return result
    if scope.get("timeframe"):
        try:
            result["timeframe"] = normalize_timeframe(str(scope["timeframe"]))
        except ValueError:
            pass
    if scope.get("session") and scope.get("session") != "auto":
        try:
            result["session"] = normalize_session(str(scope["session"]))
        except ValueError:
            pass
    return result


def _apply_regeneration_scope(action: str, arguments: dict, planner_state: dict | None) -> dict:
    """Give a scoped regeneration's timeframe/session to tools that take them.

    The trader explicitly asked for this scope, so it replaces whatever the
    route or model chose, but only for tools whose input has that field.
    """
    active = (planner_state or {}).get("active_regeneration") or {}
    scope = active.get("scope") or {}
    if not scope:
        return arguments
    try:
        fields = default_registry.get(action).input_model.model_fields
    except ValueError:
        return arguments
    scoped = dict(arguments)
    if scope.get("timeframe") and "timeframe" in fields:
        scoped["timeframe"] = scope["timeframe"]
    if scope.get("session") and "session" in fields:
        scoped["session"] = scope["session"]
    return scoped


def _apply_date_scope(action: str, arguments: dict, planner_state: dict | None) -> dict:
    """Map a date named in this message onto tools that take one.

    "What happened last Friday" bounds the event timeline to that day;
    "what changed since last Friday" compares against that day's close.
    "yesterday"/"today" keep what_changed's own named references.
    """
    active = (planner_state or {}).get("active_date_range")
    if not isinstance(active, dict):
        return arguments
    scoped = dict(arguments)
    if action in {"market_event_timeline", "get_signal_history"}:
        scoped.setdefault("start", active["start"])
        scoped.setdefault("end", active["end"])
    elif action == "what_changed" and active.get("phrase") not in {"today", "yesterday"}:
        if scoped.get("reference") in (None, "previous_close") and not scoped.get("since"):
            scoped["reference"] = "timestamp"
            scoped["since"] = active["end"]
    return scoped


# Browser-local data the trader opted in to sharing (Risk Dashboard
# positions, structured Journal fields, Scanner presets). Held only for the
# current turn: never written to planner state, the database, or the prompt
# as raw data. Tools that accept an explicit snapshot receive it.
_TURN_BROWSER_DATA: contextvars.ContextVar[dict | None] = contextvars.ContextVar("chat_browser_data", default=None)

_BROWSER_SNAPSHOT_ARGUMENTS = {
    "get_risk_dashboard": ("positions", "positions"),
    "assess_portfolio_risk": ("positions", "positions"),
    "scenario_analysis": ("positions", "positions"),
    "get_trade_journal": ("entries", "journal_entries"),
    "trade_journal_coach": ("entries", "journal_entries"),
    "get_saved_scans": ("presets", "scan_presets"),
}


def _apply_browser_data(action: str, arguments: dict) -> tuple[dict, dict[str, str]]:
    """Fill a tool's snapshot argument from opted-in browser data.

    Returns ``(arguments, trace_markers)``. A snapshot the caller already
    passed is never replaced. The markers stand in for the snapshot in the
    tool trace, so private rows are summarized there rather than copied.
    """
    mapping = _BROWSER_SNAPSHOT_ARGUMENTS.get(action)
    data = _TURN_BROWSER_DATA.get()
    if not mapping or not data:
        return arguments, {}
    argument, source = mapping
    rows = data.get(source)
    if arguments.get(argument) or not rows:
        return arguments, {}
    return {**arguments, argument: list(rows)}, {argument: f"<browser snapshot: {len(rows)} {source.replace('_', ' ')}>"}


_BROWSER_LOCAL_ACTIONS = {
    "get_risk_dashboard",
    "assess_portfolio_risk",
    "scenario_analysis",
    "get_trade_journal",
    "trade_journal_coach",
    "get_saved_scans",
}


def _browser_safe_reply_data(action: str, data: dict) -> dict:
    """Keep browser-local rows out of persisted assistant message text.

    The full snapshot may be used by the current tool call, but the Chat
    transcript is durable. Return only aggregate, non-row data there.
    """
    if action in {"get_risk_dashboard", "assess_portfolio_risk", "scenario_analysis"}:
        positions = data.get("positions")
        return {
            "available": data.get("available"),
            "position_count": len(positions) if isinstance(positions, list) else None,
            "reason": data.get("reason"),
            "gross_exposure": data.get("gross_exposure"),
            "net_exposure": data.get("net_exposure"),
            "stop_loss_risk": data.get("stop_loss_risk"),
            "price_basis": data.get("price_basis"),
            "unknown_count": len(data.get("unknowns") or []) if isinstance(data.get("unknowns"), list) else None,
        }
    if action in {"get_trade_journal", "trade_journal_coach"}:
        return {
            "available": data.get("available"),
            "total_entries": data.get("total_entries"),
            "closed_entries": data.get("closed_entries"),
            "priced_closed_entries": data.get("priced_closed_entries"),
            "win_rate_percent": data.get("win_rate_percent"),
            "expectancy_per_trade": data.get("expectancy_per_trade"),
            "average_r_multiple": data.get("average_r_multiple"),
            "setup_count": len(data.get("setup_performance") or []) if isinstance(data.get("setup_performance"), list) else None,
            "reason": data.get("reason"),
        }
    if action == "get_saved_scans":
        presets = data.get("presets")
        return {
            "available": data.get("available"),
            "preset_count": len(presets) if isinstance(presets, list) else None,
            "reason": "No browser-local saved Scanner preset is available." if data.get("available") is False else None,
        }
    return {}


def _format_browser_local_reply(action: str, data: dict, provider: str, *, query: str | None = None) -> str:
    """Turn aggregate browser-local tool data into user-facing prose.

    Browser-local rows are intentionally excluded from the durable transcript.
    This formatter keeps that privacy boundary while avoiding a raw JSON
    payload in the Chat panel.
    """
    source = provider or "MarketLens"
    if action in {"get_risk_dashboard", "assess_portfolio_risk", "scenario_analysis"}:
        if data.get("available") is False:
            reason = str(data.get("reason") or "No browser-local positions were shared for this turn.")
            if action == "scenario_analysis":
                return f"scenario_analysis (portfolio scenario) is unavailable from {source}: {reason}"
            if query == "portfolio_change":
                return f"Portfolio changes are unavailable from {source}: {reason}"
            if query == "portfolio_weakness":
                return f"Portfolio weakness ranking is unavailable from {source}: {reason}"
            return f"Portfolio risk is unavailable from {source}: {reason}"
        if query == "portfolio_change":
            return (
                "I can summarize the current shared portfolio, but I cannot verify what "
                "changed since yesterday because no prior portfolio snapshot is available."
            )
        if query == "portfolio_weakness":
            return (
                "I can summarize current shared portfolio risk, but I cannot rank those "
                "private holdings by scanner weakness until scanner data is joined to the "
                "shared position snapshot."
            )
        position_count = data.get("position_count")
        if not isinstance(position_count, int):
            positions = data.get("positions")
            position_count = len(positions) if isinstance(positions, list) else None
        count_text = f"{position_count} position{'s' if position_count != 1 else ''}" if isinstance(position_count, int) else "the shared positions"
        details = [f"{count_text}"]
        labels = (
            ("gross exposure", data.get("gross_exposure")),
            ("net exposure", data.get("net_exposure")),
            ("stop-loss risk", data.get("stop_loss_risk")),
        )
        for label, value in labels:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                details.append(f"{label} ${float(value):,.2f}")
        price_basis = data.get("price_basis")
        if price_basis:
            details.append(str(price_basis).rstrip("."))
        return f"Portfolio risk is verified from {source}: " + "; ".join(details) + "."

    if action in {"get_trade_journal", "trade_journal_coach"}:
        if data.get("available") is False:
            reason = str(data.get("reason") or "No browser-local journal entries were shared for this turn.")
            return f"Trade journal data is unavailable from {source}: {reason}"
        total = data.get("total_entries")
        closed = data.get("closed_entries")
        details = []
        if isinstance(total, int):
            details.append(f"{total} total entr{'y' if total == 1 else 'ies'}")
        if isinstance(closed, int):
            details.append(f"{closed} closed entr{'y' if closed == 1 else 'ies'}")
        win_rate = data.get("win_rate_percent")
        if isinstance(win_rate, (int, float)) and not isinstance(win_rate, bool):
            details.append(f"{float(win_rate):.1f}% win rate")
        expectancy = data.get("expectancy_per_trade")
        if isinstance(expectancy, (int, float)) and not isinstance(expectancy, bool):
            details.append(f"${float(expectancy):,.2f} expectancy per trade")
        if not details:
            details.append("no aggregate statistics available")
        return f"Trade journal review is verified from {source}: " + "; ".join(details) + "."

    if action == "get_saved_scans":
        if data.get("available") is False:
            reason = str(data.get("reason") or "No browser-local saved Scanner presets were shared for this turn.")
            return f"Saved scans are unavailable from {source}: {reason}"
        preset_count = data.get("preset_count")
        if not isinstance(preset_count, int):
            presets = data.get("presets")
            preset_count = len(presets) if isinstance(presets, list) else None
        if isinstance(preset_count, int):
            label = f"{preset_count} saved preset{'s' if preset_count != 1 else ''}"
        else:
            label = "saved Scanner presets"
        return f"Saved scans are verified from {source}: {label} available."

    return f"Verified {action} result from {source}."


def _format_indicator_reply(
    data: dict,
    *,
    provider: str,
    freshness_seconds: float | None,
    timeframe: str | None,
    arguments: dict,
    query: str | None = None,
) -> str:
    """Format an indicator result without exposing the source bar payload."""
    symbol = str(data.get("symbol") or arguments.get("symbol") or "the symbol").upper()
    indicator = str(data.get("indicator") or arguments.get("indicator") or "indicator").lower()
    labels = {
        "sma": "SMA",
        "ema": "EMA",
        "rsi": "RSI",
        "change_percent": "change",
    }
    if query in {"weekly_return", "monthly_return"} or (query and query.endswith("_bar_return")):
        labels["change_percent"] = {
            "weekly_return": "weekly return",
            "monthly_return": "monthly return",
        }.get(query, "period return")
    label = labels.get(indicator, indicator.replace("_", " ").title())
    period = data.get("period") or arguments.get("period")
    period_text = f" ({period})" if isinstance(period, int) else ""
    value = data.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return f"I couldn't format the verified {symbol} {label} result because its value was unavailable."
    if indicator == "rsi":
        value_text = f"{float(value):.1f}"
    elif indicator == "change_percent":
        value_text = f"{float(value):+.2f}%"
    else:
        value_text = f"${float(value):.2f}"

    source_timestamp = data.get("source_timestamp")
    if not source_timestamp:
        source_timestamp = data.get("timestamp")
    as_of = str(source_timestamp).split("T", 1)[0] if source_timestamp else None
    timeframe_label = timeframe or data.get("timeframe") or arguments.get("timeframe") or "specified timeframe"
    details = [f"{symbol} {label}{period_text}: {value_text}", f"{timeframe_label} bars"]
    if as_of:
        details.append(f"as of {as_of}")
    if provider:
        details.append(f"source {provider}")
    reply = "Verified " + ", ".join(details) + "."
    if isinstance(freshness_seconds, (int, float)) and freshness_seconds > 900:
        age = (
            f"{freshness_seconds / 3600:.1f} hours"
            if freshness_seconds >= 3600
            else f"{freshness_seconds / 60:.1f} minutes"
        )
        reply += f" Warning: this data is {age} old and historical; refresh market data before treating it as current."
    return reply


def _format_generic_market_reply(
    action: str,
    data: dict,
    *,
    provider: str,
    freshness_seconds: float | None,
    source_timestamp: str | None,
    timeframe: str | None,
    arguments: dict,
) -> str:
    """Render the remaining read-only tools without leaking raw JSON.

    Tool cards and traces retain the structured payload. The transcript gets
    only a bounded summary so a provider response can never become the Chat
    answer verbatim.
    """
    symbol = str(data.get("symbol") or arguments.get("symbol") or "").upper()
    labels = {
        "get_quote": "quote",
        "get_bars": "historical bars",
        "get_support_resistance": "support and resistance",
        "get_market_regime": "market regime",
        "get_news": "news",
        "get_fundamentals": "fundamentals",
        "get_options_snapshot": "options snapshot",
        "get_alerts": "alerts",
        "get_signal_history": "signal history",
        "get_sector_data": "sector data",
        "get_confluence": "multi-timeframe confluence",
        "get_relative_strength": "relative strength",
        "get_tape_state": "tape state",
        "get_session_stats": "session statistics",
        "get_calendar": "catalyst calendar",
        "why_did_it_move": "move evidence",
        "scenario_analysis": "scenario analysis",
        "historical_similarity": "historical similarity",
        "signal_explanation": "signal explanation",
        "counterargument_review": "counterargument review",
        "sensitivity_analysis": "sensitivity analysis",
        "market_event_timeline": "market event timeline",
        "anomaly_analysis": "anomaly analysis",
        "assumption_tracking": "assumption review",
        "build_trade_plan": "trade plan",
        "assess_portfolio_risk": "portfolio risk",
        "options_research": "options research",
        "trade_journal_coach": "trade journal coaching",
        "decision_checklist": "decision checklist",
        "export_report": "report",
    }
    label = labels.get(action, action.replace("_", " "))
    if data.get("available") is False:
        return f"{action} ({label}) is unavailable{f' for {symbol}' if symbol else ''}: {data.get('reason') or 'the required verified data was not available'}."

    details: list[str] = []
    if symbol:
        details.append(symbol)
    if action == "get_quote":
        if isinstance(data.get("price"), (int, float)):
            details.append(f"${float(data['price']):.2f}")
        if isinstance(data.get("change_percent"), (int, float)):
            details.append(f"{float(data['change_percent']):+.2f}%")
    elif action in {"get_bars", "get_signal_history", "market_event_timeline", "get_news", "get_calendar", "get_alerts"}:
        collection_key = {"get_bars": "bars", "get_signal_history": "signals", "market_event_timeline": "events", "get_news": "items", "get_calendar": "events", "get_alerts": "alerts"}.get(action, "")
        if collection_key and isinstance(data.get(collection_key), list):
            details.append(f"{len(data[collection_key])} {collection_key}")
    elif action == "get_support_resistance":
        for key in ("support", "resistance"):
            if isinstance(data.get(key), (int, float)):
                details.append(f"{key} ${float(data[key]):.2f}")
    elif action == "get_market_regime":
        if data.get("regime"):
            details.append(str(data["regime"]).replace("_", " "))
        if isinstance(data.get("confidence"), (int, float)):
            details.append(f"confidence {float(data['confidence']):.0%}")
    elif action == "get_relative_strength":
        signals = [item for item in data.get("signals", []) if isinstance(item, dict)]
        for item in signals[:3]:
            benchmark = item.get("benchmark") or "benchmark"
            score = item.get("rs_pct")
            if isinstance(score, (int, float)):
                details.append(f"vs {benchmark} {float(score):+.2f}%")
    elif action == "get_session_stats":
        for key in ("session", "date", "open", "high", "low", "close", "change_percent"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                details.append(f"{key.replace('_', ' ')} {float(value):+.2f}" if key == "change_percent" else f"{key} {float(value):.2f}")
            elif isinstance(value, str) and key in {"session", "date"}:
                details.append(f"{key} {value}")
    elif action in {"get_fundamentals", "get_options_snapshot", "options_research"}:
        for key in ("near_term_iv", "iv_rank", "pe_ratio", "eps", "revenue", "market_cap"):
            if isinstance(data.get(key), (int, float)):
                details.append(f"{key.replace('_', ' ')} {float(data[key]):.2f}")
        for key in ("chains", "expirations", "legs", "spreads"):
            if isinstance(data.get(key), list):
                details.append(f"{len(data[key])} {key}")
    elif action == "why_did_it_move":
        facts = data.get("facts") or []
        correlations = data.get("correlations") or []
        details.append(f"{len(facts)} verified price/volume facts")
        details.append(f"{len(correlations)} non-causal correlations")
        details.append("causation not established")
    elif action == "anomaly_analysis":
        anomalies = data.get("anomalies")
        if isinstance(anomalies, list):
            details.append(f"{len(anomalies)} detected anomalies")
    else:
        for key in ("status", "conclusion", "sample_size", "total_pnl_delta", "risk_reward", "verdict"):
            value = data.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                details.append(f"{key.replace('_', ' ')} {value}")
        if not details or (symbol and len(details) == 1):
            scalar_keys = [key for key, value in data.items() if isinstance(value, (str, int, float)) and not isinstance(value, bool) and key not in {"source_timestamp"}]
            if scalar_keys:
                details.append(f"{len(scalar_keys)} verified summary fields")

    timeframe_label = timeframe or data.get("timeframe")
    if timeframe_label:
        details.append(f"timeframe {timeframe_label}")
    if source_timestamp:
        details.append(f"as of {str(source_timestamp).split('T', 1)[0]}")
    if provider:
        details.append(f"source {provider}")
    reply = f"Verified {action} ({label}" + (f"; {'; '.join(details)}" if details else "") + ")."
    if isinstance(freshness_seconds, (int, float)) and freshness_seconds > 900:
        age = f"{freshness_seconds / 3600:.1f} hours" if freshness_seconds >= 3600 else f"{freshness_seconds / 60:.1f} minutes"
        reply += f" Warning: this data is {age} old; refresh market data before treating it as current."
    return reply


def _format_change_reply(
    data: dict,
    *,
    provider: str,
    source_timestamp: str | None,
    freshness_seconds: float | None,
    timeframe: str | None,
    arguments: dict,
) -> str:
    """Format a current-vs-baseline change without exposing nested bars."""
    symbol = str(data.get("symbol") or arguments.get("symbol") or "the symbol").upper()
    changes = data.get("changes")
    price_change = next(
        (item for item in changes or [] if isinstance(item, dict) and item.get("type") == "price"),
        None,
    )
    if not isinstance(price_change, dict) or not isinstance(price_change.get("percent"), (int, float)):
        unknowns = data.get("unknowns") or []
        reason = unknowns[0].get("reason") if unknowns and isinstance(unknowns[0], dict) else None
        return f"I couldn't verify {symbol}'s change: {reason or 'the comparison baseline was unavailable'}."
    percent = float(price_change["percent"])
    current = price_change.get("current")
    baseline = price_change.get("baseline")
    details = [f"{symbol} change: {percent:+.2f}%"]
    if isinstance(current, (int, float)) and isinstance(baseline, (int, float)):
        details.append(f"from ${float(baseline):.2f} to ${float(current):.2f}")
    reference = str(data.get("reference") or arguments.get("reference") or "previous_close")
    reference_label = {
        "previous_close": "the previous close",
        "yesterday": "yesterday's close",
        "last_visit": "the last visit",
        "timestamp": "the requested baseline",
    }.get(reference, reference.replace("_", " "))
    details.append(f"versus {reference_label}")
    timeframe_label = timeframe or data.get("timeframe") or arguments.get("timeframe") or "1d"
    details.append(f"on {timeframe_label}")
    if source_timestamp:
        details.append(f"as of {str(source_timestamp).split('T', 1)[0]}")
    if provider:
        details.append(f"source {provider}")
    reply = "Verified " + ", ".join(details) + "."
    if isinstance(freshness_seconds, (int, float)) and freshness_seconds > 900:
        age = (
            f"{freshness_seconds / 3600:.1f} hours"
            if freshness_seconds >= 3600
            else f"{freshness_seconds / 60:.1f} minutes"
        )
        reply += f" Warning: this data is {age} old; refresh market data before treating it as current."
    return reply


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


def _extract_memory_value(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    value = match.group(1).lower().replace(" ", "_")
    return "after_hours" if value in {"after-hour", "after-hours", "after_hours"} else value


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
        if trace:
            turn.planner_state["last_tool_result"] = {
                "tool": trace[-1].get("tool"),
                "ok": trace[-1].get("ok"),
                "provider": trace[-1].get("provider"),
                "source_timestamp": trace[-1].get("source_timestamp"),
            }
        _expire_carried_confirmation(turn)
        repo.set_planner_state(session_id, json.dumps(turn.planner_state, sort_keys=True))
        # _generate_reply delegates action execution to _run_turn_actions;
        # attach its transient trace to the returned ORM object for the API.
        grounded = grounded and not turn.unavailable  # deterministic fail-safe
        # `screened` is populated only by the run_screen tool — tickers the
        # turn's own message never named, so turn.focus (derived from the
        # user's text) wouldn't otherwise include them, and the frontend's
        # per-ticker quick-action buttons (add to watchlist / create alert)
        # key off `focus`/`partial` alone.
        focus = list(dict.fromkeys([*turn.focus, *screened]))
        assign_evidence_ids(trace)
        verification = verify_answer(
            reply_text,
            trace,
            allowed_symbols=[*turn.base, *focus, *turn.partial, *turn.unavailable, *_baseline_symbols(turn.market_baseline)],
            unavailable_symbols=turn.unavailable,
            user_content=user_content,
        )
        if verification.safe_content:
            reply_text = verification.safe_content
            grounded = False
        _append_turn_observability(trace, started_at, user_content)
        current_fingerprint = evidence_fingerprint(trace)
        material_change = _material_change(turn, current_fingerprint)
        regeneration = {"mode": turn.regeneration_mode, "reused_context": turn.reused_context} if turn.regeneration_mode else None
        if regeneration is not None and turn.regeneration_scope:
            regeneration["scope"] = turn.regeneration_scope
        if regeneration is not None and material_change:
            regeneration["material_change_detected"] = True
        blocks = build_response_blocks(
            content=reply_text,
            grounded=grounded,
            focus=focus,
            partial=turn.partial,
            unavailable=turn.unavailable,
            trace=trace,
            preferences=preferences,
            chart_state=turn.chart_state,
            regeneration=regeneration,
            material_change_detected=material_change,
            verification=verification.model_dump(),
        )
        assistant_message = repo.add_message(session_id, "assistant", reply_text, response_blocks=blocks)
        assistant_message.planner_trace = trace
        assistant_message.response_blocks_payload = blocks
        return assistant_message, grounded, focus, turn.partial, turn.unavailable
    finally:
        _TURN_BROWSER_DATA.reset(browser_token)
        repo.close()


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
        except Exception as e:  # noqa: BLE001 — mirror _generate_reply's contract
            logger.warning("Chat stream generation raised: %s", e)
            final_text, grounded = (
                "Something went wrong reaching the AI provider — please try again.",
                False,
            )

        # The user message is already persisted and actions may have run, so
        # a failure while finishing must still end in one persisted assistant
        # row; otherwise the client would retry and re-run the whole turn.
        try:
            if final_text is None:
                final_text, grounded = (
                    "AI is currently unavailable, so I can't answer that right now.",
                    False,
                )
            grounded = grounded and not turn.unavailable
            if trace:
                turn.planner_state["last_tool_result"] = {
                    "tool": trace[-1].get("tool"),
                    "ok": trace[-1].get("ok"),
                    "provider": trace[-1].get("provider"),
                    "source_timestamp": trace[-1].get("source_timestamp"),
                }
            _expire_carried_confirmation(turn)
            repo.set_planner_state(session_id, json.dumps(turn.planner_state, sort_keys=True))
            grounded = grounded and not turn.unavailable
            # See answer_chat_message's matching comment — `screened` (from
            # run_screen) is merged into `focus` so the frontend's quick-action
            # buttons pick up tickers the turn's own message never named.
            focus = list(dict.fromkeys([*turn.focus, *screened]))
            assign_evidence_ids(trace)
            verification = verify_answer(
                final_text,
                trace,
                allowed_symbols=[*turn.base, *focus, *turn.partial, *turn.unavailable, *_baseline_symbols(turn.market_baseline)],
                unavailable_symbols=turn.unavailable,
                user_content=user_content,
            )
            if verification.safe_content:
                final_text = verification.safe_content
                grounded = False
            _append_turn_observability(trace, started_at, user_content)
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
                preferences=preferences,
                chart_state=turn.chart_state,
                regeneration=regeneration,
                material_change_detected=material_change,
                verification=verification.model_dump(),
            )
            msg = repo.add_message(session_id, "assistant", final_text, response_blocks=blocks)
            msg.planner_trace = trace
            msg.response_blocks_payload = blocks
        except Exception as e:  # noqa: BLE001
            logger.warning("Chat stream finalization failed: %s", e)
            focus = list(dict.fromkeys(turn.focus))
            grounded = False
            msg = repo.add_message(
                session_id,
                "assistant",
                "I couldn't finish verifying that answer. Nothing was retried; please ask again if you still need it.",
            )
            msg.planner_trace = []
            msg.response_blocks_payload = []
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
            "no multi-timeframe trend or confidence"
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


# One extra attempt on a failed completion/parse before giving up (2026-
# 09-16) — this environment's local model has shown intermittent
# malformed-JSON replies where an immediate identical retry succeeds
# (observed live, not hypothetical), so trading a little latency for
# meaningfully fewer user-visible "I couldn't process that" replies is
# worth it. Total attempts = 1 + this.
_CHAT_PARSE_RETRIES = 1
CHAT_PARSE_MAX_ATTEMPTS = _CHAT_PARSE_RETRIES + 1


def _chat_route_model(role: str, fallback: str | None = None) -> str | None:
    """Resolve a role-specific model, falling back to the legacy override."""
    configured = getattr(ai_manager.settings, f"chat_{role}_model", "")
    if isinstance(configured, str) and configured.strip():
        return configured
    legacy = getattr(ai_manager.settings, "chat_model", "")
    if isinstance(legacy, str) and legacy.strip():
        return legacy
    return fallback


def _complete_and_parse(
    prompt: str,
    system: str,
    max_tokens: int,
    model: str | None,
    repair_model: str | None = None,
    *,
    trace: list[dict] | None = None,
    role: str = "synthesis",
):
    """One or more attempts at an AI completion + ``ChatReplyResponse``
    parse, retrying ``_CHAT_PARSE_RETRIES`` more time(s) on either a raw
    provider failure or a malformed reply before giving up.

    Returns ``(parsed, failure_reason)`` — ``parsed`` is ``None`` on
    total failure, with ``failure_reason`` ("ai_error" or "parse_error",
    reflecting the LAST attempt) for the caller to pick a fallback
    message. Never raises.
    """
    failure_reason = "ai_error"
    for attempt in range(_CHAT_PARSE_RETRIES + 1):
        call_started = time.perf_counter()
        requested_model = repair_model if attempt else model
        try:
            resp = run_sync(
                ai_manager.complete(
                    prompt=prompt,
                    system=system,
                    max_tokens=max_tokens,
                    model=requested_model,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Chat AI call raised (attempt %d): %s", attempt + 1, e)
            failure_reason = "ai_error"
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": role,
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "provider_exception",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, system),
                    "requested_model": requested_model,
                    "provider_request_count": 1,
                })
            continue
        provider = getattr(resp, "provider", None)
        response_model = getattr(resp, "model", None)
        attempted_providers = list(getattr(resp, "attempted_providers", None) or [])
        provider_request_count = len(attempted_providers) or 1
        if resp.text is None:
            logger.warning("Chat AI call returned no text (attempt %d)", attempt + 1)
            failure_reason = "ai_error"
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": role,
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "provider_no_text",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, system, resp.text),
                    "provider": provider,
                    "model": response_model,
                    "requested_model": requested_model,
                    "provider_request_count": provider_request_count,
                    "providers_tried": attempted_providers,
                })
            continue
        try:
            parsed = parse_chat_reply(resp.text)
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": role,
                    "attempt": attempt + 1,
                    "ok": True,
                    "status": "parsed",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, system, resp.text),
                    "provider": provider,
                    "model": response_model,
                    "requested_model": requested_model,
                    "provider_request_count": provider_request_count,
                    "providers_tried": attempted_providers,
                })
            return parsed, None
        except Exception as e:  # noqa: BLE001
            logger.info("Chat reply failed to parse (attempt %d): %s", attempt + 1, e)
            failure_reason = "parse_error"
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": role,
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "parse_error",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, system, resp.text),
                    "provider": provider,
                    "model": response_model,
                    "requested_model": requested_model,
                    "provider_request_count": provider_request_count,
                    "providers_tried": attempted_providers,
                })
    return None, failure_reason


def _build_deterministic_chat_reply(
    user_content: str,
    *,
    focus_symbols: list[str],
    planner_state: dict | None = None,
) -> ChatReplyResponse | str | None:
    """Build the shared typed plan used by blocking and streaming Chat.

    A string is an authoritative clarification; ``None`` means the bounded
    model planner is still the right fallback. Keeping this decision in one
    function prevents the two transport paths from acquiring different
    intent behavior.
    """
    # Resolve high-confidence market semantics before the broad calculation
    # hint. Words such as "return" and "change" are valid market metrics,
    # not requests for arithmetic inputs when a ticker and lookback are
    # present.
    semantic_route = route_semantic_intent(
        user_content,
        focus_symbols=focus_symbols,
        planner_state=planner_state,
    )
    if semantic_route is not None and semantic_route.action in {
        "get_indicator",
        "what_changed",
        "get_watchlist_intelligence",
        "get_market_context",
    }:
        return ChatReplyResponse(
            reply=f"Verified semantic route: {semantic_route.action}",
            grounded=True,
            action=semantic_route.action,
            action_query=semantic_route.action_query,
            action_tool_arguments=semantic_route.arguments,
        )
    if not _COMPARISON_INTENT.search(user_content) and not _ASSUMPTION_INTENT.search(user_content):
        # Applying this message's overrides is idempotent, so it is safe
        # whether or not _prepare_turn already stored them in memory.
        position = _position_risk_calculation(user_content) or _calculation_followup(
            user_content, (planner_state or {}).get("last_calculation_inputs")
        )
        if position is not None:
            return ChatReplyResponse(
                reply="Verified calculation",
                grounded=True,
                action="calculate",
                action_calculation=position,
            )
    if (
        _CALCULATION_HINT.search(user_content)
        and not _COMPARISON_INTENT.search(user_content)
        and not _ASSUMPTION_INTENT.search(user_content)
    ):
        calculation = _fallback_calculation(user_content)
        prior = (planner_state or {}).get("last_calculation_inputs")
        if calculation is None and prior and _REUSE_MEMORY_HINT.search(user_content):
            try:
                calculation = CalculationRequest(**prior)
            except (TypeError, ValueError):
                calculation = None
        if calculation is None:
            return (
                "What values should I use for that calculation? Please provide the "
                "relevant prices, position size, portfolio value, or risk inputs."
            )
        return ChatReplyResponse(
            reply="Verified calculation",
            grounded=True,
            action="calculate",
            action_calculation=calculation,
        )

    if _SAVED_SCANS_INTENT.search(user_content):
        return ChatReplyResponse(
            reply="Verified saved-scan lookup",
            grounded=True,
            action="get_saved_scans",
            action_tool_arguments={},
        )
    if _SIGNAL_HISTORY_INTENT.search(user_content):
        if len(focus_symbols) > 1:
            return "Which ticker's signal history do you want?"
        arguments: dict = {"symbol": focus_symbols[0]} if focus_symbols else {}
        timeframe = _extract_memory_value(user_content, r"\b(1m|2m|3m|5m|15m|30m|1h|4h|1d|1wk)\b")
        if timeframe:
            arguments["timeframe"] = timeframe
        return ChatReplyResponse(
            reply="Verified signal-history lookup",
            grounded=True,
            action="get_signal_history",
            action_symbol=focus_symbols[0] if focus_symbols else None,
            action_tool_arguments=arguments,
        )

    if semantic_route is not None:
        return ChatReplyResponse(
            reply=f"Verified semantic route: {semantic_route.action}",
            grounded=True,
            action=semantic_route.action,
            action_query=semantic_route.action_query,
            action_tool_arguments=semantic_route.arguments,
        )

    if _ASSUMPTION_INTENT.search(user_content):
        operation = "save" if _ASSUMPTION_SAVE_INTENT.search(user_content) else "review"
        symbol = focus_symbols[0] if len(focus_symbols) == 1 else None
        arguments = {"operation": operation, "symbol": symbol}
        if operation == "save":
            records = _parse_assumption_records(user_content, symbol)
            if not records:
                return (
                    "What assumption should I save? Include a thesis, growth rate, stop, "
                    "catalyst date, volatility, or invalidation condition."
                )
            arguments["assumptions"] = records
        return ChatReplyResponse(
            reply="Verified research-assumption review" if operation == "review" else "Save verified research assumption",
            grounded=True,
            action="assumption_tracking",
            action_tool_arguments=arguments,
            action_confirmed=operation == "save",
        )

    if _ANOMALY_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I check for anomalies?"
        return ChatReplyResponse(
            reply="Verified anomaly analysis",
            grounded=True,
            action="anomaly_analysis",
            action_tool_arguments={"symbol": focus_symbols[0]},
        )
    if _TIMELINE_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I build the event timeline for?"
        return ChatReplyResponse(
            reply="Verified market-event timeline",
            grounded=True,
            action="market_event_timeline",
            action_tool_arguments={"symbol": focus_symbols[0]},
        )
    if _OPTIONS_TOOL_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I use for the options lookup?"
        return ChatReplyResponse(
            reply="Verified options lookup",
            grounded=True,
            action="get_options_snapshot",
            action_symbol=focus_symbols[0],
            action_tool_arguments={"symbol": focus_symbols[0]},
        )
    if _WHY_MOVE_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I analyze for the move?"
        return ChatReplyResponse(
            reply="Verified move-evidence lookup",
            grounded=True,
            action="why_did_it_move",
            action_symbol=focus_symbols[0],
            action_tool_arguments={"symbol": focus_symbols[0]},
        )
    if _WHAT_CHANGED_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I compare for changes?"
        lowered = user_content.lower()
        reference = "last_visit" if "last visit" in lowered else "yesterday" if "yesterday" in lowered else "previous_close"
        return ChatReplyResponse(
            reply="Verified change comparison",
            grounded=True,
            action="what_changed",
            action_symbol=focus_symbols[0],
            action_tool_arguments={"symbol": focus_symbols[0], "reference": reference},
        )
    if _COMPARISON_INTENT.search(user_content) and (
        sum(bool(re.search(rf"\b{re.escape(symbol)}\b", user_content, re.I)) for symbol in focus_symbols) >= 2
        or "watchlist" in user_content.lower()
    ):
        lowered = user_content.lower()
        watchlist = (planner_state or {}).get("watchlist") if "watchlist" in lowered else None
        comparison_symbols = [
            symbol for symbol in focus_symbols if re.search(rf"\b{re.escape(symbol)}\b", user_content, re.I)
        ]
        metric = "return_percent"
        if "volatility" in lowered:
            metric = "volatility_percent"
        elif "volume" in lowered:
            metric = "volume"
        elif "price" in lowered:
            metric = "price"
        elif any(word in lowered for word in ("today", "daily", "change")):
            metric = "change_percent"
        direction = "asc" if any(word in lowered for word in ("weakest", "worst", "lowest", "smallest")) else "desc"
        return ChatReplyResponse(
            reply="Verified symbol comparison",
            grounded=True,
            action="compare_symbols",
            action_tool_arguments={
                "symbols": comparison_symbols,
                "watchlist": watchlist,
                "metric": metric,
                "direction": direction,
            },
        )
    if _COUNTERARGUMENT_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I review for counterarguments and invalidation?"
        return ChatReplyResponse(
            reply="Verified counterargument review",
            grounded=True,
            action="counterargument_review",
            action_tool_arguments={"symbol": focus_symbols[0]},
        )
    if _SIGNAL_EXPLANATION_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I explain the signal for?"
        return ChatReplyResponse(
            reply="Verified signal explanation",
            grounded=True,
            action="signal_explanation",
            action_tool_arguments={
                "symbol": focus_symbols[0],
                "include_historical": "historical" in user_content.lower(),
            },
        )
    if _SENSITIVITY_INTENT.search(user_content):
        return ChatReplyResponse(
            reply="Verified sensitivity analysis",
            grounded=True,
            action="sensitivity_analysis",
            action_tool_arguments={},
        )
    if _SIMILARITY_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I use for the historical similarity search?"
        return ChatReplyResponse(
            reply="Verified historical similarity search",
            grounded=True,
            action="historical_similarity",
            action_tool_arguments={
                "symbol": focus_symbols[0],
                "timeframe": (planner_state or {}).get("timeframe") or "1d",
            },
        )
    if _SCENARIO_INTENT.search(user_content):
        lowered = user_content.lower()
        numbers = _numbers_from_text(user_content)
        mentioned = [
            symbol for symbol in focus_symbols if re.search(rf"\b{re.escape(symbol)}\b", user_content, re.I)
        ]
        shock = numbers[0] if numbers and "%" in user_content else None
        if shock is not None and any(word in lowered for word in ("drop", "fall", "down", "selloff", "sell-off")):
            shock = -abs(shock)
        elif shock is not None and any(word in lowered for word in ("rise", "up", "gain", "increase")):
            shock = abs(shock)
        price_shocks = {mentioned[0]: shock} if len(mentioned) == 1 and shock is not None else {}
        portfolio_shock = shock if ("portfolio" in lowered or "selloff" in lowered or "sell-off" in lowered) else None
        stop_overrides = {}
        if "stop" in lowered and numbers and len(mentioned) == 1:
            stop_overrides[mentioned[0]] = numbers[0]
        return ChatReplyResponse(
            reply="Verified scenario analysis",
            grounded=True,
            action="scenario_analysis",
            action_tool_arguments={
                "price_shocks": price_shocks,
                "portfolio_shock_percent": portfolio_shock,
                "stop_price_overrides": stop_overrides,
            },
        )
    if _HISTORICAL_TOOL_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker and timeframe should I use for the historical data?"
        return ChatReplyResponse(
            reply="Verified historical data lookup",
            grounded=True,
            action="get_bars",
            action_symbol=focus_symbols[0],
            action_tool_arguments={
                "symbol": focus_symbols[0],
                "timeframe": (planner_state or {}).get("timeframe") or "1d",
            },
        )
    if _SCANNER_TOOL_INTENT.search(user_content):
        return ChatReplyResponse(
            reply="Verified scanner request",
            grounded=True,
            action="run_screen",
            action_query=user_content[:300],
        )
    if _RISK_TOOL_INTENT.search(user_content):
        return ChatReplyResponse(
            reply="Verified risk dashboard lookup",
            grounded=True,
            action="get_risk_dashboard",
            action_tool_arguments={},
        )
    if _JOURNAL_TOOL_INTENT.search(user_content):
        return ChatReplyResponse(
            reply="Verified trade journal lookup",
            grounded=True,
            action="get_trade_journal",
            action_tool_arguments={"symbol": focus_symbols[0]} if len(focus_symbols) == 1 else {},
        )
    if _ALERTS_TOOL_INTENT.search(user_content):
        return ChatReplyResponse(
            reply="Verified alerts lookup",
            grounded=True,
            action="get_alerts",
            action_tool_arguments={"symbol": focus_symbols[0]} if len(focus_symbols) == 1 else {},
        )
    return None


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
    """Call the AI and parse its reply. Never raises — degrades to a
    plain reply with grounded=False.

    Returns ``(text, grounded, screened)`` — ``screened`` is the tickers
    a run_screen tool call surfaced (empty for every other path), for the
    caller to fold into ``focus`` since they weren't named in the turn's
    own message.

    When the AI's reply asks for ``wants_reanalysis``, runs the chat's
    one tool (see module docstring) for the named ticker instead.
    """
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
    # ticker has no data (keeps the pre-universal wording).
    if (
        not symbol_blocks
        and unavailable
        and base_symbols
        and set(unavailable) == {s.upper() for s in base_symbols}
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
        remembered = (planner_state or {}).get("current_symbols", [])
        if _AMBIGUOUS_REFERENCE.search(user_content) and len(remembered) > 1:
            return f"Which ticker do you mean: {', '.join(remembered[:settings.ai.chat_max_tickers])}?", False, []

    focus_symbols = [b["symbol"] for b in symbol_blocks]
    deterministic = _build_deterministic_chat_reply(
        user_content,
        focus_symbols=focus_symbols,
        planner_state=planner_state,
    )
    if isinstance(deterministic, str):
        return deterministic, False, []
    if deterministic is not None:
        return _run_turn_actions(
            db,
            deterministic,
            symbol_blocks,
            unavailable,
            market_baseline,
            transcript,
            user_content,
            alert_context,
            trace=trace,
            started_at=time.monotonic(),
            planner_state=planner_state,
        )

    if not ai_manager.enabled:
        _trace_model_route(trace, "fallback", "deterministic")
        if trace is not None:
            trace.append({"kind": "server_reply", "trusted": True})
        return _deterministic_context_reply(symbol_blocks, unavailable, market_baseline)

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
    _trace_model_route(trace, "synthesis", synthesis_model)
    parsed, failure_reason = _complete_and_parse(
        prompt,
        CHAT_SYSTEM_PROMPT,
        500,
        synthesis_model,
        _chat_route_model("repair"),
        trace=trace,
        role="synthesis",
    )
    if parsed is None:
        if failure_reason == "ai_error":
            return "Something went wrong reaching the AI provider — please try again.", False, []
        return "I couldn't process that — could you rephrase?", False, []

    return _run_turn_actions(
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
    )


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


def _trace_model_route(trace: list[dict] | None, role: str, model: str | None) -> None:
    if trace is not None:
        trace.append({"kind": "model", "role": role, "model": model or "deterministic"})


def _watchlist_list_reply(db) -> str:
    """Real answer to "how many/which watchlists do I have" — straight
    from the DB, not the model's guess. See ``_WATCHLIST_LIST_INTENT``.
    """
    from backend.repositories.watchlist_repository import WatchlistRepository

    watchlists = WatchlistRepository(db).get_watchlists(active_only=True)
    if not watchlists:
        return "You don't have any watchlists yet."

    def _describe(wl) -> str:
        symbols = [s.symbol for s in wl.symbols if s.is_enabled]
        names = ", ".join(symbols) if symbols else "no symbols"
        return f'"{wl.name}" ({len(symbols)}: {names})'

    if len(watchlists) == 1:
        return f"You have 1 watchlist: {_describe(watchlists[0])}."
    return (
        f"You have {len(watchlists)} watchlists: "
        + "; ".join(_describe(wl) for wl in watchlists)
        + "."
    )


def _watchlist_contents_reply(db, name: str) -> str | None:
    """Real answer to "what tickers are in <name>" for one *specific*
    named watchlist — straight from the DB. See
    ``_WATCHLIST_CONTENTS_INTENT``. Returns ``None`` (falls through to
    the model) when ``name`` doesn't match any real watchlist, exact or
    case-insensitive.
    """
    from backend.repositories.watchlist_repository import WatchlistRepository

    name = name.strip(" \"'")
    if not name:
        return None
    repo = WatchlistRepository(db)
    wl = repo.get_watchlist_by_name(name)
    if wl is None:
        lowered = name.lower()
        wl = next(
            (w for w in repo.get_watchlists(active_only=True) if w.name.lower() == lowered),
            None,
        )
    if wl is None:
        return None
    symbols = [s.symbol for s in wl.symbols if s.is_enabled]
    names = ", ".join(symbols) if symbols else "no symbols"
    return f'"{wl.name}" has {len(symbols)}: {names}.'


def _fallback_action(user_content: str, symbol_blocks: list[dict]) -> str | None:
    """A deterministic ``action`` to use when the model left ``"none"``
    despite an unambiguous destructive request — see
    ``_DELETE_WATCHLIST_FALLBACK``. Returns ``None`` when nothing should
    override the model's own ``"none"``.
    """
    if symbol_blocks:
        return None  # a resolved ticker means this is more likely remove_from_watchlist
    if _DELETE_WATCHLIST_FALLBACK.search(user_content):
        return "delete_watchlist"
    return None


def _fallback_confirmation(
    user_content: str,
    transcript: list[tuple[str, str]],
) -> tuple[str, str | None, str | None] | None:
    """A deterministic ``(action, action_symbol, action_watchlist)`` to
    use when the trader just said yes to the app's OWN previous
    confirmation question but the model left ``action="none"`` on this
    turn — see ``_CONFIRM_DELETE_WATCHLIST_RE`` /
    ``_CONFIRM_REMOVE_FROM_WATCHLIST_RE``. Returns ``None`` when the
    prior turn wasn't a recognizable confirmation question, this turn
    isn't an affirmative, or the question's target can't be parsed back
    out with confidence (e.g. a placeholder like "that ticker").
    """
    if not transcript or transcript[-1][0] != "assistant":
        return None
    if not _AFFIRM_INTENT.search(user_content):
        return None
    prior = transcript[-1][1]
    m = _CONFIRM_DELETE_WATCHLIST_RE.match(prior)
    if m:
        return "delete_watchlist", None, m.group("name")
    m = _CONFIRM_REMOVE_FROM_WATCHLIST_RE.match(prior)
    if m:
        return "remove_from_watchlist", m.group("sym"), m.group("wl")
    return None


def _finalize_parsed(
    db,
    parsed,
    symbol_blocks: list[dict],
    user_content: str = "",
    transcript: list[tuple[str, str]] | None = None,
    trace: list[dict] | None = None,
    planner_state: dict | None = None,
) -> tuple[str, bool, list[str]]:
    """A parsed ``ChatReplyResponse`` -> ``(final_text, grounded, screened)``.

    ``screened`` is the tickers a run_screen tool call surfaced (empty
    for every other path) — see ``_generate_reply``'s docstring.

    Runs the chat's tools: ``wants_reanalysis`` (a real ``analyze_symbol``
    run), or one of the ``action`` values (alert / watchlist CRUD, a
    backtest, a screen — see the module docstring). A destructive action
    without ``action_confirmed`` never reaches ``_run_action`` — it gets
    a server-authored confirmation question instead, regardless of what
    the model set for "reply". When the model leaves ``action="none"``,
    ``_fallback_action`` / ``_fallback_confirmation`` get one more
    chance to catch an unambiguous destructive request (or its
    confirmation) before it falls through to the model's own (possibly
    hallucinated) prose.
    """
    if parsed.wants_reanalysis:
        known = [b["symbol"] for b in symbol_blocks]
        target = (parsed.reanalysis_symbol or "").upper().strip()
        if not target and len(known) == 1:
            target = known[0]
        if target and target in known:
            reanalysis_started = time.perf_counter()
            text, grounded, reanalysis_evidence = _run_reanalysis(target)
            if grounded and trace is not None:
                trace.append({
                    "tool": "analyze_symbol",
                    "ok": True,
                    "provider": "MarketLens analysis",
                    "freshness_seconds": 0.0,
                    "source_timestamp": now_ny().isoformat(),
                    "duration_ms": round((time.perf_counter() - reanalysis_started) * 1000, 3),
                    "arguments": sanitize_arguments({"symbol": target}),
                    "provider_request_count": 1,
                    "evidence_values": reanalysis_evidence,
                })
            return text, grounded, []
        if known:
            return "Which ticker should I run the full analysis for?", True, []
        return "Tell me which ticker you'd like me to run the full analysis for.", False, []
    if parsed.action == "none":
        calculation = _fallback_calculation(user_content)
        if calculation is not None:
            parsed.action = "calculate"
            parsed.action_calculation = calculation
        elif _CALCULATION_HINT.search(user_content):
            prior = (planner_state or {}).get("last_calculation_inputs")
            if prior and _REUSE_MEMORY_HINT.search(user_content):
                try:
                    parsed.action = "calculate"
                    parsed.action_calculation = CalculationRequest(**prior)
                except (TypeError, ValueError):
                    prior = None
            if parsed.action == "none":
                return (
                    "What values should I use for that calculation? Please provide the "
                    "relevant prices, position size, portfolio value, or risk inputs.",
                    False,
                    [],
                )
        else:
            fallback = _fallback_action(user_content, symbol_blocks)
            if fallback is not None:
                parsed.action = fallback
            else:
                confirmed = _fallback_confirmation(user_content, transcript or [])
                pending = (planner_state or {}).get("pending_confirmation")
                if confirmed is None and pending and _AFFIRM_INTENT.match(user_content):
                    parsed.action = pending.get("action", "none")
                    parsed.action_symbol = pending.get("symbol")
                    parsed.action_watchlist = pending.get("watchlist")
                    parsed.action_target_id = pending.get("target_id")
                    parsed.action_tool_arguments = pending.get("tool_arguments")
                    parsed.action_confirmed = True
                elif confirmed is not None:
                    parsed.action, parsed.action_symbol, parsed.action_watchlist = confirmed
                    parsed.action_confirmed = True
    if parsed.action != "none":
        if parsed.action in _DESTRUCTIVE_ACTIONS:
            # A model-produced ``action_confirmed`` is not sufficient for a
            # live Chat turn.  Confirmation must follow a server-authored
            # prompt that is represented in planner state.  Direct callers
            # without planner state retain the existing explicit-confirmed
            # contract used by the lower-level action tests.
            pending = (planner_state or {}).get("pending_confirmation")
            server_confirmed = planner_state is None and parsed.action_confirmed
            if planner_state is not None:
                server_confirmed = bool(
                    pending
                    and pending.get("action") == parsed.action
                    and _AFFIRM_INTENT.match(user_content)
                )
                if server_confirmed and pending is not None:
                    # Replay exactly what the trader approved, not a new
                    # model-supplied payload from the confirmation turn.
                    parsed.action_symbol = pending.get("symbol")
                    parsed.action_watchlist = pending.get("watchlist")
                    parsed.action_target_id = pending.get("target_id")
                    parsed.action_tool_arguments = pending.get("tool_arguments")
                    parsed.action_confirmed = True
            if not server_confirmed:
                parsed.action_confirmed = False
                if planner_state is not None:
                    tool_arguments = dict(parsed.action_tool_arguments or {})
                    # Existing browser-local entries are not needed to append
                    # one entry and should never be persisted in planner
                    # state.  Keep only the typed entry awaiting approval.
                    if parsed.action == "save_to_journal":
                        tool_arguments = {"entry": tool_arguments.get("entry")}
                    planner_state["pending_confirmation"] = {
                        "action": parsed.action,
                        "symbol": parsed.action_symbol,
                        "watchlist": parsed.action_watchlist,
                        "target_id": parsed.action_target_id,
                        "tool_arguments": tool_arguments,
                    }
                return _confirm_prompt(db, parsed), True, []
        if planner_state is not None and parsed.action_confirmed:
            planner_state["pending_confirmation"] = None
        return _run_action(db, parsed, planner_state=planner_state, trace=trace)
    return parsed.reply, parsed.grounded, []


def _action_was_executed(parsed) -> bool:
    """True when ``_finalize_parsed`` actually ran a tool for ``parsed``
    (not a plain reply, not a reanalysis, not a confirmation question
    still waiting on the trader) — the signal ``_run_turn_actions`` uses
    to decide whether chaining even applies. ``parsed`` reflects any
    ``_fallback_action`` / ``_fallback_confirmation`` mutation
    ``_finalize_parsed`` already made, since both operate in place.
    """
    if parsed.wants_reanalysis or parsed.action == "none":
        return False
    return not (parsed.action in _DESTRUCTIVE_ACTIONS and not parsed.action_confirmed)


def _run_turn_actions(
    db,
    parsed,
    symbol_blocks: list[dict],
    unavailable: list[str],
    market_baseline: dict | None,
    transcript: list[tuple[str, str]],
    user_content: str,
    alert_context: dict | None,
    trace: list[dict] | None = None,
    started_at: float | None = None,
    planner_state: dict | None = None,
    preferences: dict | None = None,
) -> tuple[str, bool, list[str]]:
    """Runs ``parsed``'s tool via ``_finalize_parsed``, then — only when
    the trader's own message hints at more than one request (see
    ``_MULTI_STEP_HINT``) and that first step was a real, already-
    executed action — asks the model whether anything from the ORIGINAL
    message is still undone, running each additional step the same way,
    until the turn's tool-call, planning-call, token, or time budget is
    spent (each reported as a stopped planning step). See the module docstring
    (2026-09-16) for why this chains single-action completion calls
    instead of widening the JSON schema.

    The combined reply is built ONLY from each executed step's own
    deterministic result text — never a continuation call's free-form
    "reply" — per rule 11 (only the app's own action result may claim
    something was done). A step that doesn't execute (model says
    "none", or a destructive step still needs its own confirmation)
    stops the chain there; its confirmation question (if any) is the
    last thing appended.
    """
    text, grounded, screened = _finalize_parsed(
        db,
        parsed,
        symbol_blocks,
        user_content,
        transcript,
        trace=trace,
        planner_state=planner_state,
    )
    if trace is not None and parsed.action != "none":
        executed = _action_was_executed(parsed)
        trace.append(
            {
                "kind": "step",
                "step": 1,
                "status": (
                    "completed"
                    if executed and grounded
                    else "needs_confirmation"
                    if parsed.action in _DESTRUCTIVE_ACTIONS and not executed
                    else "failed"
                ),
                "tool": parsed.action,
                "depends_on": [],
                "detail": _action_step_detail(parsed),
                "arguments": _action_trace_arguments(parsed),
            }
        )
    # A single assumption-tracking payload may contain several fields joined
    # by "and"; it is already one complete action, not a multi-step request.
    if (
        parsed.action in {"assumption_tracking", "compare_symbols"}
        or not _action_was_executed(parsed)
        or not _MULTI_STEP_HINT.search(user_content)
    ):
        return text, grounded, screened

    texts = [f"Step 1: {text}"]
    all_grounded = grounded
    all_screened = list(screened)
    planner = _PlannerState(
        original_request=user_content,
        executed_signatures={_action_signature(parsed)},
        completed_steps=[text],
        errors=[],
        max_tool_calls=_tool_call_limit(),
        max_planning_calls=_planning_call_limit(),
    )
    result_cache: dict[str, tuple[str, bool, list[str]]] = {}
    first_signature = _action_signature(parsed)
    if _cacheable_chat_action(parsed.action):
        result_cache[first_signature] = (text, grounded, list(screened))
    turn_started = time.monotonic() if started_at is None else started_at
    turn_budget = _turn_budget_seconds()
    token_budget = _turn_token_budget()
    # Token use is read from model-call records; keep a private record when
    # the caller did not ask for a trace so the budget still applies.
    budget_trace = trace if trace is not None else []

    def stop_for_budget(reason: str, message: str) -> None:
        nonlocal all_grounded
        texts.append(message)
        all_grounded = False
        planner.errors.append(reason)
        if trace is not None:
            trace.append(
                {
                    "kind": "step",
                    "step": len(texts),
                    "status": "stopped",
                    "tool": "planning",
                    "depends_on": [len(texts) - 1],
                    "reason": reason,
                }
            )

    while True:
        executed_steps = len(planner.completed_steps)
        if executed_steps >= planner.max_tool_calls:
            stop_for_budget(
                "tool_budget_exhausted",
                f"That was this turn's limit of {executed_steps} actions; if anything in your request is still undone, ask again.",
            )
            break
        if planner.planning_calls >= planner.max_planning_calls:
            if planner.max_planning_calls:
                stop_for_budget(
                    "planning_budget_exhausted",
                    "That used this turn's planning budget; if anything in your request is still undone, ask again.",
                )
            break
        if time.monotonic() - turn_started >= turn_budget:
            stop_for_budget(
                "time_budget_exhausted",
                "I stopped the remaining step because this turn reached its time budget.",
            )
            break
        continuation = (
            "Original request: " + user_content + "\nAlready executed: " + " ".join(texts)
        )
        prompt = build_chat_prompt(
            symbol_blocks,
            unavailable,
            market_baseline,
            transcript,
            continuation,
            alert_context,
            token_budget=_prompt_token_budget(CHAT_CONTINUATION_SYSTEM_PROMPT, _CONTINUATION_MAX_TOKENS),
            chart_state=(planner_state or {}).get("chart_state"),
            preferences=preferences,
            regeneration=(planner_state or {}).get("active_regeneration"),
        )
        next_cost = _estimate_tokens(prompt, CHAT_CONTINUATION_SYSTEM_PROMPT) + _CONTINUATION_MAX_TOKENS
        if _turn_tokens_used(budget_trace) + next_cost > token_budget:
            stop_for_budget(
                "token_budget_exhausted",
                "I stopped the remaining step because this turn reached its token budget.",
            )
            break
        planner.planning_calls += 1
        next_parsed, failure_reason = _complete_and_parse(
            prompt,
            CHAT_CONTINUATION_SYSTEM_PROMPT,
            _CONTINUATION_MAX_TOKENS,
            _chat_route_model("planning"),
            _chat_route_model("repair"),
            trace=budget_trace,
            role="planning",
        )
        if next_parsed is None:
            logger.info("chat multi-step continuation failed: %s", failure_reason)
            texts.append("I completed the available step, but could not safely plan the next step.")
            all_grounded = False
            if trace is not None:
                trace.append(
                    {
                        "kind": "step",
                        "step": len(texts),
                        "status": "failed",
                        "tool": "planning",
                        "depends_on": [len(texts) - 1],
                        "reason": failure_reason,
                    }
                )
            break

        if next_parsed.wants_reanalysis or next_parsed.action == "none":
            break

        signature = _action_signature(next_parsed)
        if signature in planner.executed_signatures:
            cached = result_cache.get(signature)
            if cached is not None:
                cached_text, cached_grounded, cached_screened = cached
                texts.append(
                    f"Step {len(texts) + 1} (reused): {cached_text} "
                    "I stopped the remaining step after reusing this result."
                )
                all_grounded = all_grounded and cached_grounded
                all_screened.extend(cached_screened)
                if trace is not None:
                    trace.append(
                        {
                            "tool": next_parsed.action,
                            "ok": cached_grounded,
                            "provider": "turn-cache",
                            "reused": True,
                            "cache_hit": True,
                            "duration_ms": 0.0,
                            "provider_request_count": 0,
                            "arguments": _action_trace_arguments(next_parsed),
                        }
                    )
            else:
                texts.append("I stopped the remaining step because it repeated an action already executed in this turn.")
            all_grounded = False
            planner.errors.append("reused_action" if cached is not None else "duplicate_action")
            if trace is not None:
                trace.append(
                    {
                        "kind": "step",
                        "step": len(texts),
                        "status": "reused" if cached is not None else "stopped",
                        "tool": next_parsed.action,
                        "depends_on": [len(texts) - 1],
                        "reason": "repeated_action",
                        "arguments": _action_trace_arguments(next_parsed),
                    }
                )
            break
        planner.executed_signatures.add(signature)

        step_text, step_grounded, step_screened = _finalize_parsed(
            db,
            next_parsed,
            symbol_blocks,
            user_content,
            transcript,
            trace=trace,
            planner_state=planner_state,
        )
        texts.append(f"Step {len(texts) + 1}: {step_text}")
        if trace is not None:
            trace.append(
                {
                    "kind": "step",
                    "step": len(texts),
                    "status": "completed" if step_grounded else "failed",
                    "tool": next_parsed.action,
                    "depends_on": [len(texts) - 1],
                    "detail": _action_step_detail(next_parsed),
                    "arguments": _action_trace_arguments(next_parsed),
                }
            )
        planner.completed_steps.append(step_text)
        all_grounded = all_grounded and step_grounded
        all_screened.extend(step_screened)
        if _cacheable_chat_action(next_parsed.action):
            result_cache[signature] = (step_text, step_grounded, list(step_screened))
        if not _action_was_executed(next_parsed):
            break  # a pending confirmation — stop the chain here

    return " ".join(texts), all_grounded, list(dict.fromkeys(all_screened))


def _generate_reply_streaming(
    db, turn: _Turn, trace: list[dict] | None = None
) -> Iterator[tuple]:
    """Streaming variant of :func:`_generate_reply`.

    Yields ``("delta", text)`` for each incremental piece of the reply,
    then exactly one ``("result", (final_text, grounded, screened))``.
    Never raises — every failure mode ends in a ``("result", ...)``.

    The streamed deltas are the model's ``reply`` field decoded live from
    the partial JSON. The trailing ``result`` is authoritative: on the
    reanalysis-tool path it differs from what was streamed, and the
    caller overwrites the bubble with it.
    """
    if _AMBIGUOUS_RANKING_REFERENCE.search(turn.user_content):
        if trace is not None:
            trace.append({"kind": "server_reply", "trusted": True})
        yield (
            "result",
            (
                "Do you mean the weakest name from your last watchlist result, "
                "or the weakest name across the market? Please specify a watchlist "
                "or say market-wide.",
                False,
                [],
            ),
        )
        return
    if (
        not turn.symbol_blocks
        and turn.unavailable
        and turn.base
        and set(turn.unavailable) == {s.upper() for s in turn.base}
    ):
        yield (
            "result",
            (
                f"I don't have enough data on {turn.unavailable[0]} yet to answer that.",
                False,
                [],
            ),
        )
        return

    if not turn.symbol_blocks and turn.unavailable and _COMPARISON_INTENT.search(turn.user_content):
        names = ", ".join(turn.unavailable)
        yield (
            "result",
            (
                f"I don't have enough verified data for {names} to compare them.",
                False,
                [],
            ),
        )
        return

    if not turn.symbol_blocks:
        m = _WATCHLIST_CONTENTS_INTENT.search(turn.user_content)
        if m:
            reply = _watchlist_contents_reply(db, m.group("name1") or m.group("name2") or "")
            if reply:
                if trace is not None:
                    trace.append({"kind": "server_reply", "trusted": True})
                yield ("result", (reply, True, []))
                return
        if _WATCHLIST_LIST_INTENT.search(turn.user_content):
            if trace is not None:
                trace.append({"kind": "server_reply", "trusted": True})
            yield ("result", (_watchlist_list_reply(db), True, []))
            return
        if turn.unavailable and turn.planner_state.get("rejected_symbols"):
            names = ", ".join(turn.unavailable)
            if trace is not None:
                trace.append({"kind": "server_reply", "trusted": True})
            yield (
                "result",
                (f"I couldn't find current verified market data for {names}. Please check the ticker and try again.", False, []),
            )
            return
        remembered = turn.planner_state.get("current_symbols", [])
        if _AMBIGUOUS_REFERENCE.search(turn.user_content) and len(remembered) > 1:
            yield (
                "result",
                (f"Which ticker do you mean: {', '.join(remembered[:settings.ai.chat_max_tickers])}?", False, []),
            )
            return

    deterministic = _build_deterministic_chat_reply(
        turn.user_content,
        focus_symbols=[b["symbol"] for b in turn.symbol_blocks],
        planner_state=turn.planner_state,
    )
    if isinstance(deterministic, str):
        yield ("result", (deterministic, False, []))
        return
    if deterministic is not None:
        yield (
            "result",
            _run_turn_actions(
                db,
                deterministic,
                turn.symbol_blocks,
                turn.unavailable,
                turn.market_baseline,
                turn.transcript,
                turn.user_content,
                turn.alert_context,
                trace=trace,
                planner_state=turn.planner_state,
                preferences=turn.preferences,
            ),
        )
        return

    if not ai_manager.enabled:
        _trace_model_route(trace, "fallback", "deterministic")
        if trace is not None:
            trace.append({"kind": "server_reply", "trusted": True})
        yield ("result", _deterministic_context_reply(turn.symbol_blocks, turn.unavailable, turn.market_baseline))
        return

    budget = _prompt_token_budget(CHAT_SYSTEM_PROMPT, 500)
    prompt = build_chat_prompt(
        turn.symbol_blocks,
        turn.unavailable,
        turn.market_baseline,
        turn.transcript,
        turn.user_content,
        turn.alert_context,
        capped_note=_capped_note(turn.capped, turn.symbol_blocks),
        token_budget=budget,
        chart_state=turn.chart_state,
        preferences=turn.preferences,
        regeneration=turn.planner_state.get("active_regeneration"),
    )

    chat_model = _chat_route_model("synthesis", _chat_route_model("planning"))
    repair_model = _chat_route_model("repair")
    _trace_model_route(trace, "synthesis", chat_model)
    parsed = None
    failure_message = "I couldn't process that — could you rephrase?"
    # Same retry rationale as _complete_and_parse — a malformed/empty
    # reply gets one more full attempt before giving up. A retry's
    # deltas stream to the client same as the first attempt's; the
    # trailing ("result", ...) below is always authoritative and
    # overwrites whatever partial text was shown, same as the existing
    # reanalysis-tool path already does.
    for attempt in range(_CHAT_PARSE_RETRIES + 1):
        call_started = time.perf_counter()
        raw = ""
        extractor = ReplyExtractor()
        attribution = StreamAttribution()
        requested_model = repair_model if attempt else chat_model
        try:
            if ai_manager.settings.chat_streaming:
                for chunk in stream_sync(
                    ai_manager.stream(
                        prompt,
                        system=CHAT_SYSTEM_PROMPT,
                        max_tokens=500,
                        model=requested_model,
                        attribution=attribution,
                    )
                ):
                    raw += chunk
                    delta = extractor.feed(raw)
                    if delta:
                        yield ("delta", delta)
            else:
                resp = run_sync(
                    ai_manager.complete(
                        prompt,
                        system=CHAT_SYSTEM_PROMPT,
                        max_tokens=500,
                        model=requested_model,
                    )
                )
                raw = resp.text or ""
                attribution.provider = getattr(resp, "provider", None)
                attribution.model = getattr(resp, "model", None)
                attribution.attempted_providers = list(getattr(resp, "attempted_providers", None) or [])
                delta = extractor.feed(raw)
                if delta:
                    yield ("delta", delta)
        except Exception as e:  # noqa: BLE001
            logger.warning("Chat streaming AI call raised (attempt %d): %s", attempt + 1, e)
            failure_message = "Something went wrong reaching the AI provider — please try again."
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": "synthesis",
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "provider_exception",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, CHAT_SYSTEM_PROMPT),
                    "requested_model": requested_model,
                    "provider_request_count": 1,
                })
            continue

        provider_request_count = len(attribution.attempted_providers or []) or 1
        if not raw.strip():
            failure_message = "AI is currently unavailable, so I can't answer that right now."
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": "synthesis",
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "provider_no_text",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, CHAT_SYSTEM_PROMPT, raw),
                    "provider": attribution.provider,
                    "model": attribution.model,
                    "requested_model": requested_model,
                    "provider_request_count": provider_request_count,
                    "providers_tried": list(attribution.attempted_providers or []),
                })
            continue

        try:
            parsed = parse_chat_reply(raw)
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": "synthesis",
                    "attempt": attempt + 1,
                    "ok": True,
                    "status": "parsed",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, CHAT_SYSTEM_PROMPT, raw),
                    "provider": attribution.provider,
                    "model": attribution.model,
                    "requested_model": requested_model,
                    "provider_request_count": provider_request_count,
                    "providers_tried": list(attribution.attempted_providers or []),
                })
            break
        except Exception as e:  # noqa: BLE001
            logger.info("Chat reply failed to parse (attempt %d): %s", attempt + 1, e)
            failure_message = extractor.text.strip() or failure_message
            if trace is not None:
                trace.append({
                    "kind": "model_call",
                    "role": "synthesis",
                    "attempt": attempt + 1,
                    "ok": False,
                    "failure_kind": "parse_error",
                    "duration_ms": round((time.perf_counter() - call_started) * 1000, 3),
                    "prompt_chars": len(prompt),
                    "estimated_tokens": _estimate_tokens(prompt, CHAT_SYSTEM_PROMPT, raw),
                    "provider": attribution.provider,
                    "model": attribution.model,
                    "requested_model": requested_model,
                    "provider_request_count": provider_request_count,
                    "providers_tried": list(attribution.attempted_providers or []),
                })

    if parsed is None:
        yield ("result", (failure_message, False, []))
        return

    yield (
        "result",
        _run_turn_actions(
            db,
            parsed,
            turn.symbol_blocks,
            turn.unavailable,
            turn.market_baseline,
            turn.transcript,
        turn.user_content,
        turn.alert_context,
        trace=trace,
        planner_state=turn.planner_state,
        preferences=turn.preferences,
        ),
    )


def _format_trade_plan(plan) -> str:
    """One sentence rendering of a TradePlan — entry/stop/targets/R:R —
    for a chat reply. ``plan`` is a ``backend.ai.prompt.TradePlan``.
    """
    parts = [f"{plan.recommendation.upper()} ({plan.conviction} conviction, {plan.time_horizon})"]
    if plan.entry_zone_low is not None and plan.entry_zone_high is not None:
        parts.append(f"entry {plan.entry_zone_low:g}-{plan.entry_zone_high:g}")
    elif plan.entry_zone_low is not None:
        parts.append(f"entry {plan.entry_zone_low:g}")
    if plan.stop_loss is not None:
        parts.append(f"stop {plan.stop_loss:g}")
    if plan.targets:
        parts.append(f"targets {', '.join(f'{t:g}' for t in plan.targets)}")
    if plan.risk_reward is not None:
        parts.append(f"R:R {plan.risk_reward:.1f}")
    return "Trade plan: " + ", ".join(parts) + f". {plan.thesis}"


def _run_reanalysis(symbol: str) -> tuple[str, bool, dict[str, float]]:
    """Execute the chat's one tool call: a real analyze_symbol() run.

    Reuses analyze_symbol() itself — the same function
    AIAnalysisPanel's "Re-run" button calls — so a chat-triggered
    reanalysis carries the exact same safety contract (never raises;
    degrades to an UncertaintyResponse on AI-off/no-data/malformed
    reply). That degrade path is rendered here as a normal,
    grounded=False chat reply rather than surfaced as an error.
    """
    try:
        result = run_sync(analyze_symbol(symbol))
    except Exception as e:  # noqa: BLE001 — the tool call must never crash the turn
        logger.warning("Chat-triggered reanalysis failed for %s: %s", symbol, e)
        return (
            "I tried to re-run the analysis but hit an error — please try again.",
            False,
            {},
        )

    if isinstance(result, UncertaintyResponse):
        return f"I tried to re-run the analysis for {symbol}, but {result.summary}", False, {}

    text = (
        f"I re-ran the analysis for {symbol}: trend is now {result.trend} "
        f"({result.confidence:.0%} confidence). {result.summary}"
    )
    evidence_values = {"confidence_percent": float(result.confidence * 100)}
    # analyze_symbol() runs with advisory=True by default, so a fresh
    # reanalysis normally carries a trade_plan (entry/stop/targets) —
    # the most actionable part of "the full read" the trader asked for.
    # Surface it instead of silently dropping it.
    if result.trade_plan is not None:
        text += " " + _format_trade_plan(result.trade_plan)
        plan = result.trade_plan
        for key in ("entry_zone_low", "entry_zone_high", "stop_loss", "risk_reward"):
            value = getattr(plan, key, None)
            if isinstance(value, (int, float)):
                evidence_values[key] = float(value)
        for index, value in enumerate(getattr(plan, "targets", []) or []):
            if isinstance(value, (int, float)):
                evidence_values[f"target_{index}"] = float(value)
    return text, True, evidence_values


# --- Action tools (2026-09-11): alert / watchlist CRUD from chat -------
#
# Each handler takes (db, parsed) and returns (text, grounded), the same
# shape every other reply path in this module returns. They reuse the
# real repositories the Alerts/Watchlist pages use — a chat-created
# alert or watchlist ticker is indistinguishable from a manually-created
# one (same tables, same add_to_watchlist -> backfill kickoff).
#
# `db` is the turn's own SQLAlchemy session (ChatRepository.db, passed
# down from answer_chat_message / stream_chat_message) — these handlers
# do not own or close it.

_DESTRUCTIVE_ACTIONS = {
    "delete_alert",
    "remove_from_watchlist",
    "delete_watchlist",
    "save_to_journal",
}

# Actions that change something build_market_baseline() reports on
# (active_alerts / watchlists) — _run_action drops the baseline's cache
# after one of these so the very next turn (which may be seconds later,
# well inside the cache's own TTL) doesn't see a pre-mutation snapshot.
# run_backtest / set_entity_type / run_screen are pure reads (or a
# per-symbol label the baseline doesn't carry), so they're left out —
# no point paying for a rebuild the baseline's own content wouldn't
# reflect anyway.
_BASELINE_MUTATING_ACTIONS = {
    "create_alert",
    "modify_alert",
    "delete_alert",
    "add_to_watchlist",
    "remove_from_watchlist",
    "create_watchlist",
    "delete_watchlist",
}


def _action_step_detail(parsed) -> dict[str, object] | None:
    """The action_confirmation block's tool-specific content.

    Pulled from ChatReplyResponse's own typed action_* fields — never the
    model's free-form prose — so a confirmation card can say what an alert
    or watchlist action actually targeted (e.g. "AAPL price_above 200")
    instead of a bare "create_alert · completed" line. Returns None for
    actions with nothing tool-specific to add (market-data reads already
    get their own visual block; save_to_journal/export_report already get
    their own richer journal_save/report block).
    """
    action = parsed.action
    if action in {"create_alert", "modify_alert"}:
        return {
            "symbol": parsed.action_symbol,
            "condition_type": parsed.action_condition_type,
            "parameter": parsed.action_parameter,
            "label": parsed.action_label,
            "target_id": parsed.action_target_id,
        }
    if action == "delete_alert":
        return {"target_id": parsed.action_target_id}
    if action in {"add_to_watchlist", "remove_from_watchlist"}:
        return {"symbol": parsed.action_symbol, "watchlist": parsed.action_watchlist}
    if action == "create_watchlist":
        return {"watchlist": parsed.action_watchlist}
    if action == "delete_watchlist":
        return {"watchlist": parsed.action_watchlist, "target_id": parsed.action_target_id}
    return None


def _confirm_prompt(db, parsed) -> str:
    """Server-authored confirmation text for a destructive action —
    never the model's own prose, so wording never depends on the model
    getting prompt-following right.

    The model is never told how many watchlists exist or their names
    (that's not in the chat context at all), so it can't reliably judge
    whether "my watchlist" is ambiguous — it either guesses or asks
    defensively even with exactly one list. delete_watchlist resolves
    the real target here, the same way ``_delete_watchlist`` itself
    will once confirmed, so the question always names the actual
    watchlist (or the actual ambiguity) instead of a vague fallback.
    """
    if parsed.action == "delete_alert":
        return "Delete that alert? Say yes to confirm."
    if parsed.action == "remove_from_watchlist":
        sym = parsed.action_symbol or "that ticker"
        where = f' from "{parsed.action_watchlist}"' if parsed.action_watchlist else ""
        return f"Remove {sym}{where}? Say yes to confirm."
    if parsed.action == "delete_watchlist":
        name = parsed.action_watchlist
        if not name:
            wl, ambiguous, candidates = _resolve_watchlist(db, None)
            if ambiguous:
                names = ", ".join(c.name for c in candidates)
                return f"You have more than one watchlist ({names}) — which one should I delete?"
            name = wl.name if wl is not None else "that watchlist"
        return (
            f'Delete the watchlist "{name}"? This removes every ticker in it — say yes to confirm.'
        )
    if parsed.action == "save_to_journal":
        entry = (parsed.action_tool_arguments or {}).get("entry") or {}
        symbol = str(entry.get("symbol") or parsed.action_symbol or "that trade").upper()
        status = str(entry.get("status") or "planned")
        return f'Save the {symbol} {status} trade plan to your Journal? Say yes to confirm.'
    return "That's a destructive action — please confirm first."  # pragma: no cover — defensive


def _kickoff_backfill(symbol: str) -> None:
    """Best-effort: register a newly chat-added symbol for live tracking
    + historical backfill, same as the watchlist REST endpoint does for
    a manually-added ticker. Never lets a failure here break the turn —
    the symbol is still on the watchlist either way, just without a
    backfill kicked off yet (the next poll cycle picks up live quotes
    regardless)."""
    try:
        from backend.api.watchlist.router import _start_symbol_tracking_and_backfill

        _start_symbol_tracking_and_backfill(symbol)
    except Exception as e:  # noqa: BLE001
        logger.warning("chat action: backfill kickoff failed for %s: %s", symbol, e)


def _resolve_watchlist(db, name: str | None, *, containing_symbol: str | None = None):
    """``(watchlist, ambiguous, candidates)``.

    ``name`` given -> look it up by name (None if it doesn't exist;
    never ambiguous).

    ``name`` omitted, no ``containing_symbol`` (add_to_watchlist /
    delete_watchlist) -> the single active watchlist if there's exactly
    one; ambiguous if there are several (the caller should ask which
    one, never guess); ``(None, False, [])`` if there are none yet.

    ``name`` omitted, ``containing_symbol`` given (remove_from_watchlist)
    -> resolved by MEMBERSHIP, not total watchlist count: a ticker on
    exactly one of the trader's lists resolves cleanly even if they have
    several lists overall; on 2+ lists it's ambiguous; on none, it's a
    clean "not found" (not ambiguous — there's nothing to pick between).

    ``candidates`` is only non-empty in the ambiguous case, for the
    caller to name the real options instead of a generic "which one?".
    """
    from backend.repositories.watchlist_repository import WatchlistRepository

    repo = WatchlistRepository(db)
    if name:
        return repo.get_watchlist_by_name(name), False, []

    lists = repo.get_watchlists(active_only=True)

    if containing_symbol:
        holders = [wl for wl in lists if repo.get_watchlist_symbol(wl.id, containing_symbol)]
        if len(holders) == 1:
            return holders[0], False, []
        if not holders:
            return None, False, []
        return None, True, holders

    if len(lists) == 1:
        return lists[0], False, []
    if not lists:
        return None, False, []
    return None, True, lists


def _create_alert(db, parsed) -> tuple[str, bool]:
    from backend.repositories.alert_repository import AlertRepository

    symbol = (parsed.action_symbol or "").upper().strip()
    condition_type = parsed.action_condition_type
    parameter = (parsed.action_parameter or "").strip()
    if not symbol or not condition_type or not parameter:
        return (
            "I need a ticker, a condition, and a threshold to set that alert — "
            'try again with specifics (e.g. "tell me when AAPL breaks above 200").',
            False,
        )
    label = parsed.action_label or f"{symbol} {condition_type.replace('_', ' ')}"
    alert = AlertRepository(db).create(
        name=label,
        symbol=symbol,
        condition_type=condition_type,
        parameter=parameter,
    )
    return (
        f'Done — alert "{alert.name}" set for {symbol} '
        f"({condition_type.replace('_', ' ')} {parameter}).",
        True,
    )


def _modify_alert(db, parsed) -> tuple[str, bool]:
    """Change an EXISTING alert's condition/threshold/name in place,
    instead of the delete-then-recreate the trader would otherwise need
    (which loses the alert's id and its trigger history). Not
    destructive — nothing is removed — so it fires on the first clear
    request like create_alert, no confirmation needed. Only the fields
    the trader actually asked to change should be set; the rest stay
    at their current value (AlertRepository.update only touches a
    field when it's not None).
    """
    from backend.repositories.alert_repository import AlertRepository

    if parsed.action_target_id is None:
        return "I don't have that alert's id — tell me which alert to change.", False
    repo = AlertRepository(db)
    if repo.get_by_id(parsed.action_target_id) is None:
        return "That alert doesn't exist anymore.", False
    if not (parsed.action_condition_type or parsed.action_parameter or parsed.action_label):
        return "What should I change about that alert?", False
    alert = repo.update(
        parsed.action_target_id,
        name=parsed.action_label,
        condition_type=parsed.action_condition_type,
        parameter=parsed.action_parameter,
    )
    if alert is None:  # deleted between the get_by_id check above and here
        return "That alert doesn't exist anymore.", False
    cond = (alert.condition_type or "").replace("_", " ")
    return f'Done — "{alert.name}" is now {alert.symbol} {cond} {alert.parameter}.', True


def _delete_alert(db, parsed) -> tuple[str, bool]:
    from backend.repositories.alert_repository import AlertRepository

    if parsed.action_target_id is None:
        return "I don't have that alert's id — tell me the ticker and I'll look it up.", False
    ok = AlertRepository(db).delete(parsed.action_target_id)
    if not ok:
        return "That alert doesn't exist anymore.", False
    return "Done — that alert is deleted.", True


def _add_to_watchlist(db, parsed) -> tuple[str, bool]:
    from backend.repositories.watchlist_repository import WatchlistRepository

    symbol = (parsed.action_symbol or "").upper().strip()
    if not symbol:
        return "Which ticker should I add?", False
    wl, ambiguous, candidates = _resolve_watchlist(db, parsed.action_watchlist)
    if ambiguous:
        names = ", ".join(c.name for c in candidates)
        return f"You have more than one watchlist ({names}) — which one should I add it to?", True
    repo = WatchlistRepository(db)
    if wl is None:
        wl = repo.create_watchlist(parsed.action_watchlist or "Watchlist")
    _, is_new, did_reenable = repo.add_symbol_to_watchlist(wl.id, symbol)
    if is_new or did_reenable:
        _kickoff_backfill(symbol)
    return f"Done — added {symbol} to {wl.name}.", True


def _remove_from_watchlist(db, parsed) -> tuple[str, bool]:
    from backend.repositories.watchlist_repository import WatchlistRepository

    symbol = (parsed.action_symbol or "").upper().strip()
    if not symbol:
        return "Which ticker should I remove?", False
    # Ambiguity here is by MEMBERSHIP, not total watchlist count: a
    # ticker that's only on one of the trader's lists resolves cleanly
    # even if they have several lists overall (see _resolve_watchlist).
    wl, ambiguous, candidates = _resolve_watchlist(
        db,
        parsed.action_watchlist,
        containing_symbol=symbol,
    )
    if ambiguous:
        names = ", ".join(c.name for c in candidates)
        return (
            f"{symbol} is on more than one watchlist ({names}) — "
            "which one should I remove it from?",
            True,
        )
    if wl is None:
        if parsed.action_watchlist:
            return f'I couldn\'t find a watchlist called "{parsed.action_watchlist}".', False
        return f"{symbol} isn't on any of your watchlists.", False
    ok = WatchlistRepository(db).remove_symbol_from_watchlist(wl.id, symbol)
    if not ok:
        return f"{symbol} wasn't in {wl.name}.", False
    return f"Done — removed {symbol} from {wl.name}.", True


def _create_watchlist(db, parsed) -> tuple[str, bool]:
    from backend.repositories.watchlist_repository import WatchlistRepository

    name = (parsed.action_watchlist or "").strip()
    if not name:
        return "What should I call the new watchlist?", False
    repo = WatchlistRepository(db)
    if repo.get_watchlist_by_name(name) is not None:
        return f'A watchlist called "{name}" already exists.', False
    wl = repo.create_watchlist(name)
    symbol = (parsed.action_symbol or "").upper().strip()
    suffix = ""
    if symbol:
        _, is_new, did_reenable = repo.add_symbol_to_watchlist(wl.id, symbol)
        suffix = f" with {symbol}"
        if is_new or did_reenable:
            _kickoff_backfill(symbol)
    return f'Done — created "{name}"{suffix}.', True


def _delete_watchlist(db, parsed) -> tuple[str, bool]:
    from backend.repositories.watchlist_repository import WatchlistRepository

    wl, ambiguous, candidates = _resolve_watchlist(db, parsed.action_watchlist)
    if ambiguous:
        names = ", ".join(c.name for c in candidates)
        return f"You have more than one watchlist ({names}) — which one should I delete?", True
    if wl is None:
        return "I couldn't find that watchlist.", False
    name = wl.name
    WatchlistRepository(db).delete_watchlist(wl.id)
    return f'Done — deleted "{name}".', True


def _run_backtest(db, parsed) -> tuple[str, bool]:
    """Fresh 6-month daily backtest of the engine's own signals — a
    real, non-destructive read (no confirm gate). The window is fixed,
    not AI-chosen: a date range is a bad thing to trust a weak model to
    fill in, and 6 months / DEFAULT_SIGNALS mirrors what a trader would
    reach for on the Backtest page for a quick check.
    """
    symbol = (parsed.action_symbol or "").upper().strip()
    if not symbol:
        return "Which ticker should I backtest?", False
    if not ai_manager.settings.backtest_tool_enabled:
        return "Backtesting from chat isn't enabled right now.", False

    from backend.api.rate_limit import _backtest_limiter

    # Chat gets its own budget under a fixed key — separate from the
    # REST endpoint's per-IP buckets, same limiter/window.
    allowed, retry_after = _backtest_limiter.is_allowed("chat-tool")
    if not allowed:
        return f"Backtests are rate-limited — try again in {retry_after}s.", False

    from backend.backtesting.engine import DEFAULT_SIGNALS, BacktestConfig, backtest_engine
    from backend.repositories.backtest_repository import BacktestRepository

    end = now_ny()
    config = BacktestConfig(
        symbol=symbol,
        start_date=end - timedelta(days=180),
        end_date=end,
        signals=list(DEFAULT_SIGNALS),
        timeframe="1d",
    )
    run_id = backtest_engine.run(config)
    run = BacktestRepository(db).get_run(run_id)
    if run is None or run.status != "completed" or not run.total_signals:
        return f"Not enough historical data to backtest {symbol} over the last 6 months.", False
    return (
        f"Over the last 6 months, {symbol}'s signals ({run.signals_requested}) fired "
        f"{run.total_signals} times — {run.win_rate_1d:.0%} win rate, avg 1-day return "
        f"{run.avg_return_1d:+.1%} (5-day {run.avg_return_5d:+.1%}).",
        True,
    )


def _set_entity_type(db, parsed) -> tuple[str, bool]:
    """Relabel a watchlist ticker as "stock" or "etf". Not destructive —
    a mislabeled classification is a one-field correction, not data loss
    — so it fires on the first clear request like create_alert, no
    confirm gate."""
    from backend.repositories.watchlist_repository import WatchlistRepository

    symbol = (parsed.action_symbol or "").upper().strip()
    entity_type = parsed.action_entity_type
    if not symbol or entity_type is None:
        return "Which ticker, and should it be a stock or an ETF?", False
    # Same membership-based resolution as remove_from_watchlist: a
    # symbol on exactly one of the trader's lists resolves cleanly even
    # with several lists overall.
    wl, ambiguous, candidates = _resolve_watchlist(
        db,
        parsed.action_watchlist,
        containing_symbol=symbol,
    )
    if ambiguous:
        names = ", ".join(c.name for c in candidates)
        return (
            f"{symbol} is on more than one watchlist ({names}) — which one should I update?",
            True,
        )
    if wl is None:
        if parsed.action_watchlist:
            return f'I couldn\'t find a watchlist called "{parsed.action_watchlist}".', False
        return f"{symbol} isn't on any of your watchlists.", False
    updated = WatchlistRepository(db).update_symbol_in_watchlist(
        wl.id,
        symbol,
        entity_type=entity_type,
    )
    if updated is None:
        return f"{symbol} wasn't in {wl.name}.", False
    label = "an ETF" if entity_type == "etf" else "a stock"
    return f"Done — {symbol} is now marked as {label} in {wl.name}.", True


def _run_screen(db, parsed) -> tuple[str, bool, list[str]]:
    """Screen the trader's watchlist against free-text criteria, via the
    same engine behind ``POST /api/nl-search`` (backend.nl_search) — a
    fully-built AI-parsed screener that was previously only reachable
    from its own dedicated search bar, not from chat. Read-only (no
    confirm gate), same as run_backtest.

    Unlike every other action handler, this one returns a 3-tuple
    (``..., screened_symbols``) — the matched tickers, so
    ``_run_action``/``_finalize_parsed`` can surface them in the turn's
    ``focus`` list. They're genuine new information the trader's own
    message never named (the whole point of screening), so without this
    the frontend's per-ticker quick-action buttons (add to watchlist /
    create alert) would never appear for them.
    """
    from backend.nl_search.executor import execute_query
    from backend.nl_search.parser import parse_query
    from backend.repositories.watchlist_repository import WatchlistRepository

    query = (parsed.action_query or "").strip()
    if not query:
        return "What should I screen your watchlist for?", False, []

    preset = _browser_preset_for_query(query)
    if preset is not None:
        return _run_saved_browser_preset(db, parsed, preset)

    watchlist_id = None
    if parsed.action_watchlist:
        wl = WatchlistRepository(db).get_watchlist_by_name(parsed.action_watchlist)
        if wl is None:
            return f'I couldn\'t find a watchlist called "{parsed.action_watchlist}".', False, []
        watchlist_id = wl.id

    try:
        filters, extras, _parser_used = parse_query(
            query,
            base={"scope": "watchlist", "watchlist_id": watchlist_id},
        )
        result = execute_query(filters, extras=extras, watchlist_id=watchlist_id, db=db)
    except Exception as e:  # noqa: BLE001 — a tool call must never crash the turn
        logger.warning("chat run_screen failed for %r: %s", query, e)
        return "Something went wrong running that screen — please try again.", False, []

    if result.universe_size == 0:
        return "Your watchlist is empty, so there's nothing to screen.", False, []
    if not result.top_n:
        return (
            f"No matches for {result.filter_description} across your "
            f"{result.universe_size} watched symbols.",
            True,
            [],
        )

    shown = result.top_n[:5]
    items = ", ".join(f"{r.symbol} ({r.total_score:+.0f})" for r in shown)
    more = f", +{len(result.top_n) - 5} more" if len(result.top_n) > 5 else ""
    plural = "es" if result.matched_count != 1 else ""
    return (
        f"{result.matched_count} match{plural} for {result.filter_description}: {items}{more}.",
        True,
        [r.symbol for r in shown],
    )


def _browser_preset_for_query(query: str) -> dict | None:
    """Resolve an explicitly requested shared Scanner preset by name."""
    data = _TURN_BROWSER_DATA.get() or {}
    presets = data.get("scan_presets")
    if not isinstance(presets, list):
        return None
    lowered = query.lower()
    candidates = [
        preset for preset in presets
        if isinstance(preset, dict) and isinstance(preset.get("name"), str)
        and preset["name"].strip()
        and preset["name"].lower() in lowered
    ]
    if candidates:
        return candidates[0]
    if len(presets) == 1 and re.search(r"\b(?:my|the|saved)\s+(?:scanner\s+)?(?:preset|scan)\b", lowered):
        return presets[0] if isinstance(presets[0], dict) else None
    return None


def _run_saved_browser_preset(db, parsed, preset: dict) -> tuple[str, bool, list[str]]:
    """Execute a validated browser preset through the Scanner filter engine."""
    from backend.api.scanner.router import _FilterRequest, _build_filter, _scoped_cache, _split_earnings_exclusion, _without_upcoming_earnings
    from backend.scanner.ranking import default_ranking_engine
    from backend.scanner.scanner import market_scanner

    raw_filters = preset.get("filters")
    if not isinstance(raw_filters, list):
        return "That saved Scanner preset has no usable filters.", False, []
    try:
        requested = [_FilterRequest.model_validate(item) for item in raw_filters if isinstance(item, dict)]
        standard, earnings_days = _split_earnings_exclusion(requested)
        scanner_filter = _build_filter(standard, str(preset.get("match") or "AND"))
    except (TypeError, ValueError) as exc:
        logger.info("invalid shared Scanner preset: %s", exc)
        return "I couldn't run that saved Scanner preset because one of its filters is no longer supported.", False, []

    watchlist_id = None
    if parsed.action_watchlist:
        from backend.repositories.watchlist_repository import WatchlistRepository

        watchlist = WatchlistRepository(db).get_watchlist_by_name(parsed.action_watchlist)
        if watchlist is None:
            return f'I couldn\'t find a watchlist called "{parsed.action_watchlist}".', False, []
        watchlist_id = watchlist.id

    from backend.repositories.watchlist_repository import WatchlistRepository

    repository = WatchlistRepository(db)
    watchlists = [repository.get_watchlist(watchlist_id)] if watchlist_id is not None else repository.get_watchlists(active_only=True)
    symbols: list[str] = []
    for watchlist in watchlists:
        if watchlist is not None:
            symbols.extend(row.symbol for row in repository.get_watchlist_symbols(watchlist.id, enabled_only=True))
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        return "Your watchlist is empty, so there's nothing to screen.", False, []
    try:
        run_sync(market_scanner.scan_symbols_async(symbols))
    except Exception as exc:  # noqa: BLE001 — match the normal screen degrade path
        logger.warning("saved Scanner preset failed: %s", exc)
        return "Something went wrong running that saved Scanner preset — please try again.", False, []

    cache = _scoped_cache(symbols)
    matched = [result for result in cache if scanner_filter.matches(result)]
    if earnings_days is not None:
        matched = _without_upcoming_earnings(matched, earnings_days)
    if not matched:
        return f"No matches for the saved preset across your {len(symbols)} watched symbols.", True, []

    ranked = default_ranking_engine.rank_one("strongest_bullish", matched, top_n=10, filter=None)
    shown_symbols = [entry.symbol for entry in (ranked.entries if ranked else [])[:5]]
    if not shown_symbols:
        shown_symbols = [result.symbol for result in matched[:5]]
    items = ", ".join(shown_symbols)
    more = f", +{len(matched) - len(shown_symbols)} more" if len(matched) > len(shown_symbols) else ""
    return f"{len(matched)} match{'es' if len(matched) != 1 else ''} for the saved preset: {items}{more}.", True, shown_symbols


def _calculate(db, parsed, *, trace: list[dict] | None = None) -> tuple[str, bool]:
    """Run a validated deterministic calculation and explain its result."""
    del db  # The calculator is read-only and does not need a database session.
    request = parsed.action_calculation
    if request is None:
        return "Tell me the values and calculation you want me to run.", False
    result = default_registry.execute(
        ToolRequest(tool_name="calculate", arguments=request.model_dump())
    )
    if not result.ok:
        if trace is not None:
            trace.append({
                "tool": "calculate",
                "kind": "calculation",
                "ok": False,
                "provider": result.provider,
                "error": result.error,
                "failure_kind": "calculation_error",
                "duration_ms": result.duration_ms,
                "arguments": sanitize_arguments(request.model_dump(mode="json")),
                "fallback": result.fallback,
            })
        return f"I couldn't calculate that safely: {result.error}", False
    values = ", ".join(f"{key}={value}" for key, value in result.data["values"].items())
    formula = result.data["formulas"][0]
    source_time = result.source_timestamp or "unknown time"
    if trace is not None:
        trace.append({
            "tool": "calculate",
            "kind": "calculation",
            "ok": True,
            "provider": result.provider,
            "source_timestamp": result.source_timestamp,
            "freshness_seconds": result.freshness_seconds,
            "session": result.session,
            "timeframe": result.timeframe,
            "duration_ms": result.duration_ms,
            "arguments": sanitize_arguments(request.model_dump(mode="json")),
            "cache_hit": bool(result.data.get("cache_hit", False)),
            "provider_request_count": 0,
            "fallback": result.fallback,
            "data": {
                "values": result.data.get("values", {}),
                "formulas": result.data.get("formulas", []),
                "operation": request.calculation,
                "request": request.model_dump(mode="json"),
            },
            "request": request.model_dump(mode="json"),
        })
    return (
        f"Verified calculation ({result.provider}, source {source_time}, "
        f"session {result.session}): {values}. Formula: {formula}.",
        True,
    )


_MARKET_TOOL_ACTIONS = {
    "get_quote",
    "get_bars",
    "get_indicator",
    "get_support_resistance",
    "get_market_regime",
    "get_market_context",
    "get_news",
    "get_fundamentals",
    "get_options_snapshot",
    "get_watchlist",
    "get_watchlist_intelligence",
    "get_risk_dashboard",
    "get_trade_journal",
    "get_application_help",
    "get_alerts",
    "get_signal_history",
    "get_saved_scans",
    "get_sector_data",
    "get_trend",
    "get_confluence",
    "get_relative_strength",
    "get_tape_state",
    "get_session_stats",
    "get_calendar",
    "why_did_it_move",
    "what_changed",
    "compare_symbols",
    "scenario_analysis",
    "historical_similarity",
    "signal_explanation",
    "counterargument_review",
    "sensitivity_analysis",
    "market_event_timeline",
    "anomaly_analysis",
    "assumption_tracking",
    "import_csv",
    "build_trade_plan",
    "assess_portfolio_risk",
    "options_research",
    "trade_journal_coach",
    "decision_checklist",
    "save_to_journal",
    "export_report",
}


def _visual_trace_payload(action: str, data: dict) -> tuple[str, dict] | None:
    """Select small, renderable payloads without persisting full tool output."""
    if action == "get_bars":
        bars = data.get("bars")
        if isinstance(bars, list):
            return "chart", {"symbol": data.get("symbol"), "timeframe": data.get("timeframe"), "session": data.get("session"), "bars": bars[-120:]}
    if action in {"get_indicator", "signal_explanation"}:
        indicators = data.get("indicators") if action == "signal_explanation" else {
            str(data.get("indicator", "indicator")): data.get("value"),
        }
        if isinstance(indicators, dict):
            return "indicator_table", {"symbol": data.get("symbol"), "indicators": indicators, "triggers": data.get("triggers", [])}
    if action in {"get_options_snapshot", "options_research"}:
        chains = data.get("chains")
        if isinstance(chains, list):
            trimmed_chains = []
            for chain in chains[:2]:
                if not isinstance(chain, dict):
                    continue
                trimmed_chains.append({
                    **chain,
                    "calls": list(chain.get("calls") or [])[:7],
                    "puts": list(chain.get("puts") or [])[:7],
                })
            return "options_chain", {"symbol": data.get("symbol"), "chains": trimmed_chains, "iv": data.get("near_term_iv"), "iv_rank": data.get("iv_rank"), "source_timestamp": data.get("source_timestamp")}
    if action in {"get_risk_dashboard", "assess_portfolio_risk"}:
        return "risk_card", {key: data.get(key) for key in ("portfolio_value", "gross_exposure", "net_exposure", "concentration", "sector_exposure", "stop_loss_risk", "drawdown", "unknowns", "conclusion") if key in data}
    if action == "scenario_analysis":
        return "scenario", {key: data.get(key) for key in ("shock_percent", "base_gross_exposure", "scenario_gross_exposure", "total_pnl_delta", "base_stop_loss_risk", "scenario_stop_loss_risk", "positions", "unknowns", "conclusion") if key in data}
    if action == "get_session_stats":
        return "session_stats", {key: data.get(key) for key in ("symbol", "session", "date", "open", "high", "low", "close", "volume", "vwap", "range", "change", "change_percent", "bar_count", "available", "reason") if key in data}
    if action == "get_watchlist_intelligence":
        concern = str(data.get("concern") or "all")
        sections = {
            "weak": ("top_bearish", "deteriorating", "weakest"),
            "strong": ("top_bullish", "relative_strength"),
            "deteriorating": ("deteriorating", "weakest"),
            "underperforming": ("top_bearish", "deteriorating", "weakest"),
            "all": ("top_bearish", "top_bullish", "deteriorating"),
        }
        items: list[dict[str, object]] = []
        seen: set[str] = set()
        for section in sections.get(concern, sections["all"]):
            for entry in data.get(section, []) or []:
                if not isinstance(entry, dict):
                    continue
                symbol = str(entry.get("symbol") or "").upper()
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                items.append({
                    "name": symbol,
                    "score": entry.get("score"),
                    "metric": entry.get("metric"),
                    "metric_label": entry.get("metric_label"),
                    "change_pct": entry.get("change_pct"),
                })
                if len(items) >= 10:
                    break
            if len(items) >= 10:
                break
        return "ranked_results", {
            "title": f"{concern.title()} watchlist names",
            "items": items,
            "watchlist_name": data.get("watchlist_name"),
            "coverage": {
                "analyzed_symbols": data.get("analyzed_symbols"),
                "watchlist_size": data.get("watchlist_size"),
                "data_status": data.get("data_status"),
            },
        }
    if action == "historical_similarity":
        return "historical_outcomes", {key: data.get(key) for key in ("symbol", "timeframe", "session", "summaries", "sample_size", "look_ahead_safe", "historical_note", "unknowns") if key in data}
    if action == "compare_symbols" and isinstance(data.get("rankings"), list):
        return "comparison_table", {"columns": ["Rank", "Symbol", "Value", "Metric"], "rows": [[row.get("rank"), row.get("symbol"), row.get("value"), row.get("metric")] for row in data["rankings"][:25]]}
    if action == "get_relative_strength" and isinstance(data.get("signals"), list):
        signals = [item for item in data["signals"] if isinstance(item, dict)]
        # rs_pct is the whole point of a relative-strength ranking (alpha vs
        # each benchmark) — sorting missing values to the bottom instead of
        # crashing or dropping them keeps a partial result still renderable.
        ranked = sorted(signals, key=lambda item: item.get("rs_pct") if isinstance(item.get("rs_pct"), (int, float)) else float("-inf"), reverse=True)
        return "ranked_results", {
            "title": f"{data.get('symbol', 'Symbol')} relative strength vs benchmarks",
            "items": [
                {
                    "name": f"vs {item.get('benchmark', 'benchmark')}",
                    "score": item.get("rs_pct"),
                    "classification": item.get("classification"),
                }
                for item in ranked
            ],
        }
    if action == "anomaly_analysis" and isinstance(data.get("anomalies"), list):
        anomalies = [item for item in data["anomalies"] if isinstance(item, dict)]
        # Not every anomaly type carries a z_score (e.g. large_prints,
        # portfolio_concentration are threshold-triggered, not z-scored) —
        # those sort after the ones that do rather than being excluded.
        ranked = sorted(
            anomalies,
            key=lambda item: abs(item["z_score"]) if isinstance(item.get("z_score"), (int, float)) else -1,
            reverse=True,
        )
        return "ranked_results", {
            "title": f"{data.get('symbol', 'Symbol')} anomalies",
            "items": [
                {
                    "name": str(item.get("type", "anomaly")).replace("_", " "),
                    "score": item.get("z_score"),
                    "severity": item.get("severity"),
                }
                for item in ranked
            ],
        }
    if action == "export_report":
        content = data.get("content")
        if isinstance(content, str):
            return "report", {
                "report_type": data.get("report_type"),
                "title": data.get("title") or "MarketLens report",
                "content": content[:20_000],
                "deep_links": dict(data.get("deep_links") or {}),
                "generated_at": data.get("generated_at"),
                "symbol": data.get("symbol"),
            }
    if action == "save_to_journal" and isinstance(data.get("saved_entry"), dict):
        return "journal_save", {
            "saved_entry": data["saved_entry"],
            "total_entries": data.get("total_entries"),
            "provider": data.get("provider"),
            "source_timestamp": data.get("source_timestamp"),
        }
    return None


def _bounded_numeric_evidence(value, prefix: str = "", *, depth: int = 0) -> dict[str, float]:
    """Keep only a small, JSON-safe numeric index for answer verification."""
    if depth > 4 or isinstance(value, bool):
        return {}
    if isinstance(value, (int, float)):
        return {prefix or "value": float(value)}
    if isinstance(value, dict):
        output: dict[str, float] = {}
        for key, child in list(value.items())[:80]:
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            output.update(_bounded_numeric_evidence(child, child_prefix, depth=depth + 1))
        return output
    if isinstance(value, list):
        output = {}
        for index, child in enumerate(value[:80]):
            output.update(_bounded_numeric_evidence(child, f"{prefix}[{index}]", depth=depth + 1))
        return output
    return {}


def _format_watchlist_intelligence(data: dict) -> str:
    """Render a concise, evidence-only answer for semantic watchlist routes."""
    concern = str(data.get("concern") or "all")
    watchlist_name = str(data.get("watchlist_name") or "your watchlist")
    labels = {
        "weak": "Weakest names",
        "strong": "Strongest names",
        "deteriorating": "Most deteriorating names",
        "underperforming": "Most underperforming names",
        "all": "Watchlist intelligence",
    }
    sections = {
        "weak": ("top_bearish", "deteriorating", "weakest"),
        "strong": ("top_bullish", "relative_strength", "mtf_alignment"),
        "deteriorating": ("deteriorating", "top_bearish", "weakest"),
        "underperforming": ("top_bearish", "deteriorating", "weakest"),
        "all": ("top_bearish", "top_bullish", "deteriorating", "relative_strength"),
    }
    benchmark_symbol = str(data.get("benchmark_symbol") or "").upper()
    if benchmark_symbol and concern in {"weak", "underperforming"}:
        sections[concern] = ("relative_strength", "top_bearish", "deteriorating", "weakest")

    rows: list[str] = []
    seen: set[str] = set()
    used_relative_weakness = False
    clear_weakness = False
    for section in sections.get(concern, sections["all"]):
        for item in data.get(section, []) or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            if section == "weakest":
                used_relative_weakness = True
                if isinstance(item.get("metric"), (int, float)) and item["metric"] < 0:
                    clear_weakness = True
            else:
                clear_weakness = True
            evidence: list[str] = []
            change_pct = item.get("change_pct")
            if isinstance(change_pct, (int, float)):
                evidence.append(f"change {change_pct:+.2f}%")
            score = item.get("score")
            if isinstance(score, (int, float)):
                evidence.append(f"score {score:+.1f}")
            metric = item.get("metric")
            metric_label = item.get("metric_label")
            if isinstance(metric, (int, float)) and metric_label:
                evidence.append(f"{metric_label} {metric:+.2f}")
            details = item.get("details") or {}
            benchmark = details.get("benchmark") if isinstance(details, dict) else None
            if benchmark:
                evidence.append(f"vs {benchmark}")
            rows.append(f"- {symbol}: {', '.join(evidence) or 'scanner evidence available'}")
            if len(rows) >= 5:
                break
        if len(rows) >= 5:
            break

    status = data.get("data_status") or "unknown"
    analyzed = data.get("analyzed_symbols")
    total = data.get("watchlist_size")
    timeframe = str(data.get("timeframe") or "").strip().lower()
    timeframe_label = {"1d": "daily", "1wk": "weekly"}.get(timeframe)
    coverage = f"Coverage: {analyzed} of {total} names have scanner data." if isinstance(analyzed, int) and isinstance(total, int) else None
    warnings = [str(item) for item in (data.get("warnings") or []) if item]

    if not rows:
        qualifier = f" relative to {benchmark_symbol}" if benchmark_symbol else ""
        answer = f"I couldn't identify any {concern} names{qualifier} in \"{watchlist_name}\" from the current scanner cache."
    elif used_relative_weakness and not clear_weakness and concern in {"weak", "underperforming"}:
        scope = f" on the {timeframe_label} timeframe" if timeframe_label else ""
        qualifier = f" relative to {benchmark_symbol}" if benchmark_symbol else ""
        answer = f"No clearly weak names were found{qualifier} in \"{watchlist_name}\". Relative weakest scanner scores{scope}:\n" + "\n".join(rows)
    else:
        scope = f" on the {timeframe_label} timeframe" if timeframe_label else ""
        qualifier = f" relative to {benchmark_symbol}" if benchmark_symbol else ""
        answer = f"{labels.get(concern, labels['all'])}{qualifier} in \"{watchlist_name}\"{scope}:\n" + "\n".join(rows)
    if not rows and timeframe_label:
        answer += f" Timeframe: {timeframe_label}."
    if coverage:
        answer += f"\n{coverage}"
    if status != "ready":
        answer += f"\nData status: {status}."
    if warnings:
        answer += "\nNote: " + " ".join(warnings[:2])
    return answer


def _run_market_tool(
    db,
    parsed,
    *,
    planner_state: dict | None = None,
    trace: list[dict] | None = None,
) -> tuple[str, bool]:
    """Execute one read-only grounded market-data tool selected by Chat."""
    del db
    arguments = dict(parsed.action_tool_arguments or {})
    if parsed.action == "assumption_tracking":
        # The session owns the ledger.  Never trust the model to carry the
        # previous records forward or to rewrite their original fields.
        arguments["existing_assumptions"] = list((planner_state or {}).get("research_assumptions", []))
    arguments = _apply_regeneration_scope(parsed.action, arguments, planner_state)
    arguments = _apply_date_scope(parsed.action, arguments, planner_state)
    trace_argument_source = arguments
    arguments, browser_markers = _apply_browser_data(parsed.action, arguments)
    request_scope: dict[str, object] = {}
    if isinstance(arguments.get("session"), str):
        try:
            request_scope["session"] = normalize_session(arguments["session"])
        except ValueError:
            pass
    if isinstance(arguments.get("timeframe"), str):
        try:
            request_scope["timeframe"] = normalize_timeframe(arguments["timeframe"])
        except ValueError:
            pass
    elif parsed.action in {
        "get_quote",
        "get_bars",
        "get_indicator",
        "get_support_resistance",
        "get_trend",
        "get_confluence",
        "get_relative_strength",
        "get_tape_state",
        "get_session_stats",
        "why_did_it_move",
        "what_changed",
        "signal_explanation",
        "historical_similarity",
    }:
        try:
            request_scope["timeframe"] = normalize_timeframe(
                str((planner_state or {}).get("timeframe") or "1d")
            )
        except ValueError:
            request_scope["timeframe"] = "1d"
    result = default_registry.execute(
        ToolRequest(
            tool_name=parsed.action,
            arguments=arguments,
            confirmed=bool(parsed.action_confirmed),
            **request_scope,
        )
    )
    if not result.ok:
        if trace is not None:
            trace.append({
                "tool": parsed.action,
                "ok": False,
                "provider": result.provider,
                "error": result.error,
                "failure_kind": result.failure_kind or "tool_error",
                "duration_ms": result.duration_ms,
                "arguments": {**sanitize_arguments(trace_argument_source), **browser_markers},
                "provider_request_count": 0,
                "fallback": result.fallback,
            })
        if parsed.action == "get_watchlist_intelligence":
            scope = str(
                arguments.get("name")
                or (planner_state or {}).get("watchlist_scope", {}).get("name")
                or "All active watchlists"
            )
            timeframe = str(arguments.get("timeframe") or "1d").lower()
            timeframe_label = {"1d": "daily", "1wk": "weekly"}.get(timeframe, timeframe)
            return (
                f'I couldn\'t retrieve {timeframe_label} watchlist intelligence for "{scope}": '
                f"{result.error}",
                False,
            )
        if parsed.action == "get_risk_dashboard" and parsed.action_query in {"portfolio_change", "portfolio_weakness"}:
            label = "changes" if parsed.action_query == "portfolio_change" else "weakness ranking"
            return f"Portfolio {label} is unavailable from {result.provider or 'MarketLens'}: {result.error}", False
        return f"I couldn't retrieve that safely: {result.error}", False
    if trace is not None:
        trace_item = {
            "tool": parsed.action,
            "ok": True,
            "provider": result.provider,
            "freshness_seconds": result.freshness_seconds,
            "source_timestamp": result.source_timestamp,
            "session": result.session,
            "timeframe": result.timeframe,
            "fallback": result.fallback,
            "entitlement": result.entitlement,
            "warnings": result.warnings,
            "duration_ms": result.duration_ms,
            "arguments": {**sanitize_arguments(trace_argument_source), **browser_markers},
            "cache_hit": bool(result.data.get("cache_hit", False)),
            "provider_request_count": int(
                result.data.get("provider_request_count", 0)
                if str(result.data.get("provider_request_count", 0)).isdigit()
                else (0 if str(result.provider).lower().startswith("marketlens") else 1)
            ),
        }
        if isinstance(arguments.get("symbol"), str):
            trace_item["symbol"] = arguments["symbol"].upper()
        numeric_evidence = _bounded_numeric_evidence(result.data)
        if isinstance(result.freshness_seconds, (int, float)):
            numeric_evidence["freshness_seconds"] = float(result.freshness_seconds)
        if numeric_evidence:
            trace_item["evidence_values"] = numeric_evidence
        visual = _visual_trace_payload(parsed.action, result.data)
        if visual is not None:
            trace_item["visual_type"], trace_item["visual_data"] = visual
        trace.append(trace_item)
    if parsed.action == "assumption_tracking" and planner_state is not None:
        records = result.data.get("assumptions")
        if isinstance(records, list):
            planner_state["research_assumptions"] = records[:200]
    if parsed.action == "get_watchlist_intelligence" and planner_state is not None:
        # Persist only the server-resolved scope and concern. The next
        # follow-up may reuse it, but raw scanner rows never enter memory.
        resolved_name = str(result.data.get("watchlist_name") or "").strip()
        planner_state["watchlist_scope"] = {
            "name": resolved_name or None,
            "aggregate": resolved_name.lower() == "all active watchlists",
            "concern": str(result.data.get("concern") or arguments.get("concern") or "all"),
        }
    if parsed.action == "get_watchlist_intelligence":
        return _format_watchlist_intelligence(result.data), True
    if parsed.action == "get_market_context":
        regime = str(result.data.get("regime") or "unknown").replace("_", " ")
        volatility = str(result.data.get("volatility_state") or "unknown")
        confidence = result.data.get("confidence")
        momentum = result.data.get("momentum")
        trend_strength = result.data.get("trend_strength")
        if regime == "unknown" and not any(
            isinstance(value, (int, float)) for value in (confidence, momentum, trend_strength)
        ):
            return "I don't have enough verified market-context data to summarize the market.", False
        details = [f"regime is {regime}", f"volatility is {volatility}"]
        if isinstance(momentum, (int, float)):
            details.append(f"momentum {float(momentum):+.2f}")
        if isinstance(trend_strength, (int, float)):
            details.append(f"trend strength {float(trend_strength):.2f}")
        if isinstance(confidence, (int, float)):
            details.append(f"confidence {float(confidence):.0%}")
        return "Verified market context: " + "; ".join(details) + ".", True
    if parsed.action == "get_trend":
        symbol = str(result.data.get("symbol") or arguments.get("symbol") or "the symbol").upper()
        direction = str(result.data.get("direction") or "unknown").replace("_", " ")
        strength = str(result.data.get("strength") or "unknown").replace("_", " ")
        classification = str(result.data.get("classification") or "").replace("_", " ")
        if direction == "unknown" and strength == "unknown":
            return f"I don't have enough verified trend data for {symbol}.", False
        details = [f"{direction} direction", f"{strength} strength"]
        if classification:
            details.append(f"{classification} classification")
        timeframe = result.timeframe or arguments.get("timeframe") or "1d"
        return f"Verified {symbol} trend ({timeframe}): " + "; ".join(details) + ".", True
    if parsed.action == "get_indicator":
        return _format_indicator_reply(
            result.data,
            provider=result.provider,
            freshness_seconds=result.freshness_seconds,
            timeframe=result.timeframe,
            arguments=arguments,
            query=getattr(parsed, "action_query", None),
        ), True
    if parsed.action == "what_changed":
        return _format_change_reply(
            result.data,
            provider=result.provider,
            source_timestamp=result.source_timestamp,
            freshness_seconds=result.freshness_seconds,
            timeframe=result.timeframe,
            arguments=arguments,
        ), True
    if parsed.action == "compare_symbols" and isinstance(result.data.get("rankings"), list):
        if isinstance(result.freshness_seconds, (int, float)) and result.freshness_seconds > 900:
            age = f"{result.freshness_seconds / 3600:.1f} hours" if result.freshness_seconds >= 3600 else f"{result.freshness_seconds / 60:.1f} minutes"
            # Do not persist/render the stale ranking table as if it were a
            # usable result. Keep the failed evidence record so the UI can
            # explain why the comparison was withheld.
            if trace:
                trace[-1]["ok"] = False
                trace[-1]["error"] = "Comparison bars are stale"
                trace[-1]["failure_kind"] = "stale"
                trace[-1].pop("visual_type", None)
                trace[-1].pop("visual_data", None)
            return (
                "I couldn't verify that comparison because the available comparison bars "
                f"are {age} old. Refresh market data and retry.",
                False,
            )
        metric = str(result.data.get("metric") or arguments.get("metric") or "value")
        metric_labels = {
            "return_percent": "return",
            "change_percent": "daily change",
            "volatility_percent": "volatility",
            "price": "price",
            "volume": "volume",
        }
        label = metric_labels.get(metric, metric.replace("_", " "))
        rows = []
        for row in result.data["rankings"][:10]:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            value = row.get("value")
            rank = row.get("rank")
            if not symbol or not isinstance(value, (int, float)):
                continue
            suffix = "%" if metric.endswith("_percent") else ""
            prefix = "$" if metric == "price" else ""
            rows.append(f"{symbol} {prefix}{float(value):.2f}{suffix} (rank {rank})")
        if rows:
            return f"Verified compare_symbols comparison by {label}: " + "; ".join(rows) + ".", True
    if parsed.action in _BROWSER_LOCAL_ACTIONS:
        return _format_browser_local_reply(
            parsed.action,
            _browser_safe_reply_data(parsed.action, result.data),
            result.provider,
            query=getattr(parsed, "action_query", None),
        ), True
    return _format_generic_market_reply(
        parsed.action,
        result.data,
        provider=result.provider,
        freshness_seconds=result.freshness_seconds,
        source_timestamp=result.source_timestamp,
        timeframe=result.timeframe,
        arguments=arguments,
    ), True


_ACTION_HANDLERS = {
    "create_alert": _create_alert,
    "modify_alert": _modify_alert,
    "delete_alert": _delete_alert,
    "add_to_watchlist": _add_to_watchlist,
    "remove_from_watchlist": _remove_from_watchlist,
    "create_watchlist": _create_watchlist,
    "delete_watchlist": _delete_watchlist,
    "run_backtest": _run_backtest,
    "set_entity_type": _set_entity_type,
    "run_screen": _run_screen,
    "calculate": _calculate,
}


def _remember_watchlist_action(parsed, succeeded: bool, planner_state: dict | None) -> None:
    """Keep the "current watchlist" memory in step with watchlist actions."""
    name = (parsed.action_watchlist or "").strip()
    if planner_state is None or not succeeded or not name:
        return
    if parsed.action in {"create_watchlist", "add_to_watchlist", "remove_from_watchlist"}:
        planner_state["watchlist"] = name
    elif parsed.action == "delete_watchlist" and str(planner_state.get("watchlist") or "").lower() == name.lower():
        planner_state["watchlist"] = None


def _run_action(
    db,
    parsed,
    *,
    planner_state: dict | None = None,
    trace: list[dict] | None = None,
) -> tuple[str, bool, list[str]]:
    """Execute one action tool. Never raises — a failure degrades to a
    plain reply with grounded=False, same contract as _run_reanalysis.

    Most handlers return ``(text, grounded)``; run_screen returns a
    3-tuple with the tickers it surfaced (see its docstring) — normalized
    to ``(text, grounded, screened)`` here either way.
    """
    handler = _ACTION_HANDLERS.get(parsed.action)
    if parsed.action not in _MARKET_TOOL_ACTIONS and handler is None:  # pragma: no cover — action is a closed Literal
        return "I couldn't do that — please try again.", False, []
    try:
        if parsed.action in _MARKET_TOOL_ACTIONS:
            # The registry converts validation errors into ok=False results,
            # but a provider/engine exception (e.g. InsufficientDataError)
            # still propagates from the handler.
            return _run_market_tool(db, parsed, planner_state=planner_state, trace=trace) + ([],)
        result = (
            _calculate(db, parsed, trace=trace)
            if parsed.action == "calculate"
            else handler(db, parsed)
        )
    except Exception as e:  # noqa: BLE001 — a tool call must never crash the turn
        logger.warning("chat action %s failed: %s", parsed.action, e)
        if trace is not None:
            trace.append({
                "tool": parsed.action,
                "ok": False,
                "provider": "MarketLens",
                "error": str(e),
                "failure_kind": "action_exception",
                "arguments": _action_trace_arguments(parsed),
                "provider_request_count": 0,
                "fallback": False,
            })
        return "Something went wrong doing that — please try again.", False, []
    if parsed.action in _BASELINE_MUTATING_ACTIONS:
        from backend.ai.market_baseline import invalidate_cache

        invalidate_cache()
    _remember_watchlist_action(parsed, bool(result[1]), planner_state)
    if len(result) == 3:
        if trace is not None and parsed.action != "calculate":
            trace.append({
                "tool": parsed.action,
                "ok": True,
                "provider": "MarketLens",
                "arguments": _action_trace_arguments(parsed),
                "provider_request_count": 0,
                "fallback": False,
            })
        return result
    text, grounded = result
    if trace is not None and parsed.action != "calculate":
        trace.append({
            "tool": parsed.action,
            "ok": grounded,
            "provider": "MarketLens",
            "arguments": _action_trace_arguments(parsed),
            "provider_request_count": 0,
            "fallback": False,
        })
    return text, grounded, []
