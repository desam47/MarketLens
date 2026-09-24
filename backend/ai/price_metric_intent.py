"""Recognize price-statistic questions for the get_price_statistics tool.

"TSLA max drawdown this year", "AAPL volatility over 30 days", "correlation
between AAPL and MSFT", "NVDA return from Jan 5 to Jan 20": each names a
ticker, a statistic, and (explicitly or by default) a window of daily
closes. Before this, those questions fell through to the calculator's
"what values should I use?" or to today's one-day change.

The parser is deliberately narrow. It needs a resolved ticker, and it steps
aside when the message carries its own price inputs (a calculator question)
or asks for implied volatility (an options question).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_CORRELATION = re.compile(r"\bcorrelat\w*\b", re.I)
_DRAWDOWN = re.compile(r"\bdraw[\s-]?downs?\b", re.I)
_VOLATILITY = re.compile(
    r"\b(?:historical\s+|realized\s+|realised\s+)?volatil(?:ity|e)\b|\bstandard\s+deviation\b", re.I
)
_IMPLIED = re.compile(r"\bimplied\b|\bIV\b|\boptions?\b", re.I)
_RETURN = re.compile(
    r"\b(?:returns?|returned|performance|performed|perform|percent(?:age)?\s+change|change|gain(?:ed)?|lost)\b"
    r"|%\s*change",
    re.I,
)
_MARKET_BENCHMARK = re.compile(r"\b(?:the\s+)?(?:market|s&p(?:\s*500)?|index)\b", re.I)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE = (
    rf"(?:{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r"|\d{4}-\d{2}-\d{2})"
)
_DATE_RANGE = re.compile(
    rf"\b(?:from|between)\s+(?P<start>{_DATE})\s+(?:to|and|through|until|-)\s+(?P<end>{_DATE})\b", re.I
)
_SINCE_DATE = re.compile(rf"\bsince\s+(?P<start>{_DATE})\b", re.I)
_YEAR_RANGE = re.compile(r"\b(?:from|between)\s+(?P<start>(?:19|20)\d{2})\s+(?:to|and|through|until|-)\s+(?P<end>(?:19|20)\d{2})\b", re.I)
_IN_YEAR = re.compile(r"\b(?:in|for|during|of)\s+(?P<year>(?:19|20)\d{2})\b", re.I)
_YTD = re.compile(r"\b(?:ytd|year[\s-]to[\s-]date|this\s+year)\b", re.I)
_TRAILING_YEAR = re.compile(r"\b(?:over|in|during|for)?\s*the\s+(?:last|past|trailing)\s+year\b", re.I)
_LAST_YEAR = re.compile(r"\blast\s+year\b", re.I)
_LOOKBACK = re.compile(
    r"\b(?:(?:over|in|during|for)\s+)?(?:the\s+)?(?:(?:last|past|trailing)\s+)?"
    r"(?P<n>\d{1,4})[\s-]?(?P<unit>d|days?|w|weeks?|mo|months?|y|years?)\b",
    re.I,
)
_LAST_UNIT = re.compile(r"\b(?:the\s+)?(?:last|past|trailing)\s+(?P<unit>week|month|quarter)\b", re.I)
_PRICE_INPUT = re.compile(r"\$\s*\d|\b\d+\.\d+\b")
_PLAIN_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")

_DAYS_PER_UNIT = {"d": 1, "w": 7, "mo": 30, "y": 365}


@dataclass(frozen=True)
class PriceMetricIntent:
    metric: str
    symbol: str
    comparison_symbol: str | None
    start: date | None
    end: date | None
    lookback_days: int | None

    def tool_arguments(self) -> dict[str, object]:
        arguments: dict[str, object] = {"symbol": self.symbol, "metric": self.metric}
        if self.comparison_symbol:
            arguments["comparison_symbol"] = self.comparison_symbol
        if self.start is not None:
            arguments["start"] = self.start.isoformat()
        if self.end is not None:
            arguments["end"] = self.end.isoformat()
        if self.lookback_days is not None:
            arguments["lookback_days"] = self.lookback_days
        return arguments


def _parse_date(text: str, today: date) -> date | None:
    text = text.strip().lower().replace(",", "")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text)
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
        if match:
            month, day, year = int(match.group(1)), int(match.group(2)), match.group(3)
        else:
            match = re.fullmatch(rf"({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+(\d{{4}}))?", text)
            if not match:
                return None
            month, day, year = _MONTHS[match.group(1)[:3]], int(match.group(2)), match.group(3)
        if year is None:
            # No year: the most recent such date that is not in the future.
            candidate = date(today.year, month, day)
            return candidate if candidate <= today else date(today.year - 1, month, day)
        year_value = int(year)
        if year_value < 100:
            year_value += 2000
        return date(year_value, month, day)
    except ValueError:
        return None


@dataclass(frozen=True)
class _Window:
    start: date | None
    end: date | None
    lookback_days: int | None
    span: tuple[int, int] | None  # where it was written, so its numbers aren't read as prices


def _window(text: str, today: date) -> _Window | str | None:
    """The window a message names; a string is a clarification to send."""
    match = _DATE_RANGE.search(text)
    if match:
        start, end = _parse_date(match.group("start"), today), _parse_date(match.group("end"), today)
        if start is None or end is None:
            return "I couldn't read one of those dates. Please give them like Jan 5 2026 or 2026-01-05."
        if start > end:
            return "That start date is after the end date — which window do you mean?"
        return _Window(start, end, None, match.span())
    match = _SINCE_DATE.search(text)
    if match:
        start = _parse_date(match.group("start"), today)
        if start is None:
            return "I couldn't read that date. Please give it like Jan 5 2026 or 2026-01-05."
        return _Window(start, today, None, match.span())
    match = _YEAR_RANGE.search(text)
    if match:
        first, last = int(match.group("start")), int(match.group("end"))
        if first > last or last > today.year:
            return "Which years do you mean? The range has to end this year or earlier."
        return _Window(date(first, 1, 1), min(date(last, 12, 31), today), None, match.span())
    match = _YTD.search(text)
    if match:
        return _Window(date(today.year, 1, 1), today, None, match.span())
    match = _TRAILING_YEAR.search(text)
    if match:
        return _Window(None, None, 365, match.span())
    match = _LAST_YEAR.search(text)
    if match:
        return _Window(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), None, match.span())
    match = _IN_YEAR.search(text)
    if match:
        year = int(match.group("year"))
        if year > today.year:
            return f"{year} hasn't happened yet — which window do you mean?"
        return _Window(date(year, 1, 1), min(date(year, 12, 31), today), None, match.span())
    match = _LOOKBACK.search(text)
    if match:
        unit = match.group("unit").lower()
        key = "mo" if unit.startswith("mo") else unit[0]
        days = int(match.group("n")) * _DAYS_PER_UNIT[key]
        if not 2 <= days <= 1826:
            return "I can compute these over 2 days to 5 years of daily history — which window do you mean?"
        return _Window(None, None, days, match.span())
    match = _LAST_UNIT.search(text)
    if match:
        days = {"week": 7, "month": 30, "quarter": 91}[match.group("unit").lower()]
        return _Window(None, None, days, match.span())
    return None


def parse_price_metric_intent(
    text: str, focus_symbols: list[str], today: date
) -> PriceMetricIntent | str | None:
    """A price-statistic request, a clarification to ask, or ``None``.

    ``None`` means "not this kind of question": the caller's other routes
    (and the calculator, for messages with their own price inputs) handle it.
    """
    if not focus_symbols:
        return None
    if _CORRELATION.search(text):
        metric = "correlation"
    elif _DRAWDOWN.search(text):
        metric = "max_drawdown"
    elif _VOLATILITY.search(text):
        if _IMPLIED.search(text):
            return None  # implied volatility is an options question
        metric = "volatility"
    elif _RETURN.search(text):
        metric = "return_percent"
    else:
        return None

    window = _window(text, today)
    if isinstance(window, str):
        return window
    # Prices the trader typed ("from $200 to $150") mean a calculation, not a
    # lookup; the window's own numbers (dates, "30 days") don't count.
    remainder = text if window is None or window.span is None else text[: window.span[0]] + text[window.span[1] :]
    if _PRICE_INPUT.search(remainder) or len(_PLAIN_NUMBER.findall(remainder)) >= 2:
        return None
    if metric == "return_percent" and window is None:
        return None  # "AAPL's return" with no window is today's change, routed elsewhere

    symbols = list(dict.fromkeys(symbol.upper() for symbol in focus_symbols))
    if metric == "correlation":
        if len(symbols) == 1 and _MARKET_BENCHMARK.search(text):
            symbols.append("SPY")
        if len(symbols) != 2:
            if len(symbols) == 1:
                return f"Which symbol should I correlate {symbols[0]} with?"
            return "Correlation compares two symbols — which two do you mean?"
        comparison = symbols[1]
    else:
        if len(symbols) != 1:
            return None  # several tickers: a comparison, handled by compare_symbols
        comparison = None

    return PriceMetricIntent(
        metric=metric,
        symbol=symbols[0],
        comparison_symbol=comparison,
        start=window.start if window else None,
        end=window.end if window else None,
        lookback_days=window.lookback_days if window else None,
    )


__all__ = ["PriceMetricIntent", "parse_price_metric_intent"]
