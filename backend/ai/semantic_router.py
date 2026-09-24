"""High-confidence semantic routing for common AI Hub requests.

The chat model remains the flexible planner for open-ended questions, but
common user-owned scopes should not depend on a model guessing a legacy flat
action.  This module translates natural-language variants into a small,
typed intent plan before tool execution.  The plan is deliberately about
meaning and scope; the target tool still validates and produces the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

WatchlistConcern = Literal[
    "weak",
    "strong",
    "deteriorating",
    "underperforming",
    "all",
]


@dataclass(frozen=True)
class SemanticRoute:
    """A canonical, server-consumable interpretation of user language."""

    action: str
    arguments: dict[str, object] = field(default_factory=dict)
    action_query: str | None = None
    confidence: Literal["high", "medium"] = "high"


_WATCHLIST_SCOPE = re.compile(
    r"\b(?:my|our|the)\s+(?:watchlist|names?|stocks?|tickers?|holdings?|positions?)\b"
    r"|\b(?:laggards?|leaders?)\s+(?:in|from|on)\s+(?:my|our)\b",
    re.I,
)
_NAMED_WATCHLIST_SCOPE = re.compile(
    r"\b(?:in|from|on)\s+(?:(?:my|our|the)\s+)?[\"“']?"
    r"(?P<name>(?!(?:my|our|the)\s+)[A-Za-z][A-Za-z0-9 &'._-]{0,79}?)"
    r"[\"”']?\s+watchlists?\b",
    re.I,
)
_WEAKNESS = re.compile(
    r"\b(?:weak(?:est|er)?|lagg(?:ard|ards)?|underperform(?:ing|er)?|"
    r"deteriorat(?:e|ing|ion)|bearish|losers?|worr(?:y|ied))\b",
    re.I,
)
_STRENGTH = re.compile(
    r"\b(?:strong(?:est|er)?|leader(?:s)?|outperform(?:ing|er)?|"
    r"bullish|winner(?:s)?)\b",
    re.I,
)
_DETERIORATION = re.compile(r"\b(?:deteriorat\w*|breaking down|slipping|rolling over)\b", re.I)
_UNDERPERFORMANCE = re.compile(r"\b(?:underperform\w*|lagg\w*|relative weakness|relative underperformance)\b", re.I)
_WATCHLIST_OVERVIEW = re.compile(
    r"\b(?:how(?:'s| is)\s+(?:my|our)\s+watchlist|"
    r"(?:rank|review|analy[sz]e|summari[sz]e|overview|status)\b.*\b(?:my|our)\s+watchlist)\b",
    re.I,
)
_MOVE = re.compile(r"\b(?:why did|why is|what caused|explain)\b.*\b(?:move|drop|surge|rally|fall|rise|down|up)\b", re.I)
_CHANGE = re.compile(r"\b(?:what changed|what has changed|changed since|since yesterday|since my last visit)\b", re.I)
_OPTIONS = re.compile(r"\b(?:options?|calls?|puts?|option chain|implied volatility|open interest|put[/-]?call)\b", re.I)
_RISK = re.compile(
    r"\b(?:portfolio risk|position risk|exposure|drawdown|risk dashboard|concentration|"
    r"my positions|my portfolio|my book|portfolio health)\b",
    re.I,
)
_JOURNAL = re.compile(r"\b(?:trade journal|journal entries?|trading journal|mistakes? review)\b", re.I)
_ALERTS = re.compile(r"\b(?:my alerts?|active alerts?|alert rules?|notifications?)\b", re.I)
_TIMELINE = re.compile(r"\b(?:event timeline|market timeline|timeline|what happened (?:before|after|around))\b", re.I)
_NEWS = re.compile(r"\b(?:news|headlines?|catalysts?|filings?|earnings?)\b", re.I)
_FUNDAMENTALS = re.compile(r"\b(?:fundamentals?|valuation|p/?e\b|eps|revenue|margins?|balance sheet|cash flow)\b", re.I)
_TREND = re.compile(r"\b(?:trend|trending|direction)\b", re.I)
_SYMBOL_OVERVIEW = re.compile(
    r"(?:\b(?:how(?:'s| is| did)|what(?:'s| is)|why(?:'s| is)|tell me about|"
    r"what do you think of|give me (?:a )?read on)\b.*\b(?:look(?:ing)?|"
    r"positioned|shape|setup|doing|performing|moving|healthy|weak|strong|"
    r"today|now|well|bad)\b|"
    r"\b(?:tell me about|what do you think of|give me (?:a )?read on)\b)",
    re.I,
)
_FOLLOWUP_SYMBOL_OVERVIEW = re.compile(r"\bwhat\s+about\b", re.I)
_KNOWN_INDICATOR = re.compile(r"\b(?P<indicator>sma|ema|rsi|change(?:\s+percent)?)\b", re.I)
_CONFLUENCE = re.compile(r"\b(?:confluence|multi[- ]?timeframe|mtf|alignment)\b", re.I)
_RELATIVE_STRENGTH = re.compile(r"\b(?:relative strength|outperform(?:ing|er)?|underperform(?:ing|er)?)\b", re.I)
_COMPARISON = re.compile(
    r"\b(?:compare|comparison|rank|ranking|strongest|weakest|best performing|worst performing)\b",
    re.I,
)
_MARKET_OVERVIEW = re.compile(
    r"\b(?:what(?:'s| is) the market|how(?:'s| is) the market|"
    r"market doing|market today|market overall|market conditions|"
    r"market backdrop|how are the markets|how are stocks doing|"
    r"what(?:'s| is) happening in the market|market behaving|"
    r"market looking)\b",
    re.I,
)
_WATCHLIST_TIMEFRAME = re.compile(
    r"\b(?:daily|weekly|1d|1wk)\s*(?:timeframe|time frame|view|data)?\b",
    re.I,
)


def _watchlist_scope_followup(user_content: str, planner_state: dict | None) -> SemanticRoute | None:
    """Reuse the last verified watchlist scope for a timeframe follow-up."""
    if not planner_state or not _WATCHLIST_TIMEFRAME.search(user_content):
        return None
    scope = planner_state.get("watchlist_scope")
    if not isinstance(scope, dict):
        return None
    concern = str(scope.get("concern") or "all")
    if concern not in {"weak", "strong", "deteriorating", "underperforming", "all"}:
        concern = "all"
    timeframe = re.search(r"\b(weekly|1wk)\b", user_content, re.I)
    arguments: dict[str, object] = {
        "concern": concern,
        "timeframe": "1wk" if timeframe else "1d",
    }
    if not scope.get("aggregate") and isinstance(scope.get("name"), str) and scope["name"].strip():
        arguments["name"] = scope["name"].strip()
    return SemanticRoute(action="get_watchlist_intelligence", arguments=arguments)


def _watchlist_route(user_content: str) -> SemanticRoute | None:
    """Recognize user-owned ranking language without requiring exact verbs."""
    named_scope = _NAMED_WATCHLIST_SCOPE.search(user_content)
    if not _WATCHLIST_SCOPE.search(user_content) and named_scope is None:
        return None

    if _DETERIORATION.search(user_content):
        concern: WatchlistConcern = "deteriorating"
    elif _UNDERPERFORMANCE.search(user_content):
        concern = "underperforming"
    elif _WEAKNESS.search(user_content):
        concern = "weak"
    elif _STRENGTH.search(user_content):
        concern = "strong"
    elif _WATCHLIST_OVERVIEW.search(user_content):
        concern = "all"
    else:
        return None

    arguments: dict[str, object] = {"concern": concern}
    if named_scope:
        arguments["name"] = named_scope.group("name").strip()
    timeframe = re.search(r"\b(daily|daily timeframe|1d|weekly|weekly timeframe|1wk)\b", user_content, re.I)
    if timeframe:
        value = timeframe.group(1).lower().replace(" ", "")
        arguments["timeframe"] = "1wk" if value in {"weekly", "weeklytimeframe", "1wk"} else "1d"

    return SemanticRoute(
        action="get_watchlist_intelligence",
        arguments=arguments,
    )


def _single_symbol_route(
    action: str,
    focus_symbols: list[str],
    *,
    arguments: dict[str, object] | None = None,
) -> SemanticRoute | None:
    if len(focus_symbols) != 1:
        return None
    return SemanticRoute(
        action=action,
        arguments={"symbol": focus_symbols[0], **(arguments or {})},
    )


def route_semantic_intent(
    user_content: str,
    *,
    focus_symbols: list[str] | None = None,
    planner_state: dict | None = None,
) -> SemanticRoute | None:
    """Return a high-confidence canonical route, or ``None``.

    ``focus_symbols`` and ``planner_state`` are accepted now so future route
    families can resolve conversational scope without changing this API.
    They are intentionally not used to guess a watchlist today.
    """
    text = user_content.strip()
    symbols = [str(symbol).upper() for symbol in (focus_symbols or [])]

    route = _watchlist_route(text)
    if route is not None:
        return route
    if not symbols:
        route = _watchlist_scope_followup(text, planner_state)
        if route is not None:
            return route
    if not symbols and _MARKET_OVERVIEW.search(text):
        return SemanticRoute(action="get_market_context")
    if _CHANGE.search(text):
        if len(symbols) == 1:
            reference = "last_visit" if "last visit" in text.lower() else "yesterday" if "yesterday" in text.lower() else "previous_close"
            return _single_symbol_route("what_changed", symbols, arguments={"reference": reference})
        return None
    if _MOVE.search(text):
        return _single_symbol_route("why_did_it_move", symbols)
    if _OPTIONS.search(text):
        return _single_symbol_route("get_options_snapshot", symbols)
    if _RISK.search(text):
        return SemanticRoute(action="get_risk_dashboard")
    if _JOURNAL.search(text):
        return SemanticRoute(
            action="get_trade_journal",
            arguments={"symbol": symbols[0]} if len(symbols) == 1 else {},
        )
    if _ALERTS.search(text):
        return SemanticRoute(
            action="get_alerts",
            arguments={"symbol": symbols[0]} if len(symbols) == 1 else {},
        )
    if _TIMELINE.search(text):
        return _single_symbol_route("market_event_timeline", symbols)
    if _NEWS.search(text):
        return _single_symbol_route("get_news", symbols)
    if _FUNDAMENTALS.search(text):
        return _single_symbol_route("get_fundamentals", symbols)
    if _KNOWN_INDICATOR.search(text):
        indicator_match = _KNOWN_INDICATOR.search(text)
        indicator = indicator_match.group("indicator").lower().replace(" ", "_") if indicator_match else ""
        if indicator == "change":
            indicator = "change_percent"
        return _single_symbol_route(
            "get_indicator",
            symbols,
            arguments={"indicator": indicator, "timeframe": (planner_state or {}).get("timeframe") or "1d"},
        )
    if _CONFLUENCE.search(text):
        return _single_symbol_route("get_confluence", symbols)
    if _RELATIVE_STRENGTH.search(text):
        return _single_symbol_route("get_relative_strength", symbols)
    if (_SYMBOL_OVERVIEW.search(text) or _FOLLOWUP_SYMBOL_OVERVIEW.search(text)) and len(symbols) == 1 and re.search(
        rf"\b{re.escape(symbols[0])}\b", text, re.I
    ):
        return _single_symbol_route("get_trend", symbols)
    if _TREND.search(text):
        return _single_symbol_route("get_trend", symbols)
    if _COMPARISON.search(text) and len(symbols) >= 2:
        lowered = text.lower()
        metric = "return_percent"
        if "volatility" in lowered:
            metric = "volatility_percent"
        elif "volume" in lowered:
            metric = "volume"
        elif "price" in lowered:
            metric = "price"
        elif any(word in lowered for word in ("today", "daily", "change")):
            metric = "change_percent"
        direction = "asc" if any(
            word in lowered for word in ("weakest", "worst", "lowest", "smallest")
        ) else "desc"
        return SemanticRoute(
            action="compare_symbols",
            arguments={
                "symbols": symbols,
                "metric": metric,
                "direction": direction,
            },
        )
    return None


__all__ = ["SemanticRoute", "WatchlistConcern", "route_semantic_intent"]
