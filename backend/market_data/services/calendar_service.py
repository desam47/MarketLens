"""Cached provider-backed corporate-event lookups shared by UI and alerts."""

from datetime import date, timedelta
from threading import Lock
from time import monotonic

from backend.utils.timezone import now_ny

_CACHE_TTL_SECONDS = 15 * 60
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_cache_lock = Lock()


def _date_text(value: object) -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            return value[:10]
        value_date = value.date() if hasattr(value, "date") else value
        return value_date.isoformat() if isinstance(value_date, date) else None
    except (TypeError, ValueError):
        return None


def events_for_symbol(symbol: str) -> list[dict[str, str]]:
    """Return upcoming earnings and dividend events from Yahoo Finance.

    Results are deliberately plain dictionaries so this provider layer remains
    independent of FastAPI response models and can also serve alert evaluation.
    """
    symbol = symbol.upper()
    now = monotonic()
    with _cache_lock:
        cached = _cache.get(symbol)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
    try:
        import yfinance as yf

        calendar = yf.Ticker(symbol).calendar or {}
    except Exception:
        calendar = {}
    events: list[dict[str, str]] = []
    for field, event_type in (
        ("Earnings Date", "earnings"),
        ("Ex-Dividend Date", "ex_dividend"),
        ("Dividend Date", "dividend"),
    ):
        raw = calendar.get(field)
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        for value in values:
            event_date = _date_text(value)
            if event_date:
                events.append({"symbol": symbol, "event_type": event_type, "date": event_date})
                break
    today = now_ny().date()
    events = [
        event
        for event in events
        if today - timedelta(days=7) <= date.fromisoformat(event["date"]) <= today + timedelta(days=180)
    ]
    with _cache_lock:
        _cache[symbol] = (now, events)
    return events
