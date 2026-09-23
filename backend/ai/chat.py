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
remove_from_watchlist, delete_watchlist) get a hard, backend-enforced
confirm gate in _finalize_parsed — a destructive action never runs
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
still undone. Bounded by _MAX_CHAIN_STEPS, and a destructive step
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
from datetime import timedelta

from backend.ai.analyze import analyze_symbol
from backend.ai.calculator import CalculationRequest
from backend.ai.chat_symbols import resolve_turn_symbols
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
from backend.ai.reply_stream import ReplyExtractor

# Chat runs its sync generator helpers on loop-less worker threads
# (ThreadPoolExecutor / asyncio.to_thread), so the async AI calls are
# bridged with run_sync/stream_sync rather than awaited.
from backend.ai.sync_bridge import run_sync, stream_sync
from backend.ai.tool_registry import ToolRequest, default_registry
from backend.config.settings import settings
from backend.models import AlertTrigger, ChatMessage
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
_MULTI_STEP_HINT = re.compile(r"\band\b|\bthen\b|\balso\b|;", re.I)
_AMBIGUOUS_REFERENCE = re.compile(
    r"\b(it|that stock|that ticker|the previous ticker|this one|that one)\b", re.I
)
# First action + up to this many chained follow-ups within one turn.
_MAX_CHAIN_STEPS = 3
_MAX_TURN_SECONDS = 30.0


def _chain_step_limit() -> int:
    """Read the bounded continuation budget from settings safely."""
    configured = min(
        getattr(settings.ai, "chat_max_chain_steps", _MAX_CHAIN_STEPS),
        getattr(settings.ai, "chat_max_planning_calls", _MAX_CHAIN_STEPS),
    )
    return max(1, min(int(configured), 8))


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


def _numbers_from_text(text: str) -> list[float]:
    return [float(raw.replace(",", "")) for raw in _CALC_NUMBER_RE.findall(text)]


def _fallback_calculation(user_content: str) -> CalculationRequest | None:
    """Build a calculator request for simple, unambiguous user wording.

    This is intentionally conservative: it requires an operation keyword and
    the exact number of inputs needed, otherwise the model gets a chance to
    clarify the request normally.
    """
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
    """Deterministically resolve a specific, real watchlist the trader
    NAMED in this message (e.g. 'analyze "My Watch" watchlist') to its
    real member symbols, so the turn builds genuine <context> blocks for
    them instead of the model declining with "no visibility" (see
    CHAT_SYSTEM_PROMPT rule 10) — that rule is for when no real
    watchlist can be resolved this way, not this case.

    Returns ``[]`` (a silent no-op) whenever the captured name doesn't
    match a real watchlist — never a guess.
    """
    m = _NAMED_WATCHLIST_RE.search(user_content)
    if m:
        name = m.group("name1") or m.group("name2") or m.group("name3") or ""
    else:
        m2 = _NAMED_WATCHLIST_LEADING_RE.match(user_content)
        name = m2.group("name") if m2 else ""
    name = name.strip(" \"'“”")
    if not name:
        return []
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
        return []
    return [s.symbol for s in wl.symbols if s.is_enabled]


# Short-lived per-symbol context cache — a burst of follow-ups about one
# name rebuilt the whole scan + aux-data each turn.
_CTX_TTL = 12.0
_CTX_CAP = 64
_ctx_lock = threading.Lock()
_ctx_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()


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
    return {
        "symbol": trigger.symbol,
        "message": trigger.message,
        "observed_value": trigger.observed_value,
        "ai_commentary": trigger.ai_commentary,
        "triggered_at": str(trigger.triggered_at) if trigger.triggered_at else None,
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
    max_steps: int


def _prepare_turn(repo: ChatRepository, session_id: int, user_content: str) -> _Turn:
    """Persist the user message and assemble the turn's quant context.

    Resolves the turn's tickers from the text (0..N, capped), attaches a
    cheap market-wide baseline when relevant, and fans the per-symbol
    context builds out over a thread pool.
    """
    session = repo.get_session(session_id)
    if session is None:
        raise ValueError(f"chat session {session_id} not found")

    repo.add_message(session_id, "user", user_content)

    try:
        planner_state = json.loads(session.planner_state or "{}")
        if not isinstance(planner_state, dict):
            planner_state = {}
    except (TypeError, ValueError):
        planner_state = {}

    history = repo.get_messages(session_id, limit=_TRANSCRIPT_TURNS + 1)
    # Exclude the message we just added — it's passed separately as
    # `new_message`, not duplicated into the transcript. Long prior
    # replies are clipped so the transcript can't dominate.
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
        named_wl_symbols = _resolve_named_watchlist_symbols(repo.db, user_content)
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

    # What this turn actually asks for — skip the rest.
    want_news = single and bool(_NEWS_INTENT.search(user_content))
    want_funda = single and bool(_FUNDA_INTENT.search(user_content))
    want_stats = bool(_STATS_INTENT.search(user_content))
    want_baseline = (
        (not symbols and not _CALCULATION_HINT.search(user_content))
        or bool(_MARKET_INTENT.search(user_content))
        or bool(_ACTION_INTENT.search(user_content))
    )

    def _ctx(sym: str) -> dict:
        return _build_context_cached(sym, news=want_news, funda=want_funda, diverg=single)

    # The market baseline and each per-symbol context are independent
    # blocking calls (DB reads, a scan, aux-data HTTP) — fan them out.
    symbol_blocks: list[dict] = []
    unavailable: list[str] = []
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
    current_calculation = _fallback_calculation(user_content)
    next_state = {
        "current_symbols": current_symbols,
        "previous_ticker": previous_ticker,
        "watchlist": planner_state.get("watchlist"),
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
        "updated_at": now_ny().isoformat(),
    }
    if next_state["timeframe"] is None:
        next_state["timeframe"] = planner_state.get("timeframe")
    if next_state["session"] is None:
        next_state["session"] = planner_state.get("session")
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
    )


def _extract_memory_value(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    value = match.group(1).lower().replace(" ", "_")
    return "after_hours" if value in {"after-hour", "after-hours", "after_hours"} else value


def answer_chat_message(
    session_id: int, user_content: str
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

    Never raises for an expected failure mode — AI-off, a per-symbol
    context failure, or a malformed AI reply all produce a stored
    assistant message explaining that, not an exception. An unexpected
    failure (e.g. the session doesn't exist) still raises.
    """
    repo = ChatRepository()
    try:
        turn = _prepare_turn(repo, session_id, user_content)
        trace: list[dict] = []
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
        )
        if trace:
            turn.planner_state["last_tool_result"] = {
                "tool": trace[-1].get("tool"),
                "ok": trace[-1].get("ok"),
                "provider": trace[-1].get("provider"),
                "source_timestamp": trace[-1].get("source_timestamp"),
            }
        repo.set_planner_state(session_id, json.dumps(turn.planner_state, sort_keys=True))
        # _generate_reply delegates action execution to _run_turn_actions;
        # attach its transient trace to the returned ORM object for the API.
        # It is intentionally not persisted in the prose message row.
        assistant_message = repo.add_message(session_id, "assistant", reply_text)
        assistant_message.planner_trace = trace
        grounded = grounded and not turn.unavailable  # deterministic fail-safe
        # `screened` is populated only by the run_screen tool — tickers the
        # turn's own message never named, so turn.focus (derived from the
        # user's text) wouldn't otherwise include them, and the frontend's
        # per-ticker quick-action buttons (add to watchlist / create alert)
        # key off `focus`/`partial` alone.
        focus = list(dict.fromkeys([*turn.focus, *screened]))
        return assistant_message, grounded, focus, turn.partial, turn.unavailable
    finally:
        repo.close()


def stream_chat_message(session_id: int, user_content: str) -> Iterator[tuple]:
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
    repo = ChatRepository()
    try:
        turn = _prepare_turn(repo, session_id, user_content)
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
        repo.set_planner_state(session_id, json.dumps(turn.planner_state, sort_keys=True))
        msg = repo.add_message(session_id, "assistant", final_text)
        msg.planner_trace = trace
        # See answer_chat_message's matching comment — `screened` (from
        # run_screen) is merged into `focus` so the frontend's quick-action
        # buttons pick up tickers the turn's own message never named.
        focus = list(dict.fromkeys([*turn.focus, *screened]))
        yield ("final", (msg, grounded, focus, turn.partial, turn.unavailable))
    finally:
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


def _complete_and_parse(prompt: str, system: str, max_tokens: int, model: str | None):
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
        try:
            resp = run_sync(
                ai_manager.complete(
                    prompt=prompt,
                    system=system,
                    max_tokens=max_tokens,
                    model=model,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Chat AI call raised (attempt %d): %s", attempt + 1, e)
            failure_reason = "ai_error"
            continue
        if resp.text is None:
            logger.warning("Chat AI call returned no text (attempt %d)", attempt + 1)
            failure_reason = "ai_error"
            continue
        try:
            return parse_chat_reply(resp.text), None
        except Exception as e:  # noqa: BLE001
            logger.info("Chat reply failed to parse (attempt %d): %s", attempt + 1, e)
            failure_reason = "parse_error"
    return None, failure_reason


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
    # Friendly degrade for a legacy single-symbol session whose only
    # ticker has no data (keeps the pre-universal wording).
    if (
        not symbol_blocks
        and unavailable
        and base_symbols
        and set(unavailable) == {s.upper() for s in base_symbols}
    ):
        return f"I don't have enough data on {unavailable[0]} yet to answer that.", False, []

    if not symbol_blocks:
        m = _WATCHLIST_CONTENTS_INTENT.search(user_content)
        if m:
            reply = _watchlist_contents_reply(db, m.group("name1") or m.group("name2") or "")
            if reply:
                return reply, True, []
        if _WATCHLIST_LIST_INTENT.search(user_content):
            return _watchlist_list_reply(db), True, []
        remembered = (planner_state or {}).get("current_symbols", [])
        if _AMBIGUOUS_REFERENCE.search(user_content) and len(remembered) > 1:
            return f"Which ticker do you mean: {', '.join(remembered[:settings.ai.chat_max_tickers])}?", False, []

    # Exact arithmetic is deterministic and must not spend an AI/provider
    # call. Missing inputs get a precise clarification instead of a generic
    # model failure; a follow-up may explicitly reuse the prior inputs.
    if _CALCULATION_HINT.search(user_content):
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
                "relevant prices, position size, portfolio value, or risk inputs.",
                False,
                [],
            )
        deterministic = ChatReplyResponse(
            reply="Verified calculation",
            grounded=True,
            action="calculate",
            action_calculation=calculation,
        )
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
        return "AI is currently unavailable, so I can't answer that right now.", False, []

    budget = max(2000, ai_manager.settings.max_tokens - 500)
    prompt = build_chat_prompt(
        symbol_blocks,
        unavailable,
        market_baseline,
        transcript,
        user_content,
        alert_context,
        capped_note=_capped_note(capped, symbol_blocks),
        token_budget=budget,
    )
    parsed, failure_reason = _complete_and_parse(
        prompt,
        CHAT_SYSTEM_PROMPT,
        500,
        ai_manager.settings.chat_model or None,
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
    )


def _capped_note(capped: bool, symbol_blocks: list[dict]) -> str | None:
    if not capped:
        return None
    shown = ", ".join(b["symbol"] for b in symbol_blocks) or "the first few"
    return f"(You named more tickers than I can dig into at once — I looked at {shown}.)"


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
            text, grounded = _run_reanalysis(target)
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
                    parsed.action_confirmed = True
                elif confirmed is not None:
                    parsed.action, parsed.action_symbol, parsed.action_watchlist = confirmed
                    parsed.action_confirmed = True
    if parsed.action != "none":
        if parsed.action in _DESTRUCTIVE_ACTIONS and not parsed.action_confirmed:
            if planner_state is not None:
                planner_state["pending_confirmation"] = {
                    "action": parsed.action,
                    "symbol": parsed.action_symbol,
                    "watchlist": parsed.action_watchlist,
                    "target_id": parsed.action_target_id,
                }
            return _confirm_prompt(db, parsed), True, []
        if planner_state is not None and parsed.action_confirmed:
            planner_state["pending_confirmation"] = None
        return _run_action(db, parsed, trace=trace)
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
) -> tuple[str, bool, list[str]]:
    """Runs ``parsed``'s tool via ``_finalize_parsed``, then — only when
    the trader's own message hints at more than one request (see
    ``_MULTI_STEP_HINT``) and that first step was a real, already-
    executed action — asks the model up to ``_MAX_CHAIN_STEPS - 1`` more
    times whether anything from the ORIGINAL message is still undone,
    running each additional step the same way. See the module docstring
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
    if not _action_was_executed(parsed) or not _MULTI_STEP_HINT.search(user_content):
        return text, grounded, screened

    texts = [f"Step 1: {text}"]
    all_grounded = grounded
    all_screened = list(screened)
    planner = _PlannerState(
        original_request=user_content,
        executed_signatures={_action_signature(parsed)},
        completed_steps=[text],
        errors=[],
        max_steps=_chain_step_limit(),
    )
    turn_started = time.monotonic() if started_at is None else started_at
    turn_budget = _turn_budget_seconds()

    for _ in range(planner.max_steps - 1):
        if time.monotonic() - turn_started >= turn_budget:
            texts.append("I stopped the remaining step because this turn reached its time budget.")
            all_grounded = False
            planner.errors.append("time_budget_exhausted")
            break
        continuation = (
            "Original request: " + user_content + "\nAlready executed: " + " ".join(texts)
        )
        budget = max(2000, ai_manager.settings.max_tokens - 500)
        prompt = build_chat_prompt(
            symbol_blocks,
            unavailable,
            market_baseline,
            transcript,
            continuation,
            alert_context,
            token_budget=budget,
        )
        next_parsed, failure_reason = _complete_and_parse(
            prompt,
            CHAT_CONTINUATION_SYSTEM_PROMPT,
            300,
            ai_manager.settings.chat_model or None,
        )
        if next_parsed is None:
            logger.info("chat multi-step continuation failed: %s", failure_reason)
            texts.append("I completed the available step, but could not safely plan the next step.")
            all_grounded = False
            break

        if next_parsed.wants_reanalysis or next_parsed.action == "none":
            break

        signature = _action_signature(next_parsed)
        if signature in planner.executed_signatures:
            texts.append("I stopped the remaining step because it repeated an action already executed in this turn.")
            all_grounded = False
            planner.errors.append("duplicate_action")
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
        planner.completed_steps.append(step_text)
        all_grounded = all_grounded and step_grounded
        all_screened.extend(step_screened)
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

    if not turn.symbol_blocks:
        m = _WATCHLIST_CONTENTS_INTENT.search(turn.user_content)
        if m:
            reply = _watchlist_contents_reply(db, m.group("name1") or m.group("name2") or "")
            if reply:
                yield ("result", (reply, True, []))
                return
        if _WATCHLIST_LIST_INTENT.search(turn.user_content):
            yield ("result", (_watchlist_list_reply(db), True, []))
            return
        remembered = turn.planner_state.get("current_symbols", [])
        if _AMBIGUOUS_REFERENCE.search(turn.user_content) and len(remembered) > 1:
            yield (
                "result",
                (f"Which ticker do you mean: {', '.join(remembered[:settings.ai.chat_max_tickers])}?", False, []),
            )
            return

    if not ai_manager.enabled:
        yield (
            "result",
            (
                "AI is currently unavailable, so I can't answer that right now.",
                False,
                [],
            ),
        )
        return

    budget = max(2000, ai_manager.settings.max_tokens - 500)
    prompt = build_chat_prompt(
        turn.symbol_blocks,
        turn.unavailable,
        turn.market_baseline,
        turn.transcript,
        turn.user_content,
        turn.alert_context,
        capped_note=_capped_note(turn.capped, turn.symbol_blocks),
        token_budget=budget,
    )

    chat_model = ai_manager.settings.chat_model or None
    parsed = None
    failure_message = "I couldn't process that — could you rephrase?"
    # Same retry rationale as _complete_and_parse — a malformed/empty
    # reply gets one more full attempt before giving up. A retry's
    # deltas stream to the client same as the first attempt's; the
    # trailing ("result", ...) below is always authoritative and
    # overwrites whatever partial text was shown, same as the existing
    # reanalysis-tool path already does.
    for attempt in range(_CHAT_PARSE_RETRIES + 1):
        raw = ""
        extractor = ReplyExtractor()
        try:
            if ai_manager.settings.chat_streaming:
                for chunk in stream_sync(
                    ai_manager.stream(
                        prompt,
                        system=CHAT_SYSTEM_PROMPT,
                        max_tokens=500,
                        model=chat_model,
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
                        model=chat_model,
                    )
                )
                raw = resp.text or ""
                delta = extractor.feed(raw)
                if delta:
                    yield ("delta", delta)
        except Exception as e:  # noqa: BLE001
            logger.warning("Chat streaming AI call raised (attempt %d): %s", attempt + 1, e)
            failure_message = "Something went wrong reaching the AI provider — please try again."
            continue

        if not raw.strip():
            failure_message = "AI is currently unavailable, so I can't answer that right now."
            continue

        try:
            parsed = parse_chat_reply(raw)
            break
        except Exception as e:  # noqa: BLE001
            logger.info("Chat reply failed to parse (attempt %d): %s", attempt + 1, e)
            failure_message = extractor.text.strip() or failure_message

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


def _run_reanalysis(symbol: str) -> tuple[str, bool]:
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
        )

    if isinstance(result, UncertaintyResponse):
        return f"I tried to re-run the analysis for {symbol}, but {result.summary}", False

    text = (
        f"I re-ran the analysis for {symbol}: trend is now {result.trend} "
        f"({result.confidence:.0%} confidence). {result.summary}"
    )
    # analyze_symbol() runs with advisory=True by default, so a fresh
    # reanalysis normally carries a trade_plan (entry/stop/targets) —
    # the most actionable part of "the full read" the trader asked for.
    # Surface it instead of silently dropping it.
    if result.trade_plan is not None:
        text += " " + _format_trade_plan(result.trade_plan)
    return text, True


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

_DESTRUCTIVE_ACTIONS = {"delete_alert", "remove_from_watchlist", "delete_watchlist"}

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


def _calculate(db, parsed) -> tuple[str, bool]:
    """Run a validated deterministic calculation and explain its result."""
    del db  # The calculator is read-only and does not need a database session.
    request = parsed.action_calculation
    if request is None:
        return "Tell me the values and calculation you want me to run.", False
    result = default_registry.execute(
        ToolRequest(tool_name="calculate", arguments=request.model_dump())
    )
    if not result.ok:
        return f"I couldn't calculate that safely: {result.error}", False
    values = ", ".join(f"{key}={value}" for key, value in result.data["values"].items())
    formula = result.data["formulas"][0]
    source_time = result.source_timestamp or "unknown time"
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
    "get_risk_dashboard",
    "get_trade_journal",
    "get_application_help",
    "get_alerts",
    "get_sector_data",
    "get_trend",
    "get_confluence",
    "get_relative_strength",
    "get_tape_state",
    "get_session_stats",
    "get_calendar",
    "import_csv",
}


def _run_market_tool(db, parsed, *, trace: list[dict] | None = None) -> tuple[str, bool]:
    """Execute one read-only grounded market-data tool selected by Chat."""
    del db
    arguments = parsed.action_tool_arguments or {}
    result = default_registry.execute(
        ToolRequest(tool_name=parsed.action, arguments=arguments)
    )
    if not result.ok:
        if trace is not None:
            trace.append({"tool": parsed.action, "ok": False, "provider": result.provider, "error": result.error, "fallback": result.fallback})
        return f"I couldn't retrieve that safely: {result.error}", False
    freshness = (
        f"{result.freshness_seconds:.1f}s old"
        if result.freshness_seconds is not None
        else "freshness unavailable"
    )
    if trace is not None:
        trace.append({
            "tool": parsed.action,
            "ok": True,
            "provider": result.provider,
            "freshness_seconds": result.freshness_seconds,
            "source_timestamp": result.source_timestamp,
            "session": result.session,
            "timeframe": result.timeframe,
            "fallback": result.fallback,
            "warnings": result.warnings,
        })
    return (
        f"Verified {parsed.action} result from {result.provider} ({freshness}, "
        f"session {result.session}, timeframe {result.timeframe or 'not specified'}): "
        f"{result.data}",
        True,
    )


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


def _run_action(db, parsed, *, trace: list[dict] | None = None) -> tuple[str, bool, list[str]]:
    """Execute one action tool. Never raises — a failure degrades to a
    plain reply with grounded=False, same contract as _run_reanalysis.

    Most handlers return ``(text, grounded)``; run_screen returns a
    3-tuple with the tickers it surfaced (see its docstring) — normalized
    to ``(text, grounded, screened)`` here either way.
    """
    handler = _ACTION_HANDLERS.get(parsed.action)
    if parsed.action in _MARKET_TOOL_ACTIONS:
        return _run_market_tool(db, parsed, trace=trace) + ([],)
    if handler is None:  # pragma: no cover — action is a closed Literal
        return "I couldn't do that — please try again.", False, []
    try:
        result = handler(db, parsed)
    except Exception as e:  # noqa: BLE001 — a tool call must never crash the turn
        logger.warning("chat action %s failed: %s", parsed.action, e)
        if trace is not None:
            trace.append({"tool": parsed.action, "ok": False, "provider": "MarketLens", "error": str(e), "fallback": False})
        return "Something went wrong doing that — please try again.", False, []
    if parsed.action in _BASELINE_MUTATING_ACTIONS:
        from backend.ai.market_baseline import invalidate_cache

        invalidate_cache()
    if len(result) == 3:
        if trace is not None:
            trace.append({"tool": parsed.action, "ok": True, "provider": "MarketLens", "fallback": False})
        return result
    text, grounded = result
    if trace is not None:
        trace.append({"tool": parsed.action, "ok": grounded, "provider": "MarketLens", "fallback": False})
    return text, grounded, []
