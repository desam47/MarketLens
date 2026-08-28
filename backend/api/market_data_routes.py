"""
Market Data API Routes
Endpoints for controlling data ingestion and accessing historical data
"""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config.settings import settings as _settings
from backend.database import get_db
from backend.models.market_data import Bar, MarketStatus, Quote

from ..market_data.services.ingestion_service import ingestion_service
from ..market_data.services.manager import _rate_limiter, market_data_manager

router = APIRouter(
    prefix="/api/market-data",
    tags=["market-data"],
    responses={404: {"description": "Not found"}},
)

class IngestionStatusResponse(BaseModel):
    is_running: bool
    symbols: list[str]
    timeframes: list[str]
    last_quote_updates: dict[str, str]
    last_bar_updates: dict[str, dict[str, str]]
    last_status_updates: dict[str, str]

class SymbolRequest(BaseModel):
    symbol: str

class SymbolsRequest(BaseModel):
    symbols: list[str]

class TimeframesRequest(BaseModel):
    timeframes: list[str]

@router.post("/ingestion/start")
async def start_ingestion(background_tasks: BackgroundTasks):
    """Start the market data ingestion service"""
    if not ingestion_service.is_running:
        background_tasks.add_task(ingestion_service.start)
        return {"message": "Market data ingestion service started"}
    else:
        return {"message": "Ingestion service is already running"}

@router.post("/ingestion/stop")
async def stop_ingestion():
    """Stop the market data ingestion service"""
    ingestion_service.stop()
    return {"message": "Market data ingestion service stopped"}

@router.post("/ingestion/toggle")
async def toggle_ingestion(background_tasks: BackgroundTasks):
    """Flip the ingestion service: stop if running, start if not.

    Returns the new state so the caller can update its UI without a
    follow-up ``GET /ingestion/status`` call.
    """
    if ingestion_service.is_running:
        ingestion_service.stop()
        return {"is_running": False, "message": "Ingestion service stopped"}
    # BackgroundTasks.add_task handles sync callables correctly — see
    # ingestion_service.start() implementation (daemon thread pattern).
    background_tasks.add_task(ingestion_service.start)
    return {"is_running": True, "message": "Ingestion service started"}

@router.get("/ingestion/status", response_model=IngestionStatusResponse)
async def get_ingestion_status():
    """Get the status of the ingestion service"""
    return IngestionStatusResponse(
        is_running=ingestion_service.is_running,
        symbols=ingestion_service.symbols,
        timeframes=ingestion_service.timeframes,
        last_quote_updates={k: v.isoformat() if v != datetime.min else ""
                          for k, v in ingestion_service.last_quote_update.items()},
        last_bar_updates={symbol: {tf: ts.isoformat() if ts != datetime.min else ""
                                 for tf, ts in timeframes.items()}
                         for symbol, timeframes in ingestion_service.last_bar_update.items()},
        last_status_updates={k: v.isoformat() if v != datetime.min else ""
                           for k, v in ingestion_service.last_status_update.items()}
    )

@router.post("/ingestion/symbols")
async def update_symbols(request: SymbolsRequest):
    """Update the list of symbols to ingest"""
    ingestion_service.symbols = request.symbols
    # Reset tracking for new symbols
    for symbol in request.symbols:
        if symbol not in ingestion_service.last_quote_update:
            ingestion_service.last_quote_update[symbol] = datetime.min
            ingestion_service.last_status_update[symbol] = datetime.min
            ingestion_service.last_bar_update[symbol] = {tf: datetime.min for tf in ingestion_service.timeframes}
    return {"message": f"Updated symbols to: {request.symbols}"}

@router.post("/ingestion/timeframes")
async def update_timeframes(request: TimeframesRequest):
    """Update the list of timeframes to ingest"""
    ingestion_service.timeframes = request.timeframes
    # Reset bar tracking for new timeframes
    for symbol in ingestion_service.symbols:
        if symbol not in ingestion_service.last_bar_update:
            ingestion_service.last_bar_update[symbol] = {}
        for tf in request.timeframes:
            if tf not in ingestion_service.last_bar_update[symbol]:
                ingestion_service.last_bar_update[symbol][tf] = datetime.min
    return {"message": f"Updated timeframes to: {request.timeframes}"}

@router.get("/quote/{symbol}", response_model=Quote)
async def get_latest_quote(symbol: str, db: Session = Depends(get_db)):
    """Get the latest quote for a symbol"""
    quote = ingestion_service.get_latest_quote(symbol.upper())
    if quote is None:
        raise HTTPException(status_code=404, detail=f"No quote data found for {symbol}")
    return quote

@router.get("/quote/{symbol}/history", response_model=list[Quote])
async def get_quote_history(symbol: str, limit: int = 100, db: Session = Depends(get_db)):
    """Get historical quotes for a symbol"""
    quotes = ingestion_service.get_quote_history(symbol.upper(), limit)
    return quotes

@router.get("/bar/{symbol}/{timeframe}", response_model=Bar)
async def get_latest_bar(symbol: str, timeframe: str, db: Session = Depends(get_db)):
    """Get the latest bar for a symbol and timeframe"""
    bar = ingestion_service.get_latest_bar(symbol.upper(), timeframe)
    if bar is None:
        raise HTTPException(status_code=404, detail=f"No bar data found for {symbol} {timeframe}")
    return bar

@router.get("/bars/{symbol}", response_model=dict[str, Bar])
async def get_latest_bars(symbol: str, db: Session = Depends(get_db)):
    """Get latest bars for all timeframes for a symbol"""
    symbol = symbol.upper()
    bars = {}
    for timeframe in ingestion_service.timeframes:
        bar = ingestion_service.get_latest_bar(symbol, timeframe)
        if bar:
            bars[timeframe] = bar
    return bars

@router.get("/status/{symbol}", response_model=MarketStatus)
async def get_market_status(symbol: str, db: Session = Depends(get_db)):
    """Get market status for a symbol"""
    try:
        status = market_data_manager.get_market_status(symbol.upper())
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get market status: {e!s}") from e


@router.get("/providers", response_model=None)
async def get_providers():
    """List all registered providers with their current health and rate-limiter stats.

    Returns each provider's ``ProviderStatus`` (healthy / last error / request
    count) plus, in a side-channel ``_rate_limit`` field, the number of calls
    that were throttled by the per-provider rate limiter. The side-channel
    field is prefixed with ``_`` so it doesn't collide with provider names.

    ``response_model=None`` because the response is a heterogeneous dict
    (provider statuses + rate-limit metadata); declaring ``ProviderStatus``
    as the response model would reject the metadata field.
    """
    statuses = market_data_manager.get_provider_statuses()
    result: dict = {name: status.model_dump() for name, status in statuses.items()}
    result["_rate_limit"] = {
        "throttled_calls": _rate_limiter.stats(),
        "limit_per_minute": _settings.market_data.rate_limit_per_minute,
    }
    return result
