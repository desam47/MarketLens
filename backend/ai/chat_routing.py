"""Deterministic Chat routing: replies the server gives without asking the model.
"""

from __future__ import annotations

import re

from backend.ai.calculator import CalculationRequest
from backend.ai.chat_actions import (
    _compute_historical_pnl,
    _run_turn_actions,
)
from backend.ai.chat_intents import (
    _AFFIRM_INTENT,
    _ALERTS_TOOL_INTENT,
    _ANOMALY_INTENT,
    _ASSUMPTION_INTENT,
    _ASSUMPTION_SAVE_INTENT,
    _CALCULATION_HINT,
    _COMPARISON_INTENT,
    _COUNTERARGUMENT_INTENT,
    _HISTORICAL_PNL_INTENT,
    _HISTORICAL_TOOL_INTENT,
    _JOURNAL_TOOL_INTENT,
    _MULTI_STEP_HINT,
    _OPTIONS_TOOL_INTENT,
    _REUSE_MEMORY_HINT,
    _RISK_TOOL_INTENT,
    _SAVED_SCANS_INTENT,
    _SCANNER_TOOL_INTENT,
    _SCENARIO_INTENT,
    _SENSITIVITY_INTENT,
    _SIGNAL_EXPLANATION_INTENT,
    _SIGNAL_HISTORY_INTENT,
    _SIMILARITY_INTENT,
    _TIMELINE_INTENT,
    _WATCHLIST_ADD_INTENT,
    _WATCHLIST_CREATE_INTENT,
    _WATCHLIST_DELETE_INTENT,
    _WATCHLIST_REMOVE_FROM_INTENT,
    _WHAT_CHANGED_INTENT,
    _WHY_MOVE_INTENT,
    _calculation_followup,
    _extract_memory_value,
    _extract_watchlist_name,
    _fallback_calculation,
    _numbers_from_text,
    _parse_assumption_records,
    _position_risk_calculation,
)
from backend.ai.price_metric_intent import parse_price_metric_intent
from backend.ai.prompt import (
    ChatReplyResponse,
)
from backend.ai.semantic_router import route_semantic_intent

# Chat runs its sync generator helpers on loop-less worker threads
# (ThreadPoolExecutor / asyncio.to_thread), so the async AI calls are
# bridged with run_sync/stream_sync rather than awaited.
from backend.utils.timezone import now_ny


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
    # Price statistics over daily closes ("TSLA max drawdown this year",
    # "AAPL volatility over 30 days", "correlation between AAPL and MSFT",
    # "NVDA return from Jan 5 to Jan 20"). Checked first: the semantic route
    # would answer a calendar window with today's change, and the calculation
    # hint would ask the trader for values the app can fetch itself.
    if not any(
        pattern.search(user_content)
        for pattern in (
            _MULTI_STEP_HINT,
            _WHY_MOVE_INTENT,
            _ANOMALY_INTENT,
            _SCENARIO_INTENT,
            _HISTORICAL_PNL_INTENT,
            _WATCHLIST_ADD_INTENT,
            _WATCHLIST_CREATE_INTENT,
            _WATCHLIST_REMOVE_FROM_INTENT,
            _WATCHLIST_DELETE_INTENT,
        )
    ):
        price_metric = parse_price_metric_intent(user_content, focus_symbols, now_ny().date())
        if isinstance(price_metric, str):
            return price_metric
        if (
            price_metric is not None
            and price_metric.metric == "return_percent"
            and price_metric.lookback_days is not None
            and route_semantic_intent(user_content, focus_symbols=focus_symbols, planner_state=planner_state)
            is not None
        ):
            # Trailing returns the semantic route already answers ("over the
            # last 5 days") keep that verified route.
            price_metric = None
        if price_metric is not None:
            return ChatReplyResponse(
                reply="Verified price statistics",
                grounded=True,
                action="get_price_statistics",
                action_symbol=price_metric.symbol,
                action_tool_arguments=price_metric.tool_arguments(),
            )

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
    if _HISTORICAL_PNL_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker did you buy?"
        resolved_pnl = _compute_historical_pnl(user_content, focus_symbols[0])
        if resolved_pnl is not None:
            pnl_request, pnl_context = resolved_pnl
            return ChatReplyResponse(
                reply="Verified position P&L",
                grounded=True,
                action="calculate",
                action_calculation=pnl_request,
                action_pnl_context=pnl_context,
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

    # Watchlist CRUD — checked last so read-only and intelligence routes take priority.
    # Multi-step messages ("create X and add Y to it") are left for the AI so it can
    # orchestrate the full chain; the deterministic path handles single-action turns only.
    if not _MULTI_STEP_HINT.search(user_content) and _WATCHLIST_REMOVE_FROM_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I remove from your watchlist?"
        return ChatReplyResponse(
            reply="Verified remove from watchlist",
            grounded=True,
            action="remove_from_watchlist",
            action_symbol=focus_symbols[0],
            action_watchlist=_extract_watchlist_name(user_content),
        )
    if not _MULTI_STEP_HINT.search(user_content) and _WATCHLIST_DELETE_INTENT.search(user_content):
        return ChatReplyResponse(
            reply="Verified delete watchlist",
            grounded=True,
            action="delete_watchlist",
            action_watchlist=_extract_watchlist_name(user_content),
        )
    if not _MULTI_STEP_HINT.search(user_content) and _WATCHLIST_ADD_INTENT.search(user_content):
        if len(focus_symbols) != 1:
            return "Which ticker should I add to your watchlist?"
        # "add RIVN to a new watchlist called Momentum" asks for the list to
        # be created with the ticker in it; add_to_watchlist no longer
        # creates a list from an unknown name.
        creates = bool(_WATCHLIST_CREATE_INTENT.search(user_content))
        return ChatReplyResponse(
            reply="Verified create watchlist" if creates else "Verified add to watchlist",
            grounded=True,
            action="create_watchlist" if creates else "add_to_watchlist",
            action_symbol=focus_symbols[0],
            action_watchlist=_extract_watchlist_name(user_content),
        )
    if not _MULTI_STEP_HINT.search(user_content) and _WATCHLIST_CREATE_INTENT.search(user_content):
        return ChatReplyResponse(
            reply="Verified create watchlist",
            grounded=True,
            action="create_watchlist",
            action_watchlist=_extract_watchlist_name(user_content),
        )

    return None


def _run_deterministic_shortcircuit(
    db,
    user_content: str,
    symbol_blocks: list[dict],
    unavailable: list[str],
    market_baseline: dict | None,
    transcript: list[tuple[str, str]],
    alert_context: dict | None,
    trace: list[dict] | None,
    planner_state: dict | None,
    preferences: dict | None = None,
    started_at: float | None = None,
) -> tuple[str, bool, list[str]] | None:
    """Run the deterministic short-circuit check before the AI call.

    Returns ``(text, grounded, screened)`` when a deterministic reply was
    built (both the plain-text case and the action case), or ``None`` when
    the caller should proceed to the AI.  Called from both
    ``_generate_reply`` and ``_generate_reply_streaming`` so the logic
    only lives in one place.
    """
    # A CRUD request can be valid even when the ticker has no current market
    # data. Keep explicitly resolved-but-unavailable symbols in the intent
    # planner for those requests only; generic market questions must still
    # reach the model's unavailable-data path instead of becoming a ticker
    # lookup merely because context construction failed.
    focus_symbols = [b["symbol"] for b in symbol_blocks]
    if any(
        pattern.search(user_content)
        for pattern in (
            _WATCHLIST_ADD_INTENT,
            _WATCHLIST_CREATE_INTENT,
            _WATCHLIST_REMOVE_FROM_INTENT,
            _WATCHLIST_DELETE_INTENT,
        )
    ):
        focus_symbols = list(dict.fromkeys([*focus_symbols, *unavailable]))
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
            started_at=started_at,
            planner_state=planner_state,
            preferences=preferences,
        )
    return None


def _confirm_pending_action(
    db,
    user_content: str,
    symbol_blocks: list[dict],
    unavailable: list[str],
    market_baseline: dict | None,
    transcript: list[tuple[str, str]],
    alert_context: dict | None,
    trace: list[dict] | None,
    planner_state: dict | None,
    preferences: dict | None = None,
) -> tuple[str, bool, list[str]] | None:
    """Execute the pending server-authored confirmation on a strict "yes".

    Checked before intent routing, the model, and the AI-off fallback, so
    confirming a destructive action never depends on a model call (with AI
    off it previously could not be confirmed at all). ``_finalize_parsed``
    still enforces the gate and replays the stored payload, not anything
    from this turn.
    """
    pending = (planner_state or {}).get("pending_confirmation")
    if not isinstance(pending, dict) or not pending.get("action") or not _AFFIRM_INTENT.match(user_content):
        return None
    parsed = ChatReplyResponse(
        reply="Confirmed",
        grounded=True,
        action=pending["action"],
        action_symbol=pending.get("symbol"),
        action_watchlist=pending.get("watchlist"),
        action_target_id=pending.get("target_id"),
        action_tool_arguments=pending.get("tool_arguments"),
        action_confirmed=True,
    )
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
