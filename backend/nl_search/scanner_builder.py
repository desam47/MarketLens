"""Natural-language Scanner Builder.

This layer translates a query into the *same* ``FilterSpec`` shape consumed
by ``POST /api/scanner/filter`` and the React Filter Builder.  It never runs a
scan itself: the caller gets a visible preview first, can edit it, and only
then sends that exact list of filters to the scanner endpoint.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.scanner.filters import default_registry as scanner_filter_registry

from .parser import parse_query, parse_query_rule_based
from .schema import NLFilters


class ScannerFilterSpec(BaseModel):
    """A serialisable scanner filter accepted unchanged by the Scanner API."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)


class ScannerBuilderPreview(BaseModel):
    query: str
    filters: list[ScannerFilterSpec]
    match: Literal["AND", "OR"] = "AND"
    filter_description: str
    filter_schema: NLFilters
    parser_used: Literal["ai", "rules", "default"]
    ai_translation_used: bool
    ambiguous: bool
    unresolved: list[str] = Field(default_factory=list)


_EARNINGS_EXCLUSION_FILTER = "exclude_earnings_within_days"
_VALID_FILTER_TYPES = frozenset([*scanner_filter_registry.list_types(), _EARNINGS_EXCLUSION_FILTER])
_DIR_MAP = {"bullish": "uptrend", "bearish": "downtrend"}
_SCANNER_ONLY_PHRASE = re.compile(
    r"\b(?:pre[ -]?market|after[ -]?hours?|breakouts?|breakdowns?|oversold reversal|"
    r"volume (?:spike|surge|expansion)|rising volume|volatility (?:contraction|squeeze|expansion)|"
    r"price\s+(?:above|below)|(?:20|50|200)[ -]?(?:day|bar)?\s*(?:sma|moving average|ma)|"
    r"vwap|ema (?:alignment|cross)|(?:all|every|multi(?:ple)?)[ -]?timeframes?\s+(?:are\s+)?align|"
    r"tight spread|spread widening|tape pressure|large prints?|trade[ -]?rate spike|"
    r"(?:bid|ask)[/-]?(?:ask)?\s+(?:imbalance|heavy)|live volume acceleration|"
    r"(?:exclude|avoid|without|no)\s+(?:upcoming\s+)?earnings)\b",
    re.I,
)


def _number_after(pattern: str, text: str, default: float) -> float:
    match = re.search(pattern, text, re.I)
    return float(match.group(1)) if match else default


def _add(filters: list[ScannerFilterSpec], type_: str, params: dict[str, Any] | None = None) -> None:
    """Append one validated filter once, preserving deterministic order."""
    params = params or {}
    candidate = ScannerFilterSpec(type=type_, params=params)
    if candidate.type not in _VALID_FILTER_TYPES:
        raise ValueError(f"Scanner Builder produced unsupported filter {candidate.type!r}")
    if candidate.type != _EARNINGS_EXCLUSION_FILTER:
        # Validate against the exact registry the execution endpoint uses.
        # A preview can therefore never show a filter the Scanner cannot run.
        scanner_filter_registry.build(candidate.model_dump())
    if not any(item.type == candidate.type and item.params == candidate.params for item in filters):
        filters.append(candidate)


def _append_schema_filters(filters: list[ScannerFilterSpec], schema: NLFilters) -> None:
    """Map the established NL-search schema to editable Scanner filters."""
    direction = _DIR_MAP.get(schema.direction or "")
    if direction and schema.timeframe:
        _add(filters, "timeframe_direction", {
            "timeframe": schema.timeframe,
            "direction": direction,
            "min_confidence": schema.min_confidence,
        })
    elif direction == "uptrend":
        _add(filters, "daily_bullish", {"min_confidence": schema.min_confidence})
    elif direction == "downtrend":
        _add(filters, "daily_bearish", {"min_confidence": schema.min_confidence})

    if schema.trend_min is not None:
        _add(filters, "trend_score_gt", {"threshold": schema.trend_min})
    if schema.trend_max is not None:
        _add(filters, "trend_score_lt", {"threshold": schema.trend_max})
    if schema.volume_min is not None:
        _add(filters, "high_volume", {"min_volume": schema.volume_min})
    if schema.rsi_oversold_below is not None:
        _add(filters, "rsi_oversold", {"threshold": schema.rsi_oversold_below})
    if schema.rsi_overbought_above is not None:
        _add(filters, "rsi_overbought", {"threshold": schema.rsi_overbought_above})
    if schema.adx_strong_above is not None:
        _add(filters, "adx_strong", {"threshold": schema.adx_strong_above})
    if schema.macd == "bullish":
        _add(filters, "macd_bullish")
    elif schema.macd == "bearish":
        _add(filters, "macd_bearish")
    if schema.min_bullish_timeframes is not None:
        _add(filters, "min_timeframe_bullish", {
            "min_count": schema.min_bullish_timeframes,
            "min_confidence": schema.min_confidence,
        })
    if schema.outperforms:
        _add(filters, "relative_strength_above", {
            "benchmark": schema.outperforms,
            "min_pct": schema.relative_strength_min or 0,
        })
    if schema.signals:
        _add(filters, "signal_present", {"signals": schema.signals})


def _append_phrase_filters(filters: list[ScannerFilterSpec], query: str, unresolved: list[str]) -> None:
    """Handle Scanner-only criteria the older NL search schema cannot express."""
    text = query.lower()

    # Session scope.
    if re.search(r"\b(pre[ -]?market)\b", text):
        _add(filters, "market_session", {"session": "premarket"})
    elif re.search(r"\b(after[ -]?hours?|post[ -]?market)\b", text):
        _add(filters, "market_session", {"session": "after_hours"})
    elif re.search(r"\bregular(?:[ -]?hours?)?\b", text):
        _add(filters, "market_session", {"session": "regular"})

    # Price/volume/volatility patterns.
    if re.search(r"\b(?:breakouts?|break out)\b", text):
        _add(filters, "breakout", {"lookback": "20", "min_breakout_pct": 0})
    if re.search(r"\b(?:breakdowns?|break down)\b", text):
        _add(filters, "breakdown", {"lookback": "20", "min_breakdown_pct": 0})
    if re.search(r"\boversold reversal\b", text):
        _add(filters, "oversold_reversal", {"threshold": 35, "min_rsi_rise": 2})
    if re.search(r"\b(?:volume spike|volume surge|volume expansion|rising volume|high volume)\b", text):
        _add(filters, "volume_expansion", {
            "min_ratio": _number_after(r"(?:volume (?:spike|surge|expansion)|rising volume).*?(\d+(?:\.\d+)?)\s*[x×]", text, 1.5),
        })
    if re.search(r"\b(?:volatility contraction|volatility squeeze|squeeze)\b", text):
        _add(filters, "volatility_contraction", {"max_ratio": 0.75})
    if re.search(r"\b(?:volatility expansion|volatility spike)\b", text):
        _add(filters, "volatility_expansion", {"min_ratio": 1.25})

    price = re.search(r"\bprice\s+(above|below)\s+\$?(\d+(?:\.\d+)?)\b", text)
    if price:
        _add(filters, "price_above" if price.group(1) == "above" else "price_below", {
            "price": float(price.group(2)),
        })

    # Moving averages and VWAP.
    ma = re.search(r"\b(?:price\s+)?(above|below)\s+(?:the\s+)?(20|50|200)(?:[ -]?(?:day|bar))?\s+(?:sma|moving average|ma)\b", text)
    if ma:
        _add(filters, "price_above_ma" if ma.group(1) == "above" else "price_below_ma", {
            "period": ma.group(2), "min_distance_pct": 0,
        })
    vwap = re.search(r"\b(?:price\s+)?(above|below)\s+(?:the\s+)?vwap\b", text)
    if vwap:
        _add(filters, "price_above_vwap" if vwap.group(1) == "above" else "price_below_vwap", {
            "min_distance_pct": 0,
        })
    if re.search(r"\bema alignment\b", text):
        _add(filters, "ema_alignment", {"direction": "bearish" if "bearish" in text else "bullish"})
    if re.search(r"\bema cross(?:over)?\b", text):
        _add(filters, "ema_crossover", {"direction": "bearish" if "bearish" in text else "bullish"})
    if re.search(r"\b(?:all|every|multi(?:ple)?)[ -]?timeframes?\s+(?:are\s+)?align", text):
        _add(filters, "mtf_alignment", {"min_timeframes": 3, "min_confidence": 0.5})

    # Relative strength.  The established parser handles "outperforming
    # QQQ"; this additionally accepts a clear underperformance request.
    underperform = re.search(r"\bunderperform(?:ing|ers?)?\s+(spy|qqq)\b", text)
    if underperform:
        _add(filters, "relative_strength_below", {"benchmark": underperform.group(1).upper(), "max_pct": -1})

    # Provider-backed catalyst criterion currently available to Scanner.
    earnings = re.search(r"\b(?:exclude|avoid|without|no)\s+(?:upcoming\s+)?earnings(?:\s+(?:within|in)\s+(\d+)\s+days?)?\b", text)
    if earnings:
        _add(filters, _EARNINGS_EXCLUSION_FILTER, {"days": earnings.group(1) or "7"})
    elif re.search(r"\b(?:news|positive|negative)\s+catalyst\b", text):
        unresolved.append("Scanner has an earnings-exclusion catalyst filter, but no verified news-catalyst filter yet.")

    # Live microstructure filters.
    tight_spread = re.search(r"\btight\s+spread(?:\s+(?:under|below|<=?)\s*(\d+(?:\.\d+)?)\s*bps?)?\b", text)
    if tight_spread:
        _add(filters, "tight_spread", {"max_spread_bps": float(tight_spread.group(1) or 10)})
    widening = re.search(r"\bspread widening(?:\s+(?:over|above|>=?)\s*(\d+(?:\.\d+)?)\s*bps?)?\b", text)
    if widening:
        _add(filters, "spread_widening", {"min_change_bps": float(widening.group(1) or 3)})
    pressure = re.search(r"\b(?:buy|sell)(?:-side)?\s+tape pressure\b|\btape pressure\s+(buy|sell)\b", text)
    if pressure:
        direction = next((value for value in pressure.groups() if value), "buy")
        _add(filters, "tape_pressure", {"direction": direction})
    large_prints = re.search(r"\blarge prints?(?:\s+(?:over|above|>=?)\s*(\d+))?\b", text)
    if large_prints:
        _add(filters, "large_print_activity", {"min_blocks": int(large_prints.group(1) or 1)})
    trade_rate = re.search(r"\btrade(?:-| )rate spike(?:\s+(?:over|above|>=?)\s*(\d+(?:\.\d+)?)\s*[x×])?\b", text)
    if trade_rate:
        _add(filters, "trade_rate_spike", {"min_acceleration": float(trade_rate.group(1) or 1.5)})
    imbalance = re.search(r"\b(bid|ask)(?:[/-]ask)?\s+(?:imbalance|heavy)(?:\s+(?:over|above|>=?)\s*(\d+(?:\.\d+)?))?\b", text)
    if imbalance:
        _add(filters, "bid_ask_imbalance", {
            "direction": imbalance.group(1), "min_imbalance": float(imbalance.group(2) or 0.2),
        })
    live_volume = re.search(r"\blive volume acceleration(?:\s+(?:over|above|>=?)\s*(\d+(?:\.\d+)?)\s*[x×])?\b", text)
    if live_volume:
        _add(filters, "live_volume_acceleration", {"min_acceleration": float(live_volume.group(1) or 1.5)})


def _describe(filters: list[ScannerFilterSpec], match: str) -> str:
    if not filters:
        return "No executable Scanner filters were recognized."
    connector = f" {match} "
    return connector.join(f"{item.type.replace('_', ' ')} ({', '.join(f'{key}={value}' for key, value in item.params.items()) or 'default'})" for item in filters)


def build_scanner_preview(
    query: str,
    *,
    scope: str = "watchlist",
    watchlist_id: int | None = None,
) -> ScannerBuilderPreview:
    """Build a safe, editable scanner preview without scanning symbols."""
    base = {"scope": scope, "watchlist_id": watchlist_id}
    # Scanner-specific phrases have a deterministic translation here. Avoid
    # spending an AI call merely because the older result-ranking schema has
    # no field for a BBO/session/earnings filter.
    if _SCANNER_ONLY_PHRASE.search(query):
        parsed = parse_query_rule_based(query, base=base)
        if parsed is None:
            schema, _extras, parser_used = NLFilters(scope=scope), None, "rules"
        else:
            schema, _extras = parsed
            parser_used = "rules"
    else:
        schema, _extras, parser_used = parse_query(query, base=base)
    filters: list[ScannerFilterSpec] = []
    unresolved: list[str] = []
    _append_schema_filters(filters, schema)
    _append_phrase_filters(filters, query, unresolved)

    match: Literal["AND", "OR"] = "OR" if re.search(r"\b(?:either|any of|or)\b", query, re.I) else "AND"
    if not filters:
        unresolved.append("No supported Scanner criterion was recognized; add a technical, session, earnings, or microstructure condition.")
    ambiguous = bool(unresolved) or parser_used == "ai" or match == "OR" or not filters
    return ScannerBuilderPreview(
        query=query,
        filters=filters,
        match=match,
        filter_description=_describe(filters, match),
        filter_schema=schema,
        parser_used=parser_used,
        ai_translation_used=parser_used == "ai",
        ambiguous=ambiguous,
        unresolved=list(dict.fromkeys(unresolved)),
    )


__all__ = ["ScannerBuilderPreview", "ScannerFilterSpec", "build_scanner_preview"]
