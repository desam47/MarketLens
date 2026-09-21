"""
API endpoints for market regime analysis
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from backend.api.trend.registry import get_engine as get_shared_trend_engine
from backend.api.ttl_cache import (
    _regime_cache,
    _regime_history_cache,
    _rs_batch_cache,
    _sector_cache,
    _transitions_cache,
)
from backend.regime.market_regime_engine import MarketRegimeEngine
from backend.regime.relative_strength_engine import RelativeStrengthEngine
from backend.regime.sector_engine import SectorEngine

from ...market_data.services.engine_seeder import (
    engine_registry,
    seed_engine_from_bars,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/regime", tags=["regime"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    """Serialize a datetime as an ISO string in America/New_York.

    Naive values are NY wall time (project convention). Delegates to the
    shared helper so every router renders the same stored timestamp
    identically — this copy used to be the only one that read naive as NY
    while sixteen others read it as UTC, so the same bar rendered 4-5h
    apart depending on which endpoint you hit.
    """
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


# In a real implementation, these would be dependency injected or managed as services
_engines: dict[str, MarketRegimeEngine] = {}
_rs_engines: dict[str, RelativeStrengthEngine] = {}
_sector_engines: dict[str, SectorEngine] = {}

# Freshness thresholds for the data_age_seconds field on the regime response.
# Used by the frontend to color the "last updated" indicator.
#   fresh:  < 60s   — engine saw a tick just now (green)
#   recent: < 5min  — engine is current within the same bar (yellow)
#   stale:  < 1hr   — engine hasn't been updated in a while (orange)
#   stuck:  >= 1hr  — engine state is from a prior session, or ingestion is broken (red)
FRESHNESS_FRESH_SEC = 60
FRESHNESS_RECENT_SEC = 300
FRESHNESS_STALE_SEC = 3600


def _freshness(age_seconds: float | None) -> str:
    """Bucket a data-age in seconds into a freshness label for the UI."""
    if age_seconds is None:
        return "unknown"
    if age_seconds < FRESHNESS_FRESH_SEC:
        return "fresh"
    if age_seconds < FRESHNESS_RECENT_SEC:
        return "recent"
    if age_seconds < FRESHNESS_STALE_SEC:
        return "stale"
    return "stuck"


def _data_age_seconds(ts: datetime | None) -> float | None:
    """Seconds between ``ts`` and now. Returns None if ts is missing.

    Stored timestamps are naive America/New_York (project convention since
    2026-09-02), so we compare against naive NY time. Previously this used
    ``datetime.now(UTC).replace(tzinfo=None)`` which caused 4–5h skew on
    every freshness computation (the timestamp was treated as UTC when it
    was actually NY).
    """
    if ts is None:
        return None
    from backend.utils.timezone import now_ny

    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    delta = (now_ny() - ts).total_seconds()
    # Negative ages (server clock skew, or ts from "the future") don't make
    # sense for freshness; clamp to 0.
    return max(0.0, delta)


def get_engine(symbol: str) -> MarketRegimeEngine:
    """Get or create regime engine for symbol, seeding from DB on first access."""
    symbol = symbol.upper()
    if symbol not in _engines:
        # Phase 3.9.2: inject the shared TrendEngine so the regime engine
        # does not build its own private TrendEngine. Both engines now
        # share warmed-up indicator state; ticks delivered to the trend
        # engine automatically feed the regime engine via its shared ref.
        shared_trend = get_shared_trend_engine(symbol)
        engine = MarketRegimeEngine(symbol, trend_engine=shared_trend)
        _engines[symbol] = engine
        # Seed historical data from DB so we return real signals immediately
        count = seed_engine_from_bars(symbol, "1m", engine.update)
        if count > 0:
            logger.info(f"Seeded regime engine for {symbol} with {count} historical bars")
        # Register for live-tick updates from the ingestion service. Both
        # quote and bar events feed the regime engine — quotes give us
        # intra-minute updates (more frequent), bars give us canonical
        # OHLCV at minute boundaries. Without the bar hook, the regime
        # signal timestamp lags the bar timestamp by 30s+ (the quote
        # loop's interval) and the dashboard shows "stale" even when
        # bars are flowing.

        # dispatch_bar passes (symbol, timeframe, price, volume, timestamp,
        # high, low, open_price) but engine.update only accepts the latter
        # six — wrap it to drop the two extras.
        def bar_update(**kwargs):
            # Phase 3.9.9: guard the f-string so it doesn't evaluate on every
            # tick when DEBUG is off (this fires for every 1m bar × every symbol).
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"bar_update callback invoked for {symbol} @ {kwargs.get('timestamp')} "
                    f"(price={kwargs.get('price')})"
                )
            engine.update(
                price=kwargs["price"],
                volume=kwargs["volume"],
                timestamp=kwargs["timestamp"],
                high=kwargs.get("high"),
                low=kwargs.get("low"),
                open_price=kwargs.get("open_price"),
            )
            # Invalidate the regime TTL cache so the next API call reflects
            # the freshly-updated regime engine state (not the 30s-TTL stale
            # response that would otherwise be returned).
            _regime_cache.pop(symbol, None)
            # Invalidate the transitions TTL cache so the latest score/timestamp
            # and transition list reflect the newly-ingested bar.
            for key in list(_transitions_cache.keys()):
                if key.startswith(f"{symbol}:"):
                    _transitions_cache.pop(key, None)

        # NOTE: quote-frequency updates are intentionally NOT fed to the regime
        # engine. Alpaca free tier stops returning fresh quotes after 16:00 ET,
        # causing stale 16:00:05 timestamps to overwrite the correct bar-driven
        # regime timestamp every 10s. Regime signals are derived from bar-level
        # indicators (ADX, ATR, Bollinger Bands) and need only minute-boundary
        # updates. The bar:1m dispatch below provides those.
        engine_registry.register("bar:1m", symbol, bar_update)
    return _engines[symbol]


@router.get("/{symbol}/current")
async def get_current_regime(symbol: str):
    """Get current market regime for symbol.

    Wrapped in a 30s TTL cache (v2.1 Item 1.2). The engine itself
    already keeps its state in ``_engines`` — the TTL cache adds a
    time-bound safety net so back-to-back dashboard refreshes don't
    re-serialise the response every time.

    Response includes `data_age_seconds` (time since the engine last saw a
    tick) and `freshness` (one of fresh|recent|stale|stuck|unknown) so the
    dashboard can tell the user whether the signal is current or stale.
    """
    key = symbol.upper()
    try:
        cached = _regime_cache.get(key)
        if cached is not None:
            return cached
        engine = get_engine(key)
        regime_signal = engine.get_current_regime()

        if regime_signal is None:
            payload = {
                "symbol": key,
                "regime": "unknown",
                "confidence": 0.0,
                "strength": 0.0,
                "supporting_factors": {},
                "timestamp": None,
                "data_age_seconds": None,
                "freshness": "unknown",
            }
        else:
            age = _data_age_seconds(regime_signal.timestamp)
            payload = {
                "symbol": regime_signal.symbol,
                "regime": regime_signal.regime.value,
                "confidence": regime_signal.confidence,
                "strength": regime_signal.strength,
                "supporting_factors": regime_signal.supporting_factors,
                "timestamp": _to_dashboard_tz(regime_signal.timestamp),
                "data_age_seconds": age,
                "freshness": _freshness(age),
            }
        _regime_cache[key] = payload
        return payload
    except Exception as e:
        logger.error(f"Error getting regime for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/history")
async def get_regime_history(symbol: str, limit: int | None = 100):
    """Get regime history for symbol (60s TTL cache)."""
    key = f"{symbol.upper()}:{limit}"
    cached = _regime_history_cache.get(key)
    if cached is not None:
        return cached
    try:
        engine = get_engine(symbol.upper())
        history = engine.get_regime_history(limit=limit)

        payload = {
            "symbol": symbol.upper(),
            "history": [
                {
                    "regime": signal.regime.value,
                    "confidence": signal.confidence,
                    "strength": signal.strength,
                    "supporting_factors": signal.supporting_factors,
                    "timestamp": _to_dashboard_tz(signal.timestamp),
                    "data_age_seconds": _data_age_seconds(signal.timestamp),
                    "freshness": _freshness(_data_age_seconds(signal.timestamp)),
                }
                for signal in history
            ],
            "count": len(history),
        }
        _regime_history_cache[key] = payload
        return payload
    except Exception as e:
        logger.error(f"Error getting regime history for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{symbol}/update")
async def update_regime(
    symbol: str,
    price: float,
    volume: float,
    timestamp: str | None = None,
    high: float | None = None,
    low: float | None = None,
    open_price: float | None = None,
):
    """Update regime engine with new market data"""
    try:
        engine = get_engine(symbol.upper())

        # Parse timestamp if provided
        ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now()

        engine.update(
            price=price, volume=volume, timestamp=ts, high=high, low=low, open_price=open_price
        )

        # Invalidate TTL cache so the next GET reflects the new tick.
        _regime_cache.pop(symbol.upper(), None)

        return {"symbol": symbol.upper(), "status": "updated", "timestamp": _to_dashboard_tz(ts)}
    except Exception as e:
        logger.error(f"Error updating regime for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ----------------------------------------------------------------------
# Phase 8: relative-strength and sector endpoints
# ----------------------------------------------------------------------


def _get_rs_engine(symbol: str) -> RelativeStrengthEngine:
    """Get or create a relative-strength engine for symbol."""
    symbol = symbol.upper()
    if symbol not in _rs_engines:
        engine = RelativeStrengthEngine(symbol)
        _rs_engines[symbol] = engine
    return _rs_engines[symbol]


def _get_sector_engine(symbol: str) -> SectorEngine:
    """Get or create a sector engine for symbol.

    Found live 2026-09-10: SectorEngine(symbol) with no injected engines
    builds three brand-new, never-fed TrendEngine instances (stock,
    sector ETF, SPY) from scratch — caching the SectorEngine itself
    here didn't help, since the inner engines never receive a single
    tick or bar either way. get_current_signal() was always
    "unknown"/"insufficient_data" regardless of how much real trend
    data actually existed. SectorEngine's own docstring says exactly
    what should happen instead: "accept injected engines to share with
    other callers ... looked up via the shared registry so any other
    component that also needs SPY or XLK gets the same instance" — so
    inject the same shared, DB-seeded TrendEngine singletons every
    other trend-consuming feature in the app already uses.
    """
    from backend.regime.sector_engine import SECTOR_ETFS, SECTOR_MAP

    symbol = symbol.upper()
    if symbol not in _sector_engines:
        sector_name = SECTOR_MAP.get(symbol, "Unknown")
        sector_etf = SECTOR_ETFS.get(sector_name)
        engine = SectorEngine(
            symbol,
            stock_engine=get_shared_trend_engine(symbol),
            sector_engine=get_shared_trend_engine(sector_etf) if sector_etf else None,
            market_engine=get_shared_trend_engine("SPY"),
        )
        _sector_engines[symbol] = engine
    return _sector_engines[symbol]


@router.get("/batch/relative-strength")
async def get_batch_relative_strength(symbols: str):
    """Batch relative-strength signals for up to 50 symbols at once.

    Replaces N separate ``/{symbol}/relative-strength`` calls with a single
    HTTP round-trip, reducing watchlist load time significantly when many
    symbols are present.
    """
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(status_code=400, detail="No symbols provided")
    if len(sym_list) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 symbols per batch")

    # Prefixed so a single-symbol batch call (e.g. symbols=AAPL) can't
    # collide with get_relative_strength's cache key below — they share
    # this TTLCache but return differently-shaped payloads.
    cache_key = "batch:" + ",".join(sorted(sym_list))
    cached = _rs_batch_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        results: dict[str, dict] = {}
        for sym in sym_list:
            try:
                engine = _get_rs_engine(sym)
                signals = engine.compute()
                results[sym] = {
                    "symbol": sym,
                    "signals": [s.to_dict() for s in signals],
                    "count": len(signals),
                }
            except Exception as e:
                logger.warning(f"Batch RS failed for {sym}: {e}")
                results[sym] = {"symbol": sym, "signals": [], "count": 0, "error": str(e)}

        payload = {"results": results, "count": len(results)}
        _rs_batch_cache[cache_key] = payload
        return payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch relative-strength error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/relative-strength")
async def get_relative_strength(symbol: str):
    """Phase 8: relative-strength signals vs SPY and QQQ benchmarks (60s TTL cache)."""
    # Prefixed to avoid colliding with get_batch_relative_strength's
    # "batch:..." keys in the same shared TTLCache.
    key = "single:" + symbol.upper()
    cached = _rs_batch_cache.get(key)
    if cached is not None:
        return cached
    try:
        engine = _get_rs_engine(symbol.upper())
        signals = engine.compute()
        payload = {
            "symbol": symbol.upper(),
            "signals": [s.to_dict() for s in signals],
            "count": len(signals),
        }
        _rs_batch_cache[key] = payload
        return payload
    except Exception as e:
        logger.error(f"Error getting relative strength for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/sector")
async def get_sector_signal(symbol: str):
    """Phase 8: sector alignment signal for symbol (5-min TTL cache)."""
    key = symbol.upper()
    cached = _sector_cache.get(key)
    if cached is not None:
        return cached
    try:
        engine = _get_sector_engine(symbol.upper())
        signal = engine.get_current_signal()
        payload = signal.to_dict()
        _sector_cache[key] = payload
        return payload
    except Exception as e:
        logger.error(f"Error getting sector signal for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
