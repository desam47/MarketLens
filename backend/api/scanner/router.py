"""
API endpoints for the market scanner.

The scanner evaluates a single symbol (or a list of symbols pulled from a
watchlist) and returns indicator values, per-dimension scores, and the
composite total. Indicators are computed by the existing
``backend.scanner.scanner.market_scanner`` singleton — this router only
serializes the result; it does not run its own analysis.

A scan triggers live data fetches (YFinance), so endpoints here are
moderately expensive. There is no background cache — the scanner's
internal ``scan_results`` dict holds the last result per symbol for
subsequent quick reads.
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.repositories.watchlist_repository import WatchlistRepository
from backend.scanner.scanner import ScanResult, market_scanner

from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


# --- Response models ---------------------------------------------------

class _QuoteResponse(BaseModel):
    symbol: str
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    timestamp: str | None = None
    provider: str | None = None


class _ScanResultResponse(BaseModel):
    symbol: str
    timestamp: str
    quote: _QuoteResponse | None = None
    indicator_values: dict[str, Any]
    scores: dict[str, float]
    total_score: float
    rank: int | None = None
    signals: list[str]
    trend_signals: dict[str, Any]


# --- Helpers -----------------------------------------------------------

def _quote_to_dict(quote) -> _QuoteResponse | None:
    if quote is None:
        return None
    return _QuoteResponse(
        symbol=quote.symbol,
        price=getattr(quote, "price", None),
        bid=getattr(quote, "bid", None),
        ask=getattr(quote, "ask", None),
        volume=getattr(quote, "volume", None),
        timestamp=(
            quote.timestamp.isoformat() if getattr(quote, "timestamp", None) else None
        ),
        provider=getattr(quote, "provider", None),
    )


def _result_to_dict(result: ScanResult) -> _ScanResultResponse:
    """Serialize a ScanResult to a JSON-friendly shape.

    The scanner stores arbitrary Python objects in ``trend_signals`` (the
    trend engine's signal dict shape), so we leave that field as ``Any``
    and let Pydantic pass it through. Everything else is coerced to JSON-
    safe types.
    """
    return _ScanResultResponse(
        symbol=result.symbol,
        timestamp=result.timestamp.isoformat() if result.timestamp else "",
        quote=_quote_to_dict(result.quote),
        indicator_values=result.indicator_values or {},
        scores=result.scores or {},
        total_score=result.calculate_total_score(),
        rank=result.rank,
        signals=list(result.signals or []),
        trend_signals=result.trend_signals or {},
    )


# --- Endpoints ---------------------------------------------------------

@router.get("/{symbol}", response_model=_ScanResultResponse)
async def scan_symbol(symbol: str):
    """Scan a single symbol and return the full result.

    Runs the full scan pipeline: quote → indicators → scores → signals.
    For an uncached symbol this triggers live YFinance calls. The
    result is also stored in the scanner's internal cache (keyed by
    symbol) so subsequent reads via ``GET /api/scanner/{symbol}/cached``
    are cheap.
    """
    try:
        result = market_scanner.scan_symbol(symbol.upper())
        # Notify the alerts engine so signal_equals alerts can fire.
        from backend.alerts.engine import alerts_engine
        alerts_engine.evaluate_scan_result(result)
        return _result_to_dict(result)
    except Exception as e:
        logger.error(f"Error scanning symbol {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{symbol}/cached", response_model=_ScanResultResponse | None)
async def get_cached_scan(symbol: str):
    """Return the last scan for ``symbol`` without triggering a new one.

    Returns ``null`` if the symbol has never been scanned in this process
    (the cache is in-process and not persisted across restarts).
    """
    result = market_scanner.get_scan_result(symbol.upper())
    if result is None:
        return None
    return _result_to_dict(result)


@router.get("/signals/{symbol}", response_model=list[str])
async def get_signals_for_symbol(symbol: str):
    """Return the trading signals from the most recent scan of ``symbol``.

    Triggers a fresh scan if the symbol is not already in the cache.
    """
    result = market_scanner.get_scan_result(symbol.upper())
    if result is None:
        # First read for this symbol: scan it.
        try:
            result = market_scanner.scan_symbol(symbol.upper())
        except Exception as e:
            logger.error(f"Error scanning symbol {symbol}: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    return result.signals or []


class _RankedResponse(BaseModel):
    timestamp: str
    count: int
    results: list[_ScanResultResponse]


@router.get("/watchlist/{watchlist_id}", response_model=_RankedResponse)
async def scan_watchlist(watchlist_id: int, db: Session = Depends(get_db)):
    """Scan every enabled symbol in ``watchlist_id`` and return them ranked.

    Symbols are scanned sequentially (each makes its own YFinance calls).
    For large watchlists this will be slow — consider running a background
    scan and reading results via the ``/cached`` endpoint if that's a
    concern.
    """
    repo = WatchlistRepository(db)
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    watchlist_symbols = repo.get_watchlist_symbols(watchlist_id, enabled_only=True)
    if not watchlist_symbols:
        return _RankedResponse(
            timestamp="", count=0, results=[]
        )

    symbols = [ws.symbol for ws in watchlist_symbols]
    # ``scan_symbols`` populates self.scan_results and self.last_scan_time.
    market_scanner.scan_symbols(symbols)
    ranked = market_scanner.rank_symbols(symbols)

    # rank_symbols stores the result in scan_results with rank assigned.
    # Build the response in rank order, not scan order.
    by_symbol = {r.symbol.upper(): r for r in market_scanner.scan_results.values()}
    results: list[_ScanResultResponse] = []
    for sym, _score in ranked:
        result = by_symbol.get(sym.upper())
        if result is not None:
            results.append(_result_to_dict(result))

    last_scan = market_scanner.last_scan_time
    return _RankedResponse(
        timestamp=last_scan.isoformat() if last_scan else "",
        count=len(results),
        results=results,
    )


@router.get("/watchlist/{watchlist_id}/top", response_model=list[_ScanResultResponse])
async def scan_watchlist_top(
    watchlist_id: int,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """Scan the watchlist and return only the top N symbols by total score.

    Convenience wrapper around ``/watchlist/{id}`` for dashboards that
    only need a leaderboard view.
    """
    repo = WatchlistRepository(db)
    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    watchlist_symbols = repo.get_watchlist_symbols(watchlist_id, enabled_only=True)
    if not watchlist_symbols:
        return []

    symbols = [ws.symbol for ws in watchlist_symbols]
    market_scanner.scan_symbols(symbols)
    ranked = market_scanner.rank_symbols(symbols)

    by_symbol = {r.symbol.upper(): r for r in market_scanner.scan_results.values()}
    out: list[_ScanResultResponse] = []
    for sym, _score in ranked[: max(0, limit)]:
        result = by_symbol.get(sym.upper())
        if result is not None:
            out.append(_result_to_dict(result))
    return out
