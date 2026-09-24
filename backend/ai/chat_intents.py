"""Regex intents and text parsing for Chat turns (no I/O).
"""

from __future__ import annotations

import re
from datetime import date

from backend.ai.calculator import CalculationRequest

# Chat runs its sync generator helpers on loop-less worker threads
# (ThreadPoolExecutor / asyncio.to_thread), so the async AI calls are
# bridged with run_sync/stream_sync rather than awaited.

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
# "bought N shares of TICKER on DATE, how much profit" — needs bars to resolve entry/exit price
_HISTORICAL_PNL_INTENT = re.compile(
    r"\b(?:bought?|purchased?|got in|entered?)\b.{1,60}\b(?:on\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{4})|in\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b"
    r"|\bhow\s+much\s+(?:profit|loss|gain|money|return|would\s+i\s+(?:make|have|get))\b.{0,80}\b(?:bought?|purchased?|shares?\s+(?:of\s+)?\w+|on\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{4}))\b",
    re.I,
)
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
# Watchlist CRUD intents — only add/create fire unconditionally; remove/delete go through the
# server confirmation gate in _finalize_parsed (same path as AI-originated destructive actions).
_WATCHLIST_ADD_INTENT = re.compile(r"\b(add|put)\b[^.!?]{0,60}\bwatch\s?list\b", re.I)
_WATCHLIST_CREATE_INTENT = re.compile(
    r"\b(create|make|start|build)\b[^.!?]{0,50}\bwatch\s?list\b|\bnew\s+watch\s?list\b", re.I
)
_WATCHLIST_REMOVE_FROM_INTENT = re.compile(
    r"\b(remove|delete|take\s+off)\b[^.!?]{0,30}\bfrom\b[^.!?]{0,20}\bwatch\s?list\b|\bunwatch\b", re.I
)
_WATCHLIST_DELETE_INTENT = re.compile(
    # Negative lookahead: don't match "delete X FROM watchlist" (that's remove_from_watchlist).
    r"\bdelete\b(?![^.!?]{0,50}\bfrom\b)[^.!?]{0,50}\bwatch\s?list\b", re.I
)
# Extracts a bare single-word watchlist name from verb-adjacent phrases like
# "create Tech watchlist" — used when _NAMED_WATCHLIST_RE finds nothing.
# The negative lookahead prevents stopwords ("my", "the", "a", "an", "new")
# from being captured as the name (e.g. "delete my watchlist" → None, not "my").
_WATCHLIST_VERB_NAME_RE = re.compile(
    r"\b(?:create|make|start|build|delete)\b\s+(?:a\s+)?(?:new\s+)?(?:the\s+)?(?:my\s+)?"
    r"(?!(?:my|the|a|an|new)\b)(?P<name>[A-Za-z][A-Za-z0-9\-\.]{1,30})\s+watch\s?list\b",
    re.I,
)
_WHY_MOVE_INTENT = re.compile(r"\b(why did .* move|why is .* (up|down)|what caused .* (move|drop|surge)|explain .* move)\b", re.I)
_WHAT_CHANGED_INTENT = re.compile(r"\b(what changed|what has changed|since yesterday|since my last visit|changed since)\b", re.I)
_COMPARISON_INTENT = re.compile(r"\b(compare|comparison|rank|ranking|strongest|weakest|best performing|worst performing|which .* (higher|lower|stronger|weaker))\b", re.I)
_SCENARIO_INTENT = re.compile(
    r"\b(what if|scenario|under a sell[- ]?off|drops?\b|falls?\b|rises?\b|stop (?:moves?|changes?)|shock"
    r"|suppose (?:i |the )?(?:bought?|entered?|got in)"
    r"|let'?s say (?:i |the )?(?:bought?|entered?|got in|had)"
    r"|if (?:i |the )?(?:bought?|entered?|got in) at"
    r")\b",
    re.I,
)
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
#
# The whole message must be an affirmation (optionally restating the verb):
# a prefix match let "ok nevermind", "okay wait, actually don't" and
# "sure, but first show me TSLA" execute the pending destructive action.
_AFFIRM_WORD = r"(?:yes|yep|yeah|yup|confirm(?:ed)?|do it|go ahead|sure|ok(?:ay)?)"
_AFFIRM_INTENT = re.compile(
    rf"^\s*{_AFFIRM_WORD}"
    r"(?:[\s,.!]+(?:please|"
    rf"{_AFFIRM_WORD}|proceed|(?:delete|remove|save) (?:it|that|them))\b)*"
    r"[\s.!]*$",
    re.I,
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
# An explicit reference to earlier inputs. A bare "it"/"last" is not one:
# "what's the return on it" or "... last year" are new questions, and
# re-running the remembered calculation answered them with stale inputs.
_REUSE_MEMORY_HINT = re.compile(
    r"\b(?:previous|prior|same|those|last|earlier)\s+(?:values?|inputs?|numbers?|figures?|calculation|calc|setup)\b"
    r"|\b(?:recalculate|re-?run|again)\b",
    re.I,
)
# Position-risk wording: "buy 200 AAPL at $220, stop $212", "300 shares at
# 50 with a stop at 47". Each field is read from its own labelled phrase,
# never from number order, so a missing label means a missing input.
# A follow-up that changes remembered calculation inputs ("use the same stop
# but 100 shares"). Narrower than _REUSE_MEMORY_HINT: "it" alone is not
# enough ("is it still above the stop at 212?" is a question, not a re-run).
_CALC_FOLLOWUP_HINT = re.compile(r"\b(same|previous|prior|instead|but|change|use|make it)\b", re.I)
_BARE_NUM = r"([0-9][0-9,]*(?:\.\d+)?)"
_NUM = r"\$?\s*" + _BARE_NUM
_SHARES_RE = re.compile(
    # A share count never carries "$", so it uses the bare number.
    r"\b(?:buy|bought|sell|sold|short|long)\s+" + _BARE_NUM + r"\s+(?:shares?\s+(?:of\s+)?)?(?:\$?[A-Za-z]{1,6}\b)?"
    r"|\b([0-9][0-9,]*)\s+(?:shares?|sh)\b",
    re.I,
)
_ENTRY_RE = re.compile(r"\bentry(?:\s+price)?\s*(?:at|of|is|=|:)?\s*" + _NUM, re.I)
_AT_PRICE_RE = re.compile(r"\bat\s*" + _NUM, re.I)
_NOT_ENTRY_BEFORE_AT = re.compile(r"\b(?:stop|stop[- ]?loss|target|take[- ]profit|account|portfolio)\W*$", re.I)
_STOP_RE = re.compile(r"\bstop(?:[- ]?loss)?\s*(?:price\s*)?(?:at|of|is|=|:|to)?\s*" + _NUM, re.I)
_TARGET_RE = re.compile(r"\b(?:target|take[- ]profit)\s*(?:price\s*)?(?:at|of|is|=|:|to)?\s*" + _NUM, re.I)
# An account size may carry a k/m suffix ("$10k", "1.5m"); prices never do.
_AMOUNT_SUFFIX = r"(?:([km])\b)?"
_ACCOUNT_RE = re.compile(
    r"\b(?:account|portfolio)\s*(?:value|size|balance)?\s*(?:of|is|=|:)?\s*" + _NUM + _AMOUNT_SUFFIX, re.I
)
# "my $10,000 account" — the value before the label. Only consulted when
# the label-first form above finds nothing.
_ACCOUNT_BEFORE_RE = re.compile(
    r"(?<![\d.,%])\$?([0-9](?:[0-9,]*[0-9])?(?:\.\d+)?)" + _AMOUNT_SUFFIX
    + r"\s+(?:dollar\s+)?(?:account|portfolio)\b",
    re.I,
)
_SUFFIX_SCALE = {"k": 1_000, "m": 1_000_000}
_RISK_PERCENT_RE = re.compile(
    r"\brisk(?:ing)?\s*(?:percent(?:age)?)?\s*(?:of|is|=|:)?\s*([0-9]+(?:\.\d+)?)\s*%"
    r"|\b([0-9]+(?:\.\d+)?)\s*%\s*(?:risk|of (?:my |the )?(?:account|portfolio))\b",
    re.I,
)
# Calendar dates ("Jan 5", "January 20, 2026", "1/5", "2026-01-05"). Their
# numbers are never calculator inputs: "return from Jan 5 to Jan 20" is a
# market question, not percentage_change(5, 20).
_CALC_DATE_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
    r"\s+\d{1,2}(?:st|nd|rd|th)?\b(?:,?\s+\d{4})?"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b\d{4}-\d{2}-\d{2}\b",
    re.I,
)


def _numbers_from_text(text: str) -> list[float]:
    return [float(raw.replace(",", "")) for raw in _CALC_NUMBER_RE.findall(text)]


def _labelled_number(pattern: re.Pattern, text: str) -> float | None:
    match = pattern.search(text)
    if not match:
        return None
    raw = next((group for group in match.groups() if group), None)
    return float(raw.replace(",", "")) if raw else None


def _account_value(user_content: str) -> float | None:
    """"account 25000", "$10k account", "portfolio of 1.5m"."""
    for pattern in (_ACCOUNT_RE, _ACCOUNT_BEFORE_RE):
        match = pattern.search(user_content)
        if match:
            value = float(match.group(1).replace(",", "")) * _SUFFIX_SCALE.get((match.group(2) or "").lower(), 1)
            if value:
                return value
    return None


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
        "account_value": _account_value(user_content),
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
    if _CALC_DATE_RE.search(user_content):
        return None
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
    # Each input comes from its own labelled phrase, never from number
    # order; a missing label means a missing input (the caller asks).
    if all(word in text for word in ("entry", "stop", "target")):
        fields = _position_risk_fields(user_content)
        if {"entry_price", "stop_price", "target_price"} <= fields.keys():
            return CalculationRequest(
                calculation="risk_reward",
                entry_price=fields["entry_price"],
                stop_price=fields["stop_price"],
                target_price=fields["target_price"],
            )
    if "position" in text and "risk" in text:
        fields = _position_risk_fields(user_content)
        risk_percent = _labelled_number(_RISK_PERCENT_RE, user_content)
        if {"entry_price", "stop_price", "account_value"} <= fields.keys() and risk_percent:
            return CalculationRequest(
                calculation="position_size",
                entry_price=fields["entry_price"],
                stop_price=fields["stop_price"],
                account_value=fields["account_value"],
                risk_percent=risk_percent,
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


# Date extraction for historical P&L — matches "Jan 2, 2026", "January 2 2026",
# "2026-01-02", "01/02/2026", "sept 23, 2026", etc.
_DATE_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{4}"
    r"|\d{4}-\d{2}-\d{2}",
    re.I,
)
_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date_from_text(text: str) -> date | None:
    text = text.strip()
    # ISO: 2026-01-02
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # Numeric: 01/02/2026 or 01-02-2026 (MM/DD/YYYY)
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    # "Jan 2, 2026" / "January 2 2026" / "Sept 23, 2026"
    m = re.match(
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
        text, re.I,
    )
    if m:
        month = _MONTH_MAP.get(m.group(1).lower()[:3])
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                return None
    return None


def _extract_watchlist_name(user_content: str) -> str | None:
    """Extract a watchlist name from a CRUD intent message.

    Tries the canonical _NAMED_WATCHLIST_RE patterns first (quoted name,
    "my/the X watchlist", "watchlist called X"), then the verb-adjacent
    single-word fallback for bare "create Tech watchlist" patterns.
    """
    m = _NAMED_WATCHLIST_RE.search(user_content)
    if m:
        name = (m.group("name1") or m.group("name2") or m.group("name3") or "").strip(" \"'“”")
        if name:
            return name
    m2 = _WATCHLIST_VERB_NAME_RE.search(user_content)
    if m2:
        name = m2.group("name").strip(" \"'")
        if name:
            return name
    return None


def _extract_memory_value(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    value = match.group(1).lower().replace(" ", "_")
    return "after_hours" if value in {"after-hour", "after-hours", "after_hours"} else value
