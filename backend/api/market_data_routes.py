"""
Market Data API Routes
Endpoints for controlling data ingestion and accessing historical data
"""
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config.settings import settings as _settings
from backend.database import get_db
from backend.models.market_data import Bar, MarketStatus, Quote

from backend.market_data.services.ingestion_service import ingestion_service
from backend.market_data.services.manager import _rate_limiter, _redis_cache, market_data_manager
from backend.api.ttl_cache import _quote_cache

router = APIRouter(
    prefix="/api/market-data",
    tags=["market-data"],
    responses={404: {"description": "Not found"}},
)

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value) or ""

class IngestionStatusResponse(BaseModel):
    is_running: bool
    symbols: list[str]
    watchlists: list[str]
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
    # start() sets is_running=True synchronously at the top, so the
    # response value is accurate even though the async loop hasn't yet
    # completed its first iteration.
    background_tasks.add_task(ingestion_service.start)
    return {"is_running": True, "message": "Ingestion service started"}

@router.get("/ingestion/status", response_model=IngestionStatusResponse)
async def get_ingestion_status():
    """Get the status of the ingestion service"""
    # get_tracking_watchlists() reads the DB — keep it off the event loop.
    watchlists = await asyncio.to_thread(ingestion_service.get_tracking_watchlists)
    # The ingestion thread mutates these dicts while it runs; iterating a
    # live dict from here can raise "dictionary changed size during
    # iteration". dict()/list() copy in a single C-level call, so snapshot
    # first and iterate the snapshot.
    quote_updates = dict(ingestion_service.last_quote_update)
    bar_updates = {sym: dict(tfs) for sym, tfs in list(ingestion_service.last_bar_update.items())}
    status_updates = dict(ingestion_service.last_status_update)
    return IngestionStatusResponse(
        is_running=ingestion_service.is_running,
        symbols=list(ingestion_service.symbols),
        watchlists=watchlists,
        timeframes=list(ingestion_service.timeframes),
        last_quote_updates={k: _to_dashboard_tz(v) if v != datetime.min else ""
                          for k, v in quote_updates.items()},
        last_bar_updates={symbol: {tf: _to_dashboard_tz(ts) if ts != datetime.min else ""
                                 for tf, ts in timeframes.items()}
                         for symbol, timeframes in bar_updates.items()},
        last_status_updates={k: _to_dashboard_tz(v) if v != datetime.min else ""
                           for k, v in status_updates.items()}
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


@router.post("/ingestion/symbols/refresh")
async def refresh_symbols_from_watchlist():
    """Reload the symbol list from the active watchlist.

    No longer required for a normal add/remove — the watchlist router
    itself now registers a newly-added symbol synchronously
    (``ingestion_service.register_symbol``) and calls this directly,
    in-process, on remove. This endpoint remains for out-of-band syncing
    (a watchlist changed by something other than the watchlist API) and
    manual resync. Note: a symbol found here that's new to the watchlist
    is registered for live tracking only — it does NOT enqueue a backfill
    job (that's the watchlist router's job at add time); see
    ``ingestion_service.register_symbol``'s docstring.
    """
    symbols = ingestion_service.refresh_symbols_from_watchlist()
    return {"message": f"Synced {len(symbols)} symbols from watchlist", "symbols": symbols}


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
    """Get the latest quote for a symbol.

    Wrapped in a 5s TTL cache (v2.1 Item 1.2) to reduce redundant
    database reads when the dashboard polls at a higher rate than the
    ingestion service updates.
    """
    key = symbol.upper()
    cached = _quote_cache.get(key)
    if cached is not None:
        return cached
    # Blocking SQLite read — run it in a worker thread so a cache miss
    # doesn't stall every other in-flight request on the event loop.
    quote = await asyncio.to_thread(ingestion_service.get_latest_quote, key)
    if quote is None:
        raise HTTPException(status_code=404, detail=f"No quote data found for {symbol}")
    _quote_cache[key] = quote
    return quote

@router.get("/quote/{symbol}/history", response_model=list[Quote])
async def get_quote_history(
    symbol: str,
    # Bounded: SQLite treats a negative LIMIT as "no limit", so an unchecked
    # value returned a symbol's entire quote history (measured: ~6k rows,
    # 2.2 MB, up to ~0.5 s) to whoever asked.
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Get historical quotes for a symbol (most recent ``limit``, max 1000)"""
    return await asyncio.to_thread(ingestion_service.get_quote_history, symbol.upper(), limit)

@router.get("/bar/{symbol}/{timeframe}", response_model=Bar)
async def get_latest_bar(symbol: str, timeframe: str, db: Session = Depends(get_db)):
    """Get the latest bar for a symbol and timeframe.

    Reads the Redis cache first (populated by the ingestion service) so
    repeated requests don't hit the database. Falls back to the database
    when Redis is empty or unavailable, then falls through to the
    provider chain as a last resort.
    """
    from backend.market_data.services.manager import market_data_manager

    # 1. Redis cache (fastest).
    cached = _redis_cache.get_latest_bar(symbol.upper(), timeframe)
    if cached is not None:
        return cached

    # 2. Database.
    bar = ingestion_service.get_latest_bar(symbol.upper(), timeframe)
    if bar is not None:
        return bar

    # 3. Provider chain (last resort — also populates the cache).
    try:
        # to_thread: get_latest_bar can make a blocking provider network
        # call here. This is an ``async def`` route handler running
        # directly on the server's event loop (unlike a plain ``def``
        # route, which FastAPI would offload to a thread pool
        # automatically) — a blocking call here stalls every other
        # concurrent request the server is handling, not just this one.
        return await asyncio.to_thread(
            market_data_manager.get_latest_bar, symbol.upper(), timeframe
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"No bar data found for {symbol} {timeframe}: {e}",
        )

@router.get("/bars/{symbol}", response_model=dict[str, Bar])
async def get_latest_bars(symbol: str, db: Session = Depends(get_db)):
    """Get latest bars for all timeframes for a symbol.

    Tries Redis cache first for each timeframe, then database, then provider
    chain — same three-tier fallback as the single-bar endpoint.
    """
    symbol = symbol.upper()
    bars = {}
    for timeframe in ingestion_service.timeframes:
        # 1. Redis cache.
        cached = _redis_cache.get_latest_bar(symbol, timeframe)
        if cached is not None:
            bars[timeframe] = cached
            continue

        # 2. Database.
        bar = ingestion_service.get_latest_bar(symbol, timeframe)
        if bar is not None:
            bars[timeframe] = bar
            continue

        # 3. Provider chain. to_thread: see get_latest_bar's comment above
        # — this is an async route handler, so a blocking provider call
        # here stalls every other concurrent request on the server.
        try:
            bars[timeframe] = await asyncio.to_thread(
                market_data_manager.get_latest_bar, symbol, timeframe
            )
        except Exception:
            # Don't fail the whole request if one timeframe is missing.
            pass
    return bars

@router.get("/status/{symbol}", response_model=MarketStatus)
async def get_market_status(symbol: str, db: Session = Depends(get_db)):
    """Get market status for a symbol"""
    try:
        # to_thread: get_market_status makes a blocking provider network
        # call — see get_latest_bar's comment above for why that matters
        # in an async route handler.
        status = await asyncio.to_thread(market_data_manager.get_market_status, symbol.upper())
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
