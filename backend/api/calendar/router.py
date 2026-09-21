"""Watchlist earnings and corporate-events calendar backed by Yahoo Finance."""

from datetime import date, datetime, timedelta
from threading import Lock
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.repositories.watchlist_repository import WatchlistRepository
from backend.utils.timezone import now_ny

router = APIRouter(prefix="/api/calendar", tags=["calendar"])
_CACHE_TTL_SECONDS = 15 * 60
_cache: dict[str, tuple[float, list["CalendarEvent"]]] = {}
_cache_lock = Lock()


class CalendarEvent(BaseModel):
    symbol: str
    event_type: str
    date: str
    source: str = "yfinance"


class WatchlistCalendarResponse(BaseModel):
    watchlist_id: int
    events: list[CalendarEvent]
    symbols_requested: int
    symbols_with_events: int
    provider: str = "yfinance"
    timestamp: datetime


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


def _events_for_symbol(symbol: str) -> list[CalendarEvent]:
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
    events: list[CalendarEvent] = []
    field_types = (
        ("Earnings Date", "earnings"),
        ("Ex-Dividend Date", "ex_dividend"),
        ("Dividend Date", "dividend"),
    )
    for field, event_type in field_types:
        raw = calendar.get(field)
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        for value in values:
            event_date = _date_text(value)
            if event_date:
                events.append(CalendarEvent(symbol=symbol, event_type=event_type, date=event_date))
                break
    today = now_ny().date()
    events = [
        event
        for event in events
        if today - timedelta(days=7)
        <= date.fromisoformat(event.date)
        <= today + timedelta(days=180)
    ]
    with _cache_lock:
        _cache[symbol] = (now, events)
    return events


@router.get("/watchlist/{watchlist_id}", response_model=WatchlistCalendarResponse)
def get_watchlist_calendar(
    watchlist_id: int, db: Session = Depends(get_db)
) -> WatchlistCalendarResponse:
    repo = WatchlistRepository(db)
    if repo.get_watchlist(watchlist_id) is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    symbols = [
        row.symbol.upper() for row in repo.get_watchlist_symbols(watchlist_id, enabled_only=True)
    ]
    events = [event for symbol in symbols for event in _events_for_symbol(symbol)]
    events.sort(key=lambda event: (event.date, event.symbol, event.event_type))
    return WatchlistCalendarResponse(
        watchlist_id=watchlist_id,
        events=events,
        symbols_requested=len(symbols),
        symbols_with_events=len({event.symbol for event in events}),
        timestamp=now_ny(),
    )
