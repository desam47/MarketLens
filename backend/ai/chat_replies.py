"""Plain-sentence Chat replies built from verified tool and calculator results.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

# Chat runs its sync generator helpers on loop-less worker threads
# (ThreadPoolExecutor / asyncio.to_thread), so the async AI calls are
# bridged with run_sync/stream_sync rather than awaited.

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


# Wording helpers for the server's own replies. They read as plain sentences,
# and each keeps the answer verifier's rules: every number still comes from
# the evidence, dates are written as "Sep 23, 2026" (not number claims),
# data older than 15 minutes never uses "current"/"today"/"now", and an
# intraday timeframe stays a token like "5m" rather than "5-minute".
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_CHART_NAMES = {"1d": "daily", "1wk": "weekly", "1w": "weekly", "1h": "hourly", "daily": "daily", "weekly": "weekly"}
_SOURCE_NAMES = {
    "webull": "Webull",
    "yahoo_finance": "Yahoo Finance",
    "alpaca": "Alpaca",
    "alpaca_iex": "Alpaca (IEX)",
    "finnhub": "Finnhub",
    "live_from_1m": "live intraday bars",
}


def _nice_date(value: object, *, with_year: bool = True) -> str:
    """"2026-09-23" (or a timestamp) as "Sep 23, 2026"; other text unchanged."""
    text = str(value or "")
    try:
        day = date.fromisoformat(text[:10])
    except ValueError:
        return text
    base = f"{_MONTH_ABBR[day.month - 1]} {day.day}"
    return f"{base}, {day.year}" if with_year else base


def _date_span(start: object, end: object) -> str:
    try:
        same_year = date.fromisoformat(str(start)[:10]).year == date.fromisoformat(str(end)[:10]).year
    except ValueError:
        return f"{start} to {end}"
    return f"{_nice_date(start, with_year=not same_year)} to {_nice_date(end)}"


def _chart_name(timeframe: object) -> str:
    """"daily"/"weekly"/"hourly", or the timeframe token itself ("5m")."""
    text = str(timeframe or "").strip()
    return _CHART_NAMES.get(text.lower(), text)


def _source_name(provider: object) -> str:
    text = str(provider or "").strip()
    if text in _SOURCE_NAMES:
        return _SOURCE_NAMES[text]
    if re.search(r"\d", text):
        return "MarketLens"
    return text.replace("_", " ") if " " in text or text[:1].isupper() else text.replace("_", " ").title()


def _age_words(seconds: float) -> str:
    """A rough age with no digits, so it is never read as a market number."""
    if seconds < 3600:
        return "less than an hour"
    if seconds < 2 * 3600:
        return "about an hour"
    if seconds < 24 * 3600:
        return "several hours"
    if seconds < 48 * 3600:
        return "about a day"
    return "several days"


def _join_and(items: list[str]) -> str:
    items = [item for item in items if item]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _count(n: int, singular: str, plural: str | None = None) -> str:
    """"no news items" / "1 news item" / "3 news items"."""
    plural = plural or f"{singular}s"
    if n == 0:
        return f"no {plural}"
    return f"{n} {singular if n == 1 else plural}"


def _as_sentence(text: object) -> str:
    """Tool-provided prose as a sentence: field names spelled out
    ("entry_price" -> "entry price"), ISO dates written out, capitalised,
    ending in a full stop."""
    value = re.sub(r"(?<=[a-z])_(?=[a-z])", " ", str(text or "").strip())
    value = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", lambda match: _nice_date(match.group()), value)
    if not value:
        return ""
    value = value[:1].upper() + value[1:]
    return value if value.endswith((".", "!", "?")) else value + "."


def _stale_note(freshness_seconds: float | None) -> str:
    if isinstance(freshness_seconds, (int, float)) and freshness_seconds > 900:
        return f" Keep in mind this data is {_age_words(freshness_seconds)} old, so refresh it before relying on it."
    return ""


def _format_browser_local_reply(action: str, data: dict, provider: str, *, query: str | None = None) -> str:
    """Turn aggregate browser-local tool data into user-facing prose.

    Browser-local rows are intentionally excluded from the durable transcript.
    This formatter keeps that privacy boundary while avoiding a raw JSON
    payload in the Chat panel.
    """
    if action in {"get_risk_dashboard", "assess_portfolio_risk", "scenario_analysis"}:
        if data.get("available") is False:
            reason = str(data.get("reason") or "No browser-local positions were shared for this turn.")
            if action == "scenario_analysis":
                return f"I can't run a portfolio scenario yet. {_as_sentence(reason)}"
            if query == "portfolio_change":
                return f"I can't show how your portfolio changed yet. {_as_sentence(reason)}"
            if query == "portfolio_weakness":
                return f"I can't rank your portfolio's weakest holdings yet. {_as_sentence(reason)}"
            return f"I can't assess your portfolio risk yet. {_as_sentence(reason)}"
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
        count_text = (
            "your one position"
            if position_count == 1
            else f"your {position_count} positions"
            if isinstance(position_count, int)
            else "the positions you shared"
        )
        figures = [
            f"{label} is ${float(value):,.2f}"
            for label, value in (
                ("gross exposure", data.get("gross_exposure")),
                ("net exposure", data.get("net_exposure")),
                ("stop-loss risk", data.get("stop_loss_risk")),
            )
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        reply = (
            f"Across {count_text}, {_join_and(figures)}."
            if figures
            else f"I have {count_text}, but no exposure figures to summarize."
        )
        price_basis = data.get("price_basis")
        if price_basis:
            reply += f" {_as_sentence(price_basis)}"
        return reply

    if action in {"get_trade_journal", "trade_journal_coach"}:
        if data.get("available") is False:
            reason = str(data.get("reason") or "No browser-local journal entries were shared for this turn.")
            return f"I can't review your trade journal yet. {_as_sentence(reason)}"
        total = data.get("total_entries")
        closed = data.get("closed_entries")
        if isinstance(total, int):
            reply = f"Your trade journal has {total} entr{'y' if total == 1 else 'ies'}"
            if isinstance(closed, int) and closed == total:
                reply += ", and it's closed" if total == 1 else ", all of them closed"
            elif isinstance(closed, int):
                reply += f", {closed} of them closed"
        elif isinstance(closed, int):
            reply = f"Your trade journal has {closed} closed entr{'y' if closed == 1 else 'ies'}"
        else:
            reply = "I have your trade journal"
        stats = []
        win_rate = data.get("win_rate_percent")
        if isinstance(win_rate, (int, float)) and not isinstance(win_rate, bool):
            stats.append(f"a {float(win_rate):.1f}% win rate")
        expectancy = data.get("expectancy_per_trade")
        if isinstance(expectancy, (int, float)) and not isinstance(expectancy, bool):
            stats.append(f"an expectancy of ${float(expectancy):,.2f} per trade")
        if stats:
            return f"{reply}, with {_join_and(stats)}."
        return f"{reply}, but not enough closed trades for win-rate statistics yet."

    if action == "get_saved_scans":
        if data.get("available") is False:
            reason = str(data.get("reason") or "No browser-local saved Scanner presets were shared for this turn.")
            return f"I can't see your saved scans yet. {_as_sentence(reason)}"
        preset_count = data.get("preset_count")
        if not isinstance(preset_count, int):
            presets = data.get("presets")
            preset_count = len(presets) if isinstance(presets, list) else None
        if isinstance(preset_count, int):
            return f"You have {preset_count} saved Scanner preset{'s' if preset_count != 1 else ''}."
        return "You have saved Scanner presets."

    return f"Here's the {action.replace('_', ' ')} result from {_source_name(provider) or 'MarketLens'}."


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
    chart = _chart_name(timeframe or data.get("timeframe") or arguments.get("timeframe"))
    reply = f"{symbol}'s {label}{period_text} is {value_text}"
    if chart:
        reply += f" on the {chart} chart"
    context = []
    if as_of:
        context.append(f"as of {_nice_date(as_of)}")
    if provider:
        context.append(f"from {_source_name(provider)}")
    if context:
        reply += f" ({', '.join(context)})"
    return reply + "." + _stale_note(freshness_seconds)


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
        reason = data.get("reason") or "the required verified data was not available"
        target = f" for {symbol}" if symbol else ""
        return f"{label.capitalize()} isn't available{target}. {_as_sentence(reason)}"

    if action == "save_to_journal":
        saved = data.get("saved_entry") or {}
        sym = str(saved.get("symbol") or arguments.get("symbol") or "").upper()
        side = str(saved.get("side") or "").lower()
        total = data.get("total_entries")
        parts = [sym, side] if sym and side else [sym or side]
        header = " ".join(p for p in parts if p)
        count_note = f" You now have {total} {'entry' if total == 1 else 'entries'}." if isinstance(total, int) else ""
        return f"Done — saved {header} trade to your Journal.{count_note}" if header else f"Done — trade saved to your Journal.{count_note}"

    effective_timeframe = timeframe or str(data.get("timeframe") or "")
    # Daily/weekly bars are end-of-day data; their age past 15 minutes is
    # expected, so no stale note for them.
    bars_eod = action == "get_bars" and effective_timeframe in ("1d", "1wk", "1w", "daily", "weekly")
    stale = not bars_eod and isinstance(freshness_seconds, (int, float)) and freshness_seconds > 900
    freshness_tag: str | None = None
    if isinstance(freshness_seconds, (int, float)):
        seconds = freshness_seconds
        if seconds < 60:
            freshness_tag = f"{seconds:.0f}s old"
        elif seconds < 3600:
            freshness_tag = f"{seconds / 60:.0f}min old"
        else:
            freshness_tag = f"{seconds / 3600:.1f}hr old"
    elif source_timestamp:
        freshness_tag = f"as of {_nice_date(source_timestamp)}"
    provenance = [part for part in (_source_name(provider) if provider else "", freshness_tag) if part]
    source_note = f" (source: {', '.join(provenance)})" if provenance else ""
    whose = f"{symbol}'s " if symbol else ""
    sentence = _generic_market_sentence(action, data, symbol=symbol, label=label, timeframe=effective_timeframe)
    reply = sentence[:-1] + source_note + "." if sentence.endswith(".") and source_note else sentence
    if not sentence:
        reply = f"I have {whose}{label}, but it has no summary figures to show{source_note}."
    if stale:
        reply += _stale_note(freshness_seconds)
    return reply


def _generic_market_sentence(action: str, data: dict, *, symbol: str, label: str, timeframe: str) -> str:
    """One or two plain sentences for a read-only tool result, or ""."""
    whose = f"{symbol}'s " if symbol else ""
    about = f" for {symbol}" if symbol else ""
    if action == "get_quote":
        price = data.get("price")
        change = data.get("change_percent")
        if not isinstance(price, (int, float)):
            return ""
        sentence = f"{symbol or 'It'} is trading at ${float(price):.2f}"
        if isinstance(change, (int, float)):
            if change > 0:
                sentence += f", up {float(change):.2f}% on the day"
            elif change < 0:
                sentence += f", down {abs(float(change)):.2f}% on the day"
            else:
                sentence += ", unchanged on the day"
        return sentence + "."
    collection = {
        "get_bars": ("bars", "price bar"),
        "get_signal_history": ("signals", "recorded signal"),
        "market_event_timeline": ("events", "market event"),
        "get_news": ("items", "news item"),
        "get_calendar": ("events", "upcoming catalyst event"),
        "get_alerts": ("alerts", "alert"),
    }.get(action)
    if collection:
        key, noun = collection
        rows = data.get(key)
        if not isinstance(rows, list):
            return ""
        chart = _chart_name(timeframe)
        on_chart = f" on the {chart} chart" if action == "get_bars" and chart else ""
        return f"I found {_count(len(rows), noun)}{about}{on_chart}."
    if action == "get_support_resistance":
        levels = [
            f"{key} is at ${float(data[key]):.2f}"
            for key in ("support", "resistance")
            if isinstance(data.get(key), (int, float))
        ]
        return f"For {symbol or 'this symbol'}, {_join_and(levels)}." if levels else ""
    if action == "get_market_regime":
        regime = str(data.get("regime") or "").replace("_", " ")
        if not regime:
            return ""
        sentence = f"{whose or 'The '}market regime is {regime}"
        if isinstance(data.get("confidence"), (int, float)):
            sentence += f", with {float(data['confidence']):.0%} confidence"
        return sentence + "."
    if action == "get_relative_strength":
        comparisons = [
            f"{float(item['rs_pct']):+.2f}% versus {item.get('benchmark') or 'its benchmark'}"
            for item in (data.get("signals") or [])[:3]
            if isinstance(item, dict) and isinstance(item.get("rs_pct"), (int, float))
        ]
        return f"{whose}relative strength is {_join_and(comparisons)}." if comparisons else ""
    if action == "get_session_stats":
        session = str(data.get("session") or "").replace("_", " ")
        when = _nice_date(data["date"]) if isinstance(data.get("date"), str) else ""
        prices = {key: data.get(key) for key in ("open", "high", "low", "close")}
        if not any(isinstance(value, (int, float)) for value in prices.values()):
            return ""
        lead = "In the " + (f"{session} session" if session else "session") + (f" on {when}" if when else "")
        parts = []
        if isinstance(prices["open"], (int, float)):
            parts.append(f"opened at {float(prices['open']):.2f}")
        if isinstance(prices["low"], (int, float)) and isinstance(prices["high"], (int, float)):
            parts.append(f"traded between {float(prices['low']):.2f} and {float(prices['high']):.2f}")
        if isinstance(prices["close"], (int, float)):
            closed = f"closed at {float(prices['close']):.2f}"
            change = data.get("change_percent")
            if isinstance(change, (int, float)) and change:
                closed += f", {'up' if change > 0 else 'down'} {abs(float(change)):.2f}%"
            parts.append(closed)
        return f"{lead}, {symbol or 'it'} {_join_and(parts)}."
    if action in {"get_fundamentals", "get_options_snapshot", "options_research"}:
        names = {
            "near_term_iv": "near-term IV",
            "iv_rank": "IV rank",
            "pe_ratio": "P/E ratio",
            "eps": "EPS",
            "revenue": "revenue",
            "market_cap": "market cap",
        }
        figures = [
            f"{names[key]} {float(data[key]):.2f}"
            for key in names
            if isinstance(data.get(key), (int, float))
        ]
        figures += [f"{len(data[key])} {key}" for key in ("chains", "expirations", "legs", "spreads") if isinstance(data.get(key), list)]
        return f"Here's {whose}{label}: {_join_and(figures)}." if figures else ""
    if action == "why_did_it_move":
        facts = data.get("facts") or []
        correlations = data.get("correlations") or []
        return (
            f"For {whose}move, I found {_count(len(facts), 'verified price or volume fact')} and "
            f"{_count(len(correlations), 'correlation')}, but nothing that establishes a cause."
        )
    if action == "anomaly_analysis":
        anomalies = data.get("anomalies")
        if isinstance(anomalies, list):
            return f"I found {_count(len(anomalies), 'unusual reading')}{about}."
        return ""
    details = [
        f"{key.replace('_', ' ')} {value}"
        for key in ("status", "conclusion", "sample_size", "total_pnl_delta", "risk_reward", "verdict")
        if isinstance((value := data.get(key)), (str, int, float)) and not isinstance(value, bool)
    ]
    if details:
        return f"Here's the {label}{about}: {_join_and(details)}."
    return ""


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
    reference = str(data.get("reference") or arguments.get("reference") or "previous_close")
    reference_label = {
        "previous_close": "the previous close",
        "yesterday": "yesterday's close",
        "last_visit": "the last visit",
        "timestamp": "the requested baseline",
    }.get(reference, reference.replace("_", " "))
    if percent > 0:
        moved = f"is up {percent:.2f}%"
    elif percent < 0:
        moved = f"is down {abs(percent):.2f}%"
    else:
        moved = "is unchanged"
    reply = f"{symbol} {moved} versus {reference_label}"
    if isinstance(current, (int, float)) and isinstance(baseline, (int, float)):
        reply += f", moving from ${float(baseline):.2f} to ${float(current):.2f}"
    context = [f"{_chart_name(timeframe or data.get('timeframe') or arguments.get('timeframe') or '1d')} chart"]
    if source_timestamp:
        context.append(f"as of {_nice_date(source_timestamp)}")
    if provider:
        context.append(f"from {_source_name(provider)}")
    return f"{reply} ({', '.join(context)})." + _stale_note(freshness_seconds)


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


def _money(value: float | None) -> str:
    """$1,234.56 — sub-dollar values keep 4dp so penny stocks stay meaningful.

    Rounding is always inside the verifier's match tolerance (the looser of
    1c or 0.05% of the value), so a displayed number still matches its evidence.
    """
    if value is None:
        return "n/a"
    digits = 4 if value != 0 and abs(value) < 1 else 2
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{digits}f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}%"


def _qty(value: float | None) -> str:
    """Share/contract counts: no decimals when whole."""
    if value is None:
        return "n/a"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def _is_market_session_closed() -> bool:
    """Whether the US market calendar considers the current session closed.

    Chat uses this for end-of-day answers, where the last completed close is
    the expected reference point rather than a live quote.
    """
    try:
        from backend.engines.market_calendar import SessionType, us_market_calendar

        return us_market_calendar.get_session_type(datetime.now(UTC)) == SessionType.CLOSED
    except Exception:
        return False


def _is_regular_market_closed() -> bool:
    """Whether the regular 09:30–16:00 ET session is no longer open.

    An end-of-day bar is valid through premarket and after-hours too: those
    extended sessions do not make a daily close a live/intraday value.
    """
    try:
        from backend.engines.market_calendar import SessionType, us_market_calendar

        return us_market_calendar.get_session_type(datetime.now(UTC)) != SessionType.REGULAR
    except Exception:
        return False


def _market_closed_note(subject: str) -> str:
    if _is_market_session_closed():
        return f" Market is closed, so {subject} is the most recent session's close."
    return ""


def _format_calculation_reply(
    request,
    values: dict,
    assumptions: list[str],
    *,
    context: dict,
    provider: str,
) -> str:
    """Render any calculator result as prose instead of a key=value dump.

    Every number printed traces to the calculator's own returned values or the
    inputs it was given, so the answer still verifies against its evidence.
    Direction words must match the sign of the result — a positive word against
    a negative value trips the verifier's contradictory-evidence check.
    """
    op = request.calculation
    get = lambda key: values.get(key)  # noqa: E731 — local shorthand only

    if op == "position_pnl":
        total = float(get("total_pnl") or 0.0)
        symbol = str(context.get("symbol") or "").upper() or "the position"
        entry_date = str(context.get("entry_date") or "")
        exit_date = str(context.get("exit_date") or "")
        entry_when = f" ({entry_date} close)" if entry_date else ""
        exit_when = f" ({exit_date} close)" if exit_date else ""
        outcome = (
            f"a profit of {_money(abs(total))}"
            if total > 0
            else f"a loss of {_money(abs(total))}"
            if total < 0
            else "break-even"
        )
        body = (
            f"{_qty(request.shares)} shares of {symbol} at {_money(request.entry_price)}"
            f"{entry_when} cost {_money(get('cost_basis'))}. "
            f"At {_money(request.exit_price)}{exit_when} the position is worth "
            f"{_money(get('exit_value'))} — {outcome} "
            f"({_money(abs(float(get('per_share_pnl') or 0.0)))} per share, "
            f"{_pct(get('return_percent'))})."
            f"{_market_closed_note('the exit price')}"
        )
    elif op == "percentage_change":
        change = float(get("percentage_change") or 0.0)
        body = (
            f"{_money(request.old_value)} to {_money(request.new_value)} is unchanged."
            if change == 0
            else f"{_money(request.old_value)} to {_money(request.new_value)} is a "
            f"{_pct(abs(change))} {'increase' if change > 0 else 'decrease'}."
        )
    elif op == "dollar_change":
        change = float(get("dollar_change") or 0.0)
        body = (
            f"{_money(request.old_value)} to {_money(request.new_value)} is unchanged."
            if change == 0
            else f"{_money(request.old_value)} to {_money(request.new_value)} is a "
            f"{_money(abs(change))} {'increase' if change > 0 else 'decrease'} per share."
        )
    elif op == "return":
        ret = float(get("return") or 0.0)
        body = (
            f"{_money(request.start_value)} to {_money(request.end_value)} is a "
            f"{_pct(abs(ret))} {'return' if ret >= 0 else 'negative return'}."
        )
    elif op == "cagr":
        body = (
            f"Growing {_money(request.start_value)} to {_money(request.end_value)} over "
            f"{_qty(request.years)} years is a compound annual growth rate of "
            f"{_pct(get('cagr'))}."
        )
    elif op == "weighted_average":
        body = (
            f"The weighted average of those prices is {_money(get('weighted_average'))}."
        )
    elif op == "position_size":
        body = (
            f"Risking {_pct(get('portfolio_risk_percent'))} of a "
            f"{_money(request.account_value)} account at {_money(request.entry_price)} with a "
            f"{_money(request.stop_price)} stop allows {_qty(get('shares'))} shares — "
            f"{_money(get('per_share_risk'))} of risk per share, "
            f"{_money(get('risk_dollars'))} at risk in total, for a "
            f"{_money(get('position_value'))} position."
        )
    elif op == "position_risk":
        extras = []
        if get("portfolio_risk_percent") is not None:
            extras.append(f"that is {_pct(get('portfolio_risk_percent'))} of the account")
        if get("reward_risk") is not None:
            extras.append(f"reward/risk to the target is {_ratio(get('reward_risk'))}")
        tail = f" — {', and '.join(extras)}." if extras else "."
        body = (
            f"{_qty(request.shares)} shares at {_money(request.entry_price)} with a "
            f"{_money(request.stop_price)} stop is a {_money(get('position_value'))} position "
            f"risking {_money(get('per_share_risk'))} per share, "
            f"{_money(get('total_risk'))} in total{tail}"
        )
    elif op == "risk_reward":
        body = (
            f"A {_money(request.entry_price)} entry with a {_money(request.stop_price)} stop "
            f"risks {_money(get('risk'))} per share to make {_money(get('reward'))} at the "
            f"{_money(request.target_price)} target — a reward/risk ratio of "
            f"{_ratio(get('risk_reward'))}."
        )
    elif op == "allocation":
        body = (
            f"A {_money(request.position_value)} position in a "
            f"{_money(request.portfolio_value)} portfolio is "
            f"{_pct(get('allocation_percent'))} of it."
        )
    elif op == "volatility":
        body = (
            f"Period volatility across those prices (standard deviation of returns) is "
            f"{_pct(get('period_volatility'))}."
        )
    elif op == "drawdown":
        body = (
            f"From a {_money(request.peak_value)} peak down to "
            f"{_money(request.trough_value)} is a {_pct(get('drawdown_percent'))} drawdown."
        )
    elif op == "max_drawdown":
        body = (
            f"The deepest peak-to-trough decline across those prices is "
            f"{_pct(get('maximum_drawdown_percent'))}."
        )
    elif op == "correlation":
        corr = float(get("correlation") or 0.0)
        strength = "strong" if abs(corr) >= 0.7 else "moderate" if abs(corr) >= 0.4 else "weak"
        body = (
            f"The correlation between the two series is {_ratio(corr)} — a {strength} "
            f"{'positive' if corr >= 0 else 'inverse'} relationship."
        )
    elif op == "options_breakeven":
        body = (
            f"A {request.option_type} at the {_money(request.strike)} strike paid for with "
            f"{_money(request.premium)} of premium breaks even at "
            f"{_money(get('breakeven'))} at expiration."
        )
    elif op == "options_intrinsic_value":
        body = (
            f"With the underlying at {_money(request.underlying_price)}, the "
            f"{_money(request.strike)} {request.option_type} has "
            f"{_money(get('intrinsic_value'))} of intrinsic value."
        )
    elif op == "options_extrinsic_value":
        body = (
            f"Of the {_money(request.premium)} premium on the {_money(request.strike)} "
            f"{request.option_type}, {_money(get('extrinsic_value'))} is extrinsic "
            f"(time and volatility) value."
        )
    elif op == "options_max_gain_loss":
        max_gain = get("max_gain_per_share")
        gain_text = "unlimited" if max_gain is None else _money(max_gain)
        body = (
            f"On the {_money(request.strike)} {request.option_type} bought for "
            f"{_money(request.premium)}, the most you can make is {gain_text} per share "
            f"and the most you can lose is {_money(get('max_loss_per_share'))} per share "
            f"(the premium paid)."
        )
    elif op == "options_assignment_exposure":
        body = (
            f"If assigned, {_qty(request.contracts or 1)} "
            f"{request.option_type} contract(s) at the {_money(request.strike)} strike means "
            f"{_qty(get('assignment_shares'))} shares and "
            f"{_money(get('assignment_cash_exposure'))} of cash exposure."
        )
    elif op == "options_vertical_spread":
        net_debit = float(get("net_debit") or 0.0)
        opening = (
            f"costs {_money(net_debit)} net debit per share"
            if net_debit > 0
            else f"collects {_money(abs(net_debit))} net credit per share"
            if net_debit < 0
            else "has no net debit or credit"
        )
        body = (
            f"That {request.option_type} vertical {opening}. "
            f"Best case is {_money(get('max_gain_per_share'))} per share "
            f"({_money(get('max_gain_total'))} total), worst case "
            f"{_money(get('max_loss_per_share'))} per share "
            f"({_money(get('max_loss_total'))} total), breaking even at "
            f"{_money(get('breakeven'))}."
        )
    elif op == "expected_move":
        body = (
            f"At {_money(request.new_value)} with that implied volatility over "
            f"{_qty(request.days_to_expiration)} days, the expected move is "
            f"±{_money(get('expected_move'))} — roughly {_money(get('lower_bound'))} to "
            f"{_money(get('upper_bound'))}."
        )
    else:
        # Unknown operation: name each value in prose rather than dumping key=value.
        named = ", ".join(
            f"{key.replace('_', ' ')} {value:,.2f}" if isinstance(value, (int, float)) else f"{key.replace('_', ' ')} {value}"
            for key, value in values.items()
            if value is not None
        )
        body = f"{op.replace('_', ' ').capitalize()}: {named}." if named else f"{op.replace('_', ' ').capitalize()} completed."

    # Only carry a caveat that states no numbers of its own — a figure in
    # explanatory prose has no evidence behind it and would fail verification.
    caveat = next((f" {note}" for note in assumptions if not any(ch.isdigit() for ch in note)), "")
    price_source = str(context.get("provider") or "").strip()
    source = f"{price_source} prices, {provider}" if price_source else provider
    return f"{body}{caveat} Source: {source}."


def _format_price_statistics_reply(data: dict) -> str:
    """A plain-sentence answer from get_price_statistics' verified values.

    States the window actually covered and how many trading days it holds,
    so a trader can see what "this year" or "30 days" resolved to, plus any
    coverage gap the tool reported.
    """
    symbol = str(data.get("symbol") or "").upper()
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    window = f"{_date_span(data.get('start_date'), data.get('end_date'))} ({data.get('observations')} trading days)"
    metric = data.get("metric")
    if metric == "return_percent":
        change = values.get("return_percent")
        if not isinstance(change, (int, float)):
            text = f"I couldn't compute {symbol}'s return from {window}."
        else:
            move = (
                f"was up {float(change):.2f}%"
                if change > 0
                else f"was down {abs(float(change)):.2f}%"
                if change < 0
                else "was unchanged"
            )
            text = (
                f"From {window}, {symbol} {move}, going from "
                f"{_money(values.get('start_close'))} to {_money(values.get('end_close'))}."
            )
    elif metric == "volatility":
        daily = values.get("daily_volatility_percent")
        annual = values.get("annualized_volatility_percent")
        text = (
            f"From {window}, {symbol}'s daily returns had a standard deviation of "
            f"{float(daily):.2f}%, which is about {float(annual):.2f}% annualized."
            if isinstance(daily, (int, float)) and isinstance(annual, (int, float))
            else f"I couldn't compute {symbol}'s volatility from {window}."
        )
    elif metric == "max_drawdown":
        depth = values.get("max_drawdown_percent")
        if isinstance(depth, (int, float)) and depth > 0:
            one_year = str(data.get("start_date"))[:4] == str(data.get("end_date"))[:4]
            text = (
                f"From {window}, {symbol}'s largest drawdown was {float(depth):.2f}%: it dropped from "
                f"{_money(values.get('peak_close'))} on {_nice_date(values.get('peak_date'), with_year=not one_year)} "
                f"to {_money(values.get('trough_close'))} on {_nice_date(values.get('trough_date'), with_year=not one_year)}."
            )
        else:
            text = f"From {window}, {symbol} had no drawdown: it never closed below an earlier high."
    else:
        other = str(data.get("comparison_symbol") or "").upper()
        correlation = values.get("correlation")
        if isinstance(correlation, (int, float)):
            strength = abs(float(correlation))
            if strength >= 0.7:
                relation = "they moved together closely" if correlation > 0 else "they tended to move in opposite directions"
            elif strength >= 0.4:
                relation = "they tended to move together" if correlation > 0 else "they often moved in opposite directions"
            elif strength >= 0.2:
                relation = "they moved together only loosely" if correlation > 0 else "they moved in opposite directions only loosely"
            else:
                relation = "they moved largely independently"
            text = (
                f"From {window}, the daily returns of {symbol} and {other} had a correlation of "
                f"{float(correlation):.2f}, so {relation}."
            )
        else:
            text = f"I couldn't compute a correlation for {symbol} and {other} from {window}."
    notes = [
        str(item.get("reason"))
        for item in (data.get("unknowns") or [])
        if isinstance(item, dict) and item.get("reason")
    ]
    if notes:
        caveat = _as_sentence("; ".join(notes))
        text += " One caveat: " + caveat[:1].lower() + caveat[1:]
    return text


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
                # Skip if this metric duplicates change_pct already shown above.
                if not (isinstance(change_pct, (int, float)) and abs(metric - change_pct) < 0.01):
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
