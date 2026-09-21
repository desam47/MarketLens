"""Watchlist earnings and corporate-events calendar backed by Yahoo Finance."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_db
from backend.market_data.services.calendar_service import events_for_symbol
from backend.repositories.watchlist_repository import WatchlistRepository
from backend.utils.timezone import now_ny

router = APIRouter(prefix="/api/calendar", tags=["calendar"])
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


class SymbolCalendarResponse(BaseModel):
    symbol: str
    events: list[CalendarEvent]
    provider: str = "yfinance"
    timestamp: datetime


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
    events = [CalendarEvent(**event) for symbol in symbols for event in events_for_symbol(symbol)]
    events.sort(key=lambda event: (event.date, event.symbol, event.event_type))
    return WatchlistCalendarResponse(
        watchlist_id=watchlist_id,
        events=events,
        symbols_requested=len(symbols),
        symbols_with_events=len({event.symbol for event in events}),
        timestamp=now_ny(),
    )


@router.get("/symbol/{symbol}", response_model=SymbolCalendarResponse)
def get_symbol_calendar(symbol: str) -> SymbolCalendarResponse:
    symbol = symbol.upper()
    return SymbolCalendarResponse(
        symbol=symbol,
        events=[CalendarEvent(**event) for event in events_for_symbol(symbol)],
        timestamp=now_ny(),
    )
