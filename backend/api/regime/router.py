"""
API endpoints for market regime analysis
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from ...market_data.services.engine_seeder import (
    engine_registry,
    seed_engine_from_bars,
)
from backend.regime.market_regime_engine import MarketRegimeEngine
from backend.regime.relative_strength_engine import RelativeStrengthEngine
from backend.regime.sector_engine import SectorEngine
from backend.api.ttl_cache import _regime_cache

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
        engine = MarketRegimeEngine(symbol)
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
    """Get regime history for symbol"""
    try:
        engine = get_engine(symbol.upper())
        history = engine.get_regime_history(limit=limit)

        return {
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
            "count": len(history)
        }
    except Exception as e:
        logger.error(f"Error getting regime history for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/{symbol}/update")
async def update_regime(symbol: str, price: float, volume: float,
                       timestamp: str | None = None,
                       high: float | None = None,
                       low: float | None = None,
                       open_price: float | None = None):
    """Update regime engine with new market data"""
    try:
        engine = get_engine(symbol.upper())

        # Parse timestamp if provided
        ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now()

        engine.update(
            price=price,
            volume=volume,
            timestamp=ts,
            high=high,
            low=low,
            open_price=open_price
        )

        # Invalidate TTL cache so the next GET reflects the new tick.
        _regime_cache.pop(symbol.upper(), None)

        return {
            "symbol": symbol.upper(),
            "status": "updated",
            "timestamp": _to_dashboard_tz(ts)
        }
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
    """Get or create a sector engine for symbol."""
    symbol = symbol.upper()
    if symbol not in _sector_engines:
        engine = SectorEngine(symbol)
        _sector_engines[symbol] = engine
    return _sector_engines[symbol]


@router.get("/{symbol}/relative-strength")
async def get_relative_strength(symbol: str):
    """Phase 8: relative-strength signals vs SPY and QQQ benchmarks."""
    try:
        engine = _get_rs_engine(symbol.upper())
        signals = engine.compute()
        return {
            "symbol": symbol.upper(),
            "signals": [s.to_dict() for s in signals],
            "count": len(signals),
        }
    except Exception as e:
        logger.error(f"Error getting relative strength for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/sector")
async def get_sector_signal(symbol: str):
    """Phase 8: sector alignment signal for symbol."""
    try:
        engine = _get_sector_engine(symbol.upper())
        signal = engine.get_current_signal()
        return signal.to_dict()
    except Exception as e:
        logger.error(f"Error getting sector signal for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
