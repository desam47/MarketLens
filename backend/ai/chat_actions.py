"""Chat actions and tools: alert/watchlist handlers, market tools and multi-step turns.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from backend.ai.analyze import analyze_symbol
from backend.ai.calculator import CalculationRequest
from backend.ai.chat_intents import (
    _AFFIRM_INTENT,
    _CALCULATION_HINT,
    _CONFIRM_DELETE_WATCHLIST_RE,
    _CONFIRM_REMOVE_FROM_WATCHLIST_RE,
    _DATE_RE,
    _DELETE_WATCHLIST_FALLBACK,
    _MULTI_STEP_HINT,
    _NAMED_WATCHLIST_LEADING_RE,
    _NAMED_WATCHLIST_RE,
    _REUSE_MEMORY_HINT,
    _SHARES_RE,
    _fallback_calculation,
    _parse_date_from_text,
)
from backend.ai.chat_model import (
    _CONTINUATION_MAX_TOKENS,
    _ai_setting,
    _chat_route_model,
    _complete_and_parse,
    _estimate_tokens,
    _planning_call_limit,
    _prompt_token_budget,
    _tool_call_limit,
    _turn_budget_seconds,
    _turn_token_budget,
    _turn_tokens_used,
)
from backend.ai.chat_observability import (
    sanitize_arguments,
    sanitize_error_message,
    sanitize_warnings,
)
from backend.ai.chat_replies import (
    _BROWSER_LOCAL_ACTIONS,
    _browser_safe_reply_data,
    _chart_name,
    _format_browser_local_reply,
    _format_calculation_reply,
    _format_change_reply,
    _format_generic_market_reply,
    _format_indicator_reply,
    _format_price_statistics_reply,
    _format_trade_plan,
    _format_watchlist_intelligence,
    _is_regular_market_closed,
    _join_and,
)
from backend.ai.prompt import (
    CHAT_CONTINUATION_SYSTEM_PROMPT,
    UncertaintyResponse,
    build_chat_prompt,
)

# Chat runs its sync generator helpers on loop-less worker threads
# (ThreadPoolExecutor / asyncio.to_thread), so the async AI calls are
# bridged with run_sync/stream_sync rather than awaited.
from backend.ai.sync_bridge import run_sync
from backend.ai.tool_registry import (
    ToolRequest,
    default_registry,
    normalize_session,
    normalize_timeframe,
)
from backend.engines.market_calendar import daily_data_is_current
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)


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


def _resolve_named_watchlist_symbols(db, user_content: str) -> list[str]:
    resolved = _resolve_named_watchlist(db, user_content)
    return resolved[1] if resolved else []


def _compute_historical_pnl(
    user_content: str, symbol: str
) -> tuple[CalculationRequest, dict[str, str]] | None:
    """Resolve entry/exit closing prices from bars for a 'bought N shares on DATE' query.

    Returns ``(CalculationRequest, context)`` so the turn routes through the
    verified calculator (giving the answer a real evidence trace), or None when
    the shares/dates can't be parsed — the model planner then handles it.
    """
    # Extract share count
    shares_match = _SHARES_RE.search(user_content)
    if not shares_match:
        return None
    raw_shares = next((g for g in shares_match.groups() if g), None)
    if not raw_shares:
        return None
    shares = float(raw_shares.replace(",", ""))

    # Extract dates — first date = entry, second = exit (default: today)
    date_strings = _DATE_RE.findall(user_content)
    if not date_strings:
        return None
    entry_date = _parse_date_from_text(date_strings[0])
    if entry_date is None:
        return None
    exit_date = (
        _parse_date_from_text(date_strings[1]) if len(date_strings) > 1 else None
    ) or date.today()

    # Fetch daily bars (2y covers any query in the last 2 years)
    try:
        from backend.ai.market_tools import BarsRequest, get_bars_tool

        payload = get_bars_tool(BarsRequest(symbol=symbol, timeframe="1d", range="2y", limit=2_000))
        payload_dict = payload.model_dump(mode="json")
        bars = payload_dict.get("bars") or []
        provider = str(payload_dict.get("provider") or "MarketLens")
    except Exception:
        return None

    if not bars:
        return None

    # Find the nearest bar on or after each target date
    def nearest_bar(target: date) -> dict | None:
        for bar in bars:
            ts = str(bar.get("timestamp") or "")
            try:
                bar_date = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
            except (ValueError, TypeError):
                continue
            if bar_date >= target:
                return bar
        return None

    entry_bar = nearest_bar(entry_date)
    exit_bar = nearest_bar(exit_date) or bars[-1]  # fall back to latest bar

    if entry_bar is None:
        return None

    entry_price = float(entry_bar["close"])
    exit_price = float(exit_bar["close"])
    if entry_price <= 0 or exit_price <= 0:
        return None

    def bar_date_str(bar: dict) -> str:
        ts = str(bar.get("timestamp") or "")
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%b %d, %Y")
        except (ValueError, TypeError):
            return ts[:10]

    return CalculationRequest(
        calculation="position_pnl",
        entry_price=entry_price,
        exit_price=exit_price,
        shares=shares,
    ), {
        "symbol": symbol,
        "entry_date": bar_date_str(entry_bar),
        "exit_date": bar_date_str(exit_bar),
        "provider": provider,
    }


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


def _rollback_quietly(db) -> None:
    """Roll back a failed flush so the turn's session can still persist.

    A caught exception from a handler or tool can leave the shared session
    in a failed transaction; without a rollback every later write on it
    (planner state, the assistant row) raises ``PendingRollbackError``.
    Earlier steps already committed their own work, so only the failed
    step's partial changes are discarded.
    """
    if db is None:
        return
    try:
        db.rollback()
    except Exception as e:  # noqa: BLE001
        logger.warning("chat session rollback failed: %s", e)


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
                problem = _destructive_target_problem(db, parsed)
                if problem is not None:
                    # Nothing (or no single thing) to act on: answer instead
                    # of asking the trader to approve an unknown target.
                    if planner_state is not None:
                        planner_state["pending_confirmation"] = None
                    return problem, False, []
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


def _trace_is_action_only(trace: list[dict]) -> bool:
    """True when every substantive entry in the trace is a completed CRUD
    action step — no market data fetches, no AI text generations.  The
    grounded fail-safe (``grounded and not turn.unavailable``) must not
    fire for these turns: the acted-upon ticker may not be in
    ``_known_symbols()`` yet (e.g. just being added to a watchlist), but
    a successful action is not a data-quality problem.
    """
    crud_tools = {
        "create_alert",
        "modify_alert",
        "delete_alert",
        "add_to_watchlist",
        "remove_from_watchlist",
        "create_watchlist",
        "delete_watchlist",
    }
    substantive = [
        e
        for e in trace
        if e.get("kind") not in {"context", "regeneration"}
        and e.get("tool") != "chat_market_baseline"
    ]
    return bool(substantive) and all(
        (e.get("kind") == "step" and e.get("status") == "completed")
        or (e.get("tool") in crud_tools and e.get("ok") is True)
        for e in substantive
    )


def _asks_for_input(text: str) -> bool:
    """An ungrounded step reply that ends in a question is waiting on the
    trader ("which watchlist?"), not a failure. Only the trace label uses
    this; the chain already stops on any ungrounded step."""
    return text.rstrip().endswith("?")


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
                    else "needs_input"
                    if _asks_for_input(text)
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
        # A step that failed or asked a question ("which watchlist?") must
        # be answered first: later steps often depend on it ("create X and
        # add Y to it").
        or not grounded
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
                    "status": (
                        "completed"
                        if step_grounded
                        else "needs_input"
                        if _asks_for_input(step_text)
                        else "failed"
                    ),
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
        if not _action_was_executed(next_parsed) or not step_grounded:
            break  # a pending confirmation, a failure, or a question — stop here

    return " ".join(texts), all_grounded, list(dict.fromkeys(all_screened))


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


def _destructive_target_problem(db, parsed) -> str | None:
    """Resolve a destructive action's target before asking to confirm it.

    Returns a reply to send instead of the confirmation question when there
    is nothing, or no single thing, to act on. Otherwise pins the resolved
    target on ``parsed`` so the stored pending confirmation replays exactly
    what the question named, even if another watchlist is created or
    renamed before the trader says yes.
    """
    if parsed.action == "delete_alert":
        from backend.repositories.alert_repository import AlertRepository

        if parsed.action_target_id is None:
            return "Which alert should I delete? Tell me the ticker or the alert's name."
        if AlertRepository(db).get_by_id(parsed.action_target_id) is None:
            return "I couldn't find that alert — it may already be deleted."
    elif parsed.action == "delete_watchlist":
        wl, ambiguous, candidates = _resolve_watchlist(db, parsed.action_watchlist)
        if ambiguous:
            names = ", ".join(c.name for c in candidates)
            return f"You have more than one watchlist ({names}) — which one should I delete?"
        if wl is None:
            if parsed.action_watchlist:
                return f'I couldn\'t find a watchlist called "{parsed.action_watchlist}".'
            return "You don't have any watchlists to delete."
        parsed.action_watchlist = wl.name
        parsed.action_target_id = wl.id
    return None


def _describe_alert(alert) -> str:
    condition = (alert.condition_type or "").replace("_", " ")
    return f'"{alert.name}" ({alert.symbol} {condition} {alert.parameter})'


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
        from backend.repositories.alert_repository import AlertRepository

        alert = (
            AlertRepository(db).get_by_id(parsed.action_target_id)
            if parsed.action_target_id is not None
            else None
        )
        if alert is not None:
            return f"Delete the alert {_describe_alert(alert)}? Say yes to confirm."
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


def _find_watchlist_by_name(repo, name: str):
    """An active watchlist by exact name, else by case-insensitive name.

    Names reach here from the trader's own wording ("my tech watchlist"),
    so an exact-only match turned a case difference into a second list.
    """
    wl = repo.get_watchlist_by_name(name)
    if wl is not None:
        return wl
    lowered = name.strip().lower()
    return next((w for w in repo.get_watchlists(active_only=True) if w.name.lower() == lowered), None)


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
        return _find_watchlist_by_name(repo, name), False, []

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
        return f"You have more than one watchlist ({names}) — which one should I add it to?", False
    repo = WatchlistRepository(db)
    if wl is None:
        existing = [w.name for w in repo.get_watchlists(active_only=True)]
        if parsed.action_watchlist and existing:
            # A name that matches no list is far more often a typo than a
            # request for a new one; creating it silently left a stray list.
            return _unknown_watchlist_reply(parsed.action_watchlist, existing, symbol), False
        wl = repo.create_watchlist(parsed.action_watchlist or "Watchlist")
    _, is_new, did_reenable = repo.add_symbol_to_watchlist(wl.id, symbol)
    if is_new or did_reenable:
        _kickoff_backfill(symbol)
    return f"Done — added {symbol} to {wl.name}.", True


def _unknown_watchlist_reply(name: str, existing: list[str], symbol: str) -> str:
    from difflib import get_close_matches

    close = get_close_matches(name.lower(), [n.lower() for n in existing], n=1, cutoff=0.6)
    if close:
        match = next(n for n in existing if n.lower() == close[0])
        return f'I couldn\'t find a watchlist called "{name}". Did you mean "{match}"?'
    return (
        f'I couldn\'t find a watchlist called "{name}". Your watchlists: {", ".join(existing)}. '
        f'To start a new one, say "create a watchlist called {name} with {symbol}".'
    )


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
            False,
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
    existing = _find_watchlist_by_name(repo, name)
    if existing is not None:
        return f'A watchlist called "{existing.name}" already exists.', False
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

    repo = WatchlistRepository(db)
    if parsed.action_target_id is not None:
        # Pinned by _destructive_target_problem when the confirmation was
        # asked: delete exactly the list the question named.
        wl = repo.get_watchlist(parsed.action_target_id)
        if wl is None:
            return "That watchlist no longer exists.", False
    else:
        wl, ambiguous, candidates = _resolve_watchlist(db, parsed.action_watchlist)
        if ambiguous:
            names = ", ".join(c.name for c in candidates)
            return f"You have more than one watchlist ({names}) — which one should I delete?", False
        if wl is None:
            return "I couldn't find that watchlist.", False
    name = wl.name
    repo.delete_watchlist(wl.id)
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
    if not _ai_setting("backtest_tool_enabled"):
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
            False,
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
        _rollback_quietly(db)
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
    from backend.api.scanner.router import (
        _build_filter,
        _FilterRequest,
        _scoped_cache,
        _split_earnings_exclusion,
        _without_upcoming_earnings,
    )
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
        safe_error = sanitize_error_message(
            result.error,
            failure_kind=result.failure_kind or "calculation_error",
            default="I couldn't complete that calculation safely.",
        )
        if trace is not None:
            trace.append({
                "tool": "calculate",
                "kind": "calculation",
                "ok": False,
                "provider": result.provider,
                "error": safe_error,
                "failure_kind": "calculation_error",
                "duration_ms": result.duration_ms,
                "arguments": sanitize_arguments(request.model_dump(mode="json")),
                "fallback": result.fallback,
            })
        return f"I couldn't calculate that safely: {safe_error}", False
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
    return _format_calculation_reply(
        request,
        result.data.get("values") or {},
        result.data.get("assumptions") or [],
        context=getattr(parsed, "action_pnl_context", None) or {},
        provider=result.provider,
    ), True


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
    "get_price_statistics",
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
        "compare_symbols",
        "signal_explanation",
        "historical_similarity",
    }:
        try:
            request_scope["timeframe"] = normalize_timeframe(
                str((planner_state or {}).get("timeframe") or "1d")
            )
        except ValueError:
            request_scope["timeframe"] = "1d"
    elif parsed.action == "get_price_statistics":
        request_scope["timeframe"] = "1d"  # always daily closes
    result = default_registry.execute(
        ToolRequest(
            tool_name=parsed.action,
            arguments=arguments,
            confirmed=bool(parsed.action_confirmed),
            **request_scope,
        )
    )
    if not result.ok:
        safe_error = sanitize_error_message(
            result.error,
            failure_kind=result.failure_kind or "tool_error",
        )
        if trace is not None:
            trace.append({
                "tool": parsed.action,
                "ok": False,
                "provider": result.provider,
                "error": safe_error,
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
                f"{safe_error}",
                False,
            )
        if parsed.action == "get_risk_dashboard" and parsed.action_query in {"portfolio_change", "portfolio_weakness"}:
            label = "changes" if parsed.action_query == "portfolio_change" else "weakness ranking"
            return f"Portfolio {label} is unavailable from {result.provider or 'MarketLens'}: {safe_error}", False
        return f"I couldn't retrieve that safely: {safe_error}", False
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
            "warnings": sanitize_warnings(result.warnings),
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
        reply = f"The market is in a {regime} regime with {volatility} volatility"
        if isinstance(confidence, (int, float)):
            reply += f" ({float(confidence):.0%} confidence)"
        reply += "."
        readings = []
        if isinstance(momentum, (int, float)):
            readings.append(f"momentum is {float(momentum):+.2f}")
        if isinstance(trend_strength, (int, float)):
            readings.append(f"trend strength is {float(trend_strength):.2f}")
        if readings:
            text = _join_and(readings)
            reply += f" {text[:1].upper()}{text[1:]}."
        return reply, True
    if parsed.action == "get_trend":
        symbol = str(result.data.get("symbol") or arguments.get("symbol") or "the symbol").upper()
        direction = str(result.data.get("direction") or "unknown").replace("_", " ")
        strength = str(result.data.get("strength") or "unknown").replace("_", " ")
        classification = str(result.data.get("classification") or "").replace("_", " ")
        if direction == "unknown" and strength == "unknown":
            return f"I don't have enough verified trend data for {symbol}.", False
        chart = _chart_name(result.timeframe or arguments.get("timeframe") or "1d")
        # The data's own words (sideways/bullish/weak) are kept: the answer
        # verifier checks direction words against these labels.
        if direction == "sideways":
            movement = "moving sideways"
        elif direction in {"up", "down"}:
            movement = f"trending {direction}"
        else:
            movement = f"in {'an' if direction[:1] in 'aeiou' else 'a'} {direction} trend"
        reply = f"On the {chart} chart, {symbol} is {movement}"
        if strength != "unknown":
            reply += f" with {strength} strength"
        reply += "."
        if classification:
            reply += f" Overall, it reads as {classification}."
        return reply, True
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
    if parsed.action == "get_price_statistics":
        return _format_price_statistics_reply(result.data), True
    if parsed.action == "compare_symbols" and isinstance(result.data.get("rankings"), list):
        # Daily/weekly rankings describe completed bars. Once the regular
        # session has closed, those bars remain the correct comparison
        # reference and the cache age alone must not turn a usable end-of-day
        # answer into a failure. Intraday comparisons retain the strict
        # 15-minute limit.
        comparison_timeframe = str(result.timeframe or arguments.get("timeframe") or "").lower()
        completed_session_comparison = (
            comparison_timeframe in {"1d", "1wk"} and _is_regular_market_closed()
        ) or (
            # During the session, daily bars through the latest completed
            # close are still the right reference.
            comparison_timeframe == "1d" and daily_data_is_current(result.source_timestamp)
        )
        if (
            isinstance(result.freshness_seconds, (int, float))
            and result.freshness_seconds > 900
            and not completed_session_comparison
        ):
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
            if not symbol or not isinstance(value, (int, float)):
                continue
            if metric.endswith("_percent"):
                shown = f"{float(value):.2f}%"
            elif metric == "price":
                shown = f"${float(value):.2f}"
            elif metric == "volume":
                shown = f"{float(value):,.0f}"
            else:
                shown = f"{float(value):.2f}"
            rows.append((symbol, shown))
        if rows:
            first, first_value = rows[0]
            if len(rows) == 1:
                reply = f"{first}'s {label} is {first_value}."
            elif len(rows) == 2:
                reply = f"Ranked by {label}, {first} comes first at {first_value}, ahead of {rows[1][0]} at {rows[1][1]}."
            else:
                rest = _join_and([f"{symbol} ({shown})" for symbol, shown in rows[1:]])
                reply = f"Ranked by {label}, {first} comes first at {first_value}, followed by {rest}."
            if comparison_timeframe in {"1d", "1wk"} and _is_regular_market_closed():
                reply += " These figures are as of the last market close."
            elif completed_session_comparison:
                # Current daily bars mid-session: the session in progress
                # isn't in them, and the market is not closed.
                reply += " These use daily closes through the last completed session, so the session in progress isn't included."
            return reply, True
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
        _rollback_quietly(db)
        safe_error = sanitize_error_message(
            e,
            failure_kind="action_exception",
            default="Something went wrong doing that — please try again.",
        )
        if trace is not None:
            trace.append({
                "tool": parsed.action,
                "ok": False,
                "provider": "MarketLens",
                "error": safe_error,
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
