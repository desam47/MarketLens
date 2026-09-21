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

import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.ttl_cache import _scan_cache, ttl_cached
from backend.market_data.services.calendar_service import events_for_symbol
from backend.repositories.watchlist_repository import WatchlistRepository
from backend.utils.timezone import now_ny

from ...market_data.services.manager import market_data_manager
from ...scanner.filters import (
    AndFilter,
    OrFilter,
    TrueFilter,
    default_registry,
)
from ...scanner.ranking import RankingEngine, default_ranking_engine
from ...scanner.scanner import ScanResult, market_scanner
from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")
_EARNINGS_EXCLUSION_FILTER = "exclude_earnings_within_days"


def _to_dashboard_tz(value: datetime | None) -> str:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value) or ""


# --- Response models ---------------------------------------------------


class _QuoteResponse(BaseModel):
    symbol: str
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    timestamp: str | None = None
    provider: str | None = None
    data_status: str | None = None


class _ScanResultResponse(BaseModel):
    symbol: str
    timestamp: str
    quote: _QuoteResponse | None = None
    change: float | None = None
    change_pct: float | None = None
    indicator_values: dict[str, Any]
    scores: dict[str, float]
    total_score: float
    rank: int | None = None
    signals: list[str]
    trend_signals: dict[str, Any]
    explanation: dict[str, Any] = Field(default_factory=dict)
    # True when the symbol's watchlist row is enabled. Surfaced in the UI so
    # disabled rows can still be shown (greyed out) and re-enabled from the
    # watchlist table. Defaults to True for non-watchlist scan endpoints
    # (``/top-movers``, ``/scan``) where the concept doesn't apply.
    is_enabled: bool = True
    # Entity classification from the watchlist row: "stock" or "etf".
    entity_type: str | None = "stock"


class _RankedResponse(BaseModel):
    timestamp: str
    count: int
    results: list[_ScanResultResponse]


class _RankedEntryResponse(BaseModel):
    symbol: str
    score: float
    rank: int
    metrics: dict[str, Any] = Field(default_factory=dict)


class _NamedRankingResponse(BaseModel):
    name: str
    label: str
    description: str
    total_eligible: int
    entries: list[_RankedEntryResponse]


class _FilterRequest(BaseModel):
    """A single filter expression.

    Example::

        {"type": "daily_bullish", "params": {"min_confidence": 0.6}}
    """

    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class _FilterBody(BaseModel):
    """Request body for /filter and /rankings endpoints."""

    filters: list[_FilterRequest] = Field(default_factory=list)
    match: str = Field(default="AND", description="AND or OR")


# --- Helpers -----------------------------------------------------------


def _quote_to_dict(quote) -> _QuoteResponse | None:
    if quote is None:
        return None
    data_status = getattr(quote, "data_status", None)
    return _QuoteResponse(
        symbol=quote.symbol,
        price=getattr(quote, "price", None),
        bid=getattr(quote, "bid", None),
        ask=getattr(quote, "ask", None),
        volume=getattr(quote, "volume", None),
        timestamp=_to_dashboard_tz(getattr(quote, "timestamp", None)) or None,
        provider=getattr(quote, "provider", None),
        data_status=str(data_status) if data_status is not None else None,
    )


def _result_to_dict(
    result: ScanResult,
    historical_performance: dict[str, Any] | None = None,
) -> _ScanResultResponse:
    """Serialize a ScanResult to a JSON-friendly shape.

    The scanner stores arbitrary Python objects in ``trend_signals`` (the
    trend engine's signal dict shape), so we leave that field as ``Any``
    and let Pydantic pass it through. Everything else is coerced to JSON-
    safe types.

    ``total_score`` is the *signed* weighted average so the dashboard can
    separate bullish (positive) from bearish (negative) entries.
    """
    explanation = dict(getattr(result, "explanation", None) or {})
    if historical_performance is not None:
        explanation["historical_performance"] = historical_performance

    return _ScanResultResponse(
        symbol=result.symbol,
        timestamp=_to_dashboard_tz(result.timestamp),
        quote=_quote_to_dict(result.quote),
        change=result.change,
        change_pct=result.change_pct,
        indicator_values=result.indicator_values or {},
        scores=result.scores or {},
        total_score=result.calculate_signed_total_score(),
        rank=result.rank,
        signals=list(result.signals or []),
        trend_signals=result.trend_signals or {},
        explanation=explanation,
        is_enabled=getattr(result, "is_enabled", True),
    )


def _result_to_lite_dict(result: ScanResult) -> _ScanResultResponse:
    """Lightweight serialization for the WebSocket live stream.

    The ScannerPage table only displays symbol, price, total_score,
    trend directions, signals, and timestamp. The full ``_result_to_dict``
    payload also ships per-indicator values, per-dimension scores, and
    rank — ~20 extra JSON fields per symbol that the live table never
    reads. This omits those to cut per-message payload roughly in half.
    """
    rs = result.trend_signals or {}
    trend_lite: dict[str, Any] = {}
    for tf, sig in rs.items():
        if isinstance(sig, dict):
            trend_lite[tf] = {
                "direction": sig.get("direction"),
                "confidence": sig.get("confidence"),
            }
        else:
            trend_lite[tf] = sig

    return _ScanResultResponse(
        symbol=result.symbol,
        timestamp=_to_dashboard_tz(result.timestamp),
        quote=_quote_to_dict(result.quote),
        change=result.change,
        change_pct=result.change_pct,
        indicator_values={},
        scores={},
        total_score=result.calculate_signed_total_score(),
        rank=None,
        signals=list(result.signals or []),
        trend_signals=trend_lite,
        explanation=getattr(result, "explanation", None) or {},
        is_enabled=getattr(result, "is_enabled", True),
    )


def _build_filter(filters: list[_FilterRequest], match: str):
    """Build a Filter expression from a request body.

    Empty filter list returns ``TrueFilter`` — a genuine match-all. This
    used to be ``DailyBullish(min_confidence=-1.0)``, on the mistaken
    belief that disabling the confidence floor made it match everything:
    ``TimeframeDirection.matches()`` still requires
    ``direction == "uptrend"`` regardless of confidence, so "match all"
    silently excluded every symbol whose daily trend was not literally an
    uptrend (found live 2026-09-13: ``{"filters": []}`` matched 2 of the
    10 cached symbols where ``{"filters": [{"type": "true"}]}`` matched
    all 10). ``TrueFilter`` is the same fix NL search already made for
    this exact bug — see ``filters.py``.

    Unknown filter types are surfaced as ``HTTPException(400)`` so the
    client sees a 4xx rather than an opaque 500.
    """
    if not filters:
        return TrueFilter()
    try:
        built = [default_registry.build(f.model_dump()) for f in filters]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if match.upper() == "OR":
        return OrFilter(built)
    return AndFilter(built)


def _split_earnings_exclusion(filters: list[_FilterRequest]) -> tuple[list[_FilterRequest], int | None]:
    """Extract the scanner's provider-backed earnings exclusion constraint."""
    standard: list[_FilterRequest] = []
    days: int | None = None
    for item in filters:
        if item.type.lower() != _EARNINGS_EXCLUSION_FILTER:
            standard.append(item)
            continue
        try:
            value = int(item.params.get("days", 7))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Earnings exclusion days must be an integer") from exc
        if not 0 <= value <= 180:
            raise HTTPException(status_code=400, detail="Earnings exclusion days must be between 0 and 180")
        days = value if days is None else min(days, value)
    return standard, days


def _without_upcoming_earnings(results: list[ScanResult], days: int) -> list[ScanResult]:
    today = now_ny().date()
    included: list[ScanResult] = []
    for result in results:
        has_upcoming_earnings = False
        for event in events_for_symbol(result.symbol):
            if event.get("event_type") != "earnings" or not event.get("date"):
                continue
            try:
                days_until = (datetime.fromisoformat(event["date"]).date() - today).days
            except ValueError:
                continue
            if 0 <= days_until <= days:
                has_upcoming_earnings = True
                break
        if not has_upcoming_earnings:
            included.append(result)
    return included


def _scoped_cache(symbols: list[str] | None) -> list[ScanResult]:
    """Return cached scan results, narrowed to ``symbols`` when given.

    ``market_scanner.scan_results`` is a module-global that accumulates
    every symbol ever scanned. Reading it directly makes an endpoint that
    advertises a ``symbols`` scope silently rank/filter over the whole
    universe instead (found live 2026-09-13: ``POST /api/scanner/rankings``
    with ``symbols=["SPY"]`` still reported ``total_eligible`` for all 10
    cached symbols). Every endpoint that accepts a symbol scope must read
    the cache through here.
    """
    cache = list(market_scanner.scan_results.values())
    if symbols:
        wanted = {s.upper() for s in symbols}
        cache = [r for r in cache if r.symbol.upper() in wanted]
    return cache


async def _ensure_symbols_scanned(symbols: list[str]) -> None:
    """Scan symbols that are not yet in the scanner cache.

    Replaces the previous pattern of unconditionally calling
    ``scan_symbols_async`` for every symbol on every rankings/filter
    request. The WebSocket dispatcher already scans subscribed symbols
    on a 30-second cadence, so most symbols will already have a cached
    result — re-scanning them here is redundant work that doubles the
    backend load and slows down the sidebar.

    Only the *missing* symbols (never scanned) are fetched here.
    """
    missing = [s for s in symbols if s.upper() not in market_scanner.scan_results]
    if missing:
        await market_scanner.scan_symbols_async(missing)


def _serialize_named_ranking(rr) -> _NamedRankingResponse:
    return _NamedRankingResponse(
        name=rr.name,
        label=rr.label,
        description=rr.description,
        total_eligible=rr.total_eligible,
        entries=[
            _RankedEntryResponse(
                symbol=e.symbol,
                score=e.score,
                rank=e.rank,
                metrics=e.metrics,
            )
            for e in rr.entries
        ],
    )


def _empty_rankings(engine: RankingEngine) -> list[_NamedRankingResponse]:
    return [
        _NamedRankingResponse(
            name=meta["name"],
            label=meta["label"],
            description=meta["description"],
            total_eligible=0,
            entries=[],
        )
        for meta in engine.CATEGORIES
    ]


# --- Phase 10: composable filters + named rankings ----------------------
# IMPORTANT: these literal-path routes MUST be registered before any
# ``/{symbol}`` path-parameter route, otherwise FastAPI will route
# ``/filter-types`` to the symbol-scanner endpoint and try to scan
# a stock called "filter-types".


@router.get("/filter-types", response_model=list[str])
async def list_filter_types():
    """List the filter ``type`` strings accepted by ``POST /api/scanner/filter``."""
    return [*default_registry.list_types(), _EARNINGS_EXCLUSION_FILTER]


@router.post("/filter", response_model=list[_ScanResultResponse])
async def filter_scan_results(
    filter_body: _FilterBody = Body(...),
    symbols: list[str] | None = Query(default=None),
):
    """Run the named filter against the scanner's current cache.

    If ``symbols`` is provided, those symbols are scanned first (so the
    response reflects the latest data) and the result set is scoped to
    them. Otherwise every cached symbol is eligible.
    """
    standard_filters, earnings_exclusion_days = _split_earnings_exclusion(filter_body.filters)
    f = _build_filter(standard_filters, filter_body.match)

    if symbols:
        # Only scan symbols that aren't already cached — avoids duplicating
        # work the WebSocket dispatcher is already doing every 30s.
        upper_symbols = [s.upper() for s in symbols]
        await _ensure_symbols_scanned(upper_symbols)

    cache = _scoped_cache(upper_symbols if symbols else None)
    if not cache:
        return []

    matched = [r for r in cache if f.matches(r)]
    if earnings_exclusion_days is not None:
        matched = await asyncio.to_thread(_without_upcoming_earnings, matched, earnings_exclusion_days)
    return [_result_to_dict(r) for r in matched]


@router.post("/rankings", response_model=list[_NamedRankingResponse])
async def get_named_rankings(
    filter_body: _FilterBody = Body(...),
    top_n: int = Query(default=10),
    symbols: list[str] | None = Query(default=None),
    engine: RankingEngine = Depends(lambda: default_ranking_engine),
):
    """Compute named rankings (Strongest Bullish, etc.) over the cache.

    An optional filter narrows the candidate set before ranking, and an
    optional ``symbols`` scope narrows it to those symbols only.
    """
    standard_filters, earnings_exclusion_days = _split_earnings_exclusion(filter_body.filters)
    f = _build_filter(standard_filters, filter_body.match)

    if symbols:
        # Only scan symbols that aren't already cached — avoids duplicating
        # work the WebSocket dispatcher is already doing every 30s.
        upper_symbols = [s.upper() for s in symbols]
        await _ensure_symbols_scanned(upper_symbols)

    cache = _scoped_cache(upper_symbols if symbols else None)
    if not cache:
        return _empty_rankings(engine)

    candidates = (
        await asyncio.to_thread(_without_upcoming_earnings, cache, earnings_exclusion_days)
        if earnings_exclusion_days is not None
        else cache
    )
    ranked = engine.rank(candidates, top_n=top_n, filter=f)
    return [_serialize_named_ranking(rr) for rr in ranked.values()]


@router.get("/rankings/categories", response_model=list[dict[str, str]])
async def list_ranking_categories():
    """List the named ranking categories available."""
    return default_ranking_engine.CATEGORIES


def _resolve_watchlist_symbols(repo: WatchlistRepository, watchlist_id: int | None) -> list[str]:
    """Resolve the symbol scope for a top-movers scan.

    Shared by ``/top-movers`` and ``/top-movers/combined`` so both scan
    the exact same watchlist scope. Raises 404 for an unknown
    ``watchlist_id``; returns an empty list (not a 404) when
    ``watchlist_id`` is omitted/0 and there are no active watchlists.
    """
    if watchlist_id is None or watchlist_id == 0:
        # Scan across **all** active watchlists
        all_wls = repo.get_watchlists(active_only=True)
        symbols_set: set[str] = set()
        for wl in all_wls:
            symbols = repo.get_watchlist_symbols(wl.id, enabled_only=True)
            # each element in `symbols` is a WatchlistSymbol instance
            symbols_set.update(ws.symbol for ws in symbols)
        return list(symbols_set)

    watchlist = repo.get_watchlist(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    watchlist_symbols = repo.get_watchlist_symbols(watchlist_id, enabled_only=True)
    return [ws if isinstance(ws, str) else str(ws.symbol) for ws in watchlist_symbols]


def _rank_entries(
    named: dict, by_symbol: dict[str, ScanResult], ranking_key: str
) -> list[_ScanResultResponse]:
    target = named.get(ranking_key)
    if not target:
        return []
    out: list[_ScanResultResponse] = []
    for entry in target.entries:
        result = by_symbol.get(entry.symbol.upper())
        if result is not None:
            out.append(_result_to_dict(result))
    return out


@router.get("/top-movers", response_model=list[_ScanResultResponse])
async def get_top_movers(
    direction: str = Query("bullish", pattern="^(bullish|bearish)$"),
    limit: int = Query(10, ge=0, le=50),
    watchlist_id: int | None = Query(
        None, description="Watchlist to scan (defaults to first active)"
    ),
    db: Session = Depends(get_db),
):
    """Return the top N strongest-bullish or strongest-bearish symbols.

    Used by the dashboard's "Top Bullish" and "Top Bearish" cards. When
    ``watchlist_id`` is omitted, scans the first active watchlist.

    Prefer ``/top-movers/combined`` when both directions are needed —
    calling this endpoint twice (once per direction) scans the same
    watchlist twice for no benefit, since ``default_ranking_engine.rank()``
    already computes both directions in a single pass.
    """
    repo = WatchlistRepository(db)
    symbols = await asyncio.to_thread(_resolve_watchlist_symbols, repo, watchlist_id)
    if not symbols:
        return []

    await market_scanner.scan_symbols_async(symbols)

    cache = _scoped_cache(symbols)
    ranking_key = "strongest_bullish" if direction == "bullish" else "strongest_bearish"
    named = default_ranking_engine.rank(cache, top_n=limit)
    by_symbol = {r.symbol.upper(): r for r in cache}
    return _rank_entries(named, by_symbol, ranking_key)


class _TopMoversCombinedResponse(BaseModel):
    bullish: list[_ScanResultResponse]
    bearish: list[_ScanResultResponse]


@router.get("/top-movers/combined", response_model=_TopMoversCombinedResponse)
async def get_top_movers_combined(
    limit: int = Query(10, ge=0, le=50),
    watchlist_id: int | None = Query(
        None, description="Watchlist to scan (defaults to first active)"
    ),
    db: Session = Depends(get_db),
):
    """Return strongest-bullish and strongest-bearish symbols from one scan.

    Scans the watchlist once and ranks once, then reads both directions
    out of that single ranking pass — replaces two ``/top-movers`` calls
    (one per direction) from the dashboard's Top Movers card, which scanned
    the same watchlist twice.
    """
    repo = WatchlistRepository(db)
    symbols = await asyncio.to_thread(_resolve_watchlist_symbols, repo, watchlist_id)
    if not symbols:
        return _TopMoversCombinedResponse(bullish=[], bearish=[])

    await market_scanner.scan_symbols_async(symbols)

    cache = _scoped_cache(symbols)
    named = default_ranking_engine.rank(cache, top_n=limit)
    by_symbol = {r.symbol.upper(): r for r in cache}
    return _TopMoversCombinedResponse(
        bullish=_rank_entries(named, by_symbol, "strongest_bullish"),
        bearish=_rank_entries(named, by_symbol, "strongest_bearish"),
    )


@router.get("/watchlist/{watchlist_id}/rankings", response_model=list[_NamedRankingResponse])
async def get_watchlist_rankings(
    watchlist_id: int,
    top_n: int = 10,
    db: Session = Depends(get_db),
    engine: RankingEngine = Depends(lambda: default_ranking_engine),
):
    """Scan a watchlist and return named rankings for the enabled symbols.

    Convenience wrapper that combines ``/watchlist/{id}`` with the
    ranking engine — useful for the dashboard's "Strongest Bullish
    Today" cards.
    """
    repo = WatchlistRepository(db)
    watchlist = await asyncio.to_thread(repo.get_watchlist, watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    watchlist_symbols = await asyncio.to_thread(
        repo.get_watchlist_symbols, watchlist_id, enabled_only=True
    )
    if not watchlist_symbols:
        return _empty_rankings(engine)

    symbols = [ws if isinstance(ws, str) else str(ws.symbol) for ws in watchlist_symbols]
    await market_scanner.scan_symbols_async(symbols)

    cache = _scoped_cache(symbols)
    ranked = engine.rank(cache, top_n=top_n)
    return [_serialize_named_ranking(rr) for rr in ranked.values()]


# --- Symbol-level endpoints -------------------------------------------


async def _scan_and_notify(symbol: str) -> _ScanResultResponse:
    """Run the full scan pipeline and notify the alerts engine.

    Extracted so it can be wrapped by ``ttl_cached`` — the cache
    stores the serialised response dict, not the raw ``ScanResult``.
    The alerts engine is notified on every real scan (not on cache hits).

    Fetches historical_bars/quote first, same as scan_symbols' batch
    path — scan_symbol() computes change/change_pct from historical_bars
    (see Scanner._compute_change), and without them this endpoint always
    returned change_pct=None. That also poisoned the shared
    market_scanner.scan_results cache other endpoints (filter, rankings)
    read via _scoped_cache without necessarily re-scanning first, since
    this and the batch path write to the same symbol-keyed dict — a
    symbol viewed on the Symbol page just before checking Top Movers
    could briefly show up there with no change_pct. Found live
    2026-09-16 (CTNT).
    """
    symbol = symbol.upper()
    # to_thread: get_historical_bars / get_quote / scan_symbol are all
    # blocking (DB + provider network calls). This is an ``async def``
    # route handler, running directly on the server's event loop — a
    # blocking call here would stall every other concurrent request, not
    # just this one (the same class of bug documented on
    # scan_symbols_async's _prefetch()).
    try:
        historical_bars = await asyncio.to_thread(
            market_data_manager.get_historical_bars,
            symbol,
            timeframe="1d",
            range_="3mo",
        )
    except Exception:
        historical_bars = None
    try:
        quote = await asyncio.to_thread(market_data_manager.get_quote, symbol)
    except Exception:
        quote = None
    result = await asyncio.to_thread(
        market_scanner.scan_symbol,
        symbol,
        historical_bars=historical_bars,
        quote=quote,
    )
    from backend.alerts.engine import alerts_engine

    alerts_engine.evaluate_scan_result(result)
    return _result_to_dict(result)


@ttl_cached(_scan_cache, key_fn=lambda s: s.upper())
async def _cached_scan(symbol: str) -> _ScanResultResponse:
    return await _scan_and_notify(symbol)


@router.get("/{symbol}", response_model=_ScanResultResponse)
async def scan_symbol(
    symbol: str,
    include_history: bool = Query(
        False,
        description="Attach completed daily signal outcome statistics to the explanation.",
    ),
):
    """Scan a single symbol and return the full result.

    Wrapped in a 10s TTL cache (v2.1 Item 1.2). Repeated requests
    within the TTL window return the cached response without re-running
    the scan pipeline. The result is also stored in the scanner's
    internal ``scan_results`` dict (keyed by symbol) so subsequent
    reads via ``GET /api/scanner/{symbol}/cached`` are cheap.
    """
    try:
        response = await _cached_scan(symbol.upper())
        if include_history:
            # signal_recorder owns its own short-lived DB session. Keep this
            # off the event loop because it performs a synchronous SQL query.
            try:
                from backend.services.signal_recorder import signal_recorder

                stats = await asyncio.to_thread(signal_recorder.get_stats, symbol.upper(), "1d")
            except Exception as exc:  # pragma: no cover - DB/provider dependent
                logger.debug("Historical signal stats unavailable for %s: %s", symbol, exc)
                stats = None
            if stats is not None:
                explanation = dict(response.explanation or {})
                explanation["historical_performance"] = stats
                response = response.model_copy(update={"explanation": explanation})
        return response
    except Exception as e:
        logger.error(f"Error scanning symbol {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


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
            raise HTTPException(status_code=500, detail=str(e)) from e
    return result.signals or []


# --- Watchlist endpoints ----------------------------------------------


@router.get("/watchlist/{watchlist_id}", response_model=_RankedResponse)
async def scan_watchlist(watchlist_id: int, db: Session = Depends(get_db)):
    """Scan every enabled symbol in ``watchlist_id`` and return them ranked.

    Includes disabled rows in the response (with ``is_enabled=False``) so the
    UI can render them greyed out and let the user re-enable or remove them
    without a second round-trip. Disabled rows are not actually scanned —
    they get a stub entry derived from the last cached scan if available,
    otherwise they appear with neutral score ``0.0``.

    Enabled symbols are scanned concurrently via ``asyncio.to_thread`` so
    wall-clock latency is roughly ``ceil(N / workers)`` rather than N serial
    HTTP round-trips.
    """
    repo = WatchlistRepository(db)
    watchlist = await asyncio.to_thread(repo.get_watchlist, watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    # Pull BOTH enabled and disabled rows so the UI can manage them.
    all_rows = await asyncio.to_thread(
        repo.get_all_watchlist_symbols, watchlist_id, include_disabled=True
    )
    if not all_rows:
        return _RankedResponse(timestamp="", count=0, results=[])

    enabled_rows = [ws for ws in all_rows if ws.is_enabled]
    disabled_rows = [ws for ws in all_rows if not ws.is_enabled]

    # Build a symbol → entity_type map so we can tag each scan result.
    entity_map = {ws.symbol.upper(): ws.entity_type for ws in all_rows}

    # Only the enabled symbols need a live scan.
    enabled_symbols = [ws.symbol for ws in enabled_rows]
    if enabled_symbols:
        await market_scanner.scan_symbols_async(enabled_symbols)
        ranked = market_scanner.rank_symbols(enabled_symbols)
    else:
        ranked = []

    by_symbol = {r.symbol.upper(): r for r in market_scanner.scan_results.values()}
    results: list[_ScanResultResponse] = []

    # 1) Enabled rows: in rank order with real scan data.
    for sym, _score in ranked:
        result = by_symbol.get(sym.upper())
        if result is not None:
            entry = _result_to_dict(result)
            entry.entity_type = entity_map.get(sym.upper()) or "stock"
            results.append(entry)

    # 2) Disabled rows: append as stubs so the user can re-enable or delete
    #    them. Use the last cached scan if available, otherwise neutral
    #    placeholders. ``is_enabled=False`` tells the UI to grey them out.
    for ws in disabled_rows:
        cached = by_symbol.get(ws.symbol.upper())
        if cached is not None:
            entry = _result_to_dict(cached)
            entry.is_enabled = False
            entry.entity_type = ws.entity_type or "stock"
            results.append(entry)
        else:
            results.append(
                _ScanResultResponse(
                    symbol=ws.symbol,
                    timestamp="",
                    quote=None,
                    indicator_values={},
                    scores={},
                    total_score=0.0,
                    rank=None,
                    signals=[],
                    trend_signals={},
                    is_enabled=False,
                    entity_type=ws.entity_type or "stock",
                )
            )

    last_scan = market_scanner.last_scan_time
    return _RankedResponse(
        timestamp=_to_dashboard_tz(last_scan),
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
    watchlist = await asyncio.to_thread(repo.get_watchlist, watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    watchlist_symbols = await asyncio.to_thread(
        repo.get_watchlist_symbols, watchlist_id, enabled_only=True
    )
    if not watchlist_symbols:
        return []

    symbols = [ws.symbol for ws in watchlist_symbols]
    await market_scanner.scan_symbols_async(symbols)
    ranked = market_scanner.rank_symbols(symbols)

    by_symbol = {r.symbol.upper(): r for r in market_scanner.scan_results.values()}
    out: list[_ScanResultResponse] = []
    top = limit if limit > 0 else len(ranked)
    for sym, _score in ranked[:top]:
        result = by_symbol.get(sym.upper())
        if result is not None:
            out.append(_result_to_dict(result))
    return out
