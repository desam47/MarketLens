"""
API endpoints for trend analysis

All TrendEngine instances are sourced from the shared registry
(``backend.api.trend.registry``). This guarantees that the trend API,
multi-timeframe API, and any future consumer share the same warmed-up
engine per symbol — no signal divergence from independent warmup paths.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from backend.api.ttl_cache import _trend_cache, _trend_history_cache

from .registry import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trend", tags=["trend"])

# Convert UTC timestamps → America/New_York (auto EST/EDT) for the
# dashboard. Without this the browser sees "2026-08-31T15:18:08" with no
# offset and JavaScript interprets it as local time — wrong for users
# outside the server's timezone.
_DASHBOARD_TZ = ZoneInfo("America/New_York")


def _to_dashboard_tz(value: datetime | None) -> str | None:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


def _build_trend_payload(engine, sym: str, timeframe: str, tf) -> dict:
    trend_signal = engine.get_current_trend(tf)
    if trend_signal is None:
        return {
            "symbol": sym,
            "timeframe": timeframe,
            "direction": "unknown",
            "strength": "unknown",
            "confidence": 0.0,
            "score": None,
            "classification": None,
            "timestamp": None,
        }
    return {
        "symbol": trend_signal.symbol,
        "timeframe": trend_signal.timeframe.value,
        "direction": trend_signal.direction.value,
        "strength": trend_signal.strength.value,
        "confidence": trend_signal.confidence,
        "score": trend_signal.score,
        "classification": trend_signal.classification.value
            if hasattr(trend_signal.classification, "value")
            else trend_signal.classification,
        "timestamp": _to_dashboard_tz(trend_signal.timestamp),
    }


def _get_cached_trend(sym: str, timeframe: str, engine) -> dict:
    """Read-or-compute a single (symbol, timeframe) trend payload, TTL-cached.

    Shared by the single-timeframe and batch endpoints so both hit the
    same 30s cache keyed by ``symbol:timeframe`` — a timeframe fetched via
    one path is warm for the other.
    """
    from backend.engines.timeframe import Timeframe

    try:
        tf = Timeframe(timeframe)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}") from None

    cache_key = f"{sym}:{timeframe}"
    cached = _trend_cache.get(cache_key)
    if cached is not None:
        return cached
    payload = _build_trend_payload(engine, sym, timeframe, tf)
    _trend_cache[cache_key] = payload
    return payload


@router.get("/{symbol}/current/{timeframe}")
async def get_current_trend(symbol: str, timeframe: str):
    """Get current trend for symbol and timeframe.

    Wrapped in a 30s TTL cache (v2.1 Item 1.2), keyed by
    ``symbol:timeframe`` so different timeframes don't share entries.
    """
    try:
        sym = symbol.upper()
        # Reuse the shared, pre-warmed TrendEngine from the registry.
        engine = get_engine(sym)
        return _get_cached_trend(sym, timeframe, engine)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trend for {symbol} {timeframe}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{symbol}/batch")
async def get_trend_batch(symbol: str, timeframes: str):
    """Get current trend for multiple timeframes in a single request.

    Replaces N separate ``GET /current/{tf}`` calls with one round trip —
    the dashboard's Multi-Timeframe Trend section fetches up to 10
    timeframes per load, which used to mean 10 separate GETs.
    ``timeframes`` is a comma-separated list (e.g. ``1m,5m,1h``). Each
    entry is read through the same 30s TTL cache as the single-timeframe
    endpoint, so results are identical and either endpoint warms the
    other's cache.
    """
    sym = symbol.upper()
    requested = [t.strip() for t in timeframes.split(",") if t.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="timeframes must not be empty")
    try:
        engine = get_engine(sym)
        return [_get_cached_trend(sym, tf, engine) for tf in requested]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trend batch for {symbol} ({timeframes}): {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{symbol}/cache/{timeframe}")
async def clear_trend_cache(symbol: str, timeframe: str):
    """Invalidate the TTL cache for a (symbol, timeframe) pair.

    Useful when a stale "unknown" response was cached during a cold-start
    window and the engine now has data. The next GET recomputes from the
    live TrendEngine.
    """
    cache_key = f"{symbol.upper()}:{timeframe}"
    _trend_cache.pop(cache_key, None)
    return {"symbol": symbol.upper(), "timeframe": timeframe, "cache": "cleared"}


@router.get("/{symbol}/history/{timeframe}")
async def get_trend_history(symbol: str, timeframe: str, limit: int | None = 100):
    """Get trend history for symbol and timeframe (60s TTL cache)."""
    key = f"{symbol.upper()}:{timeframe}:{limit}"
    cached = _trend_history_cache.get(key)
    if cached is not None:
        return cached
    try:
        from backend.engines.timeframe import Timeframe

        try:
            tf = Timeframe(timeframe)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}") from None

        engine = get_engine(symbol.upper())
        history = engine.get_trend_history(tf, limit=limit)

        payload = {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "history": [
                {
                    "direction": signal.direction.value,
                    "strength": signal.strength.value,
                    "confidence": signal.confidence,
                    "score": signal.score,
                    "timestamp": _to_dashboard_tz(signal.timestamp),
                }
                for signal in history
            ],
            "count": len(history),
        }
        _trend_history_cache[key] = payload
        return payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trend history for {symbol} {timeframe}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{symbol}/update/{timeframe}")
async def update_trend(symbol: str, timeframe: str, price: float, volume: float,
                       timestamp: str | None = None):
    """Update trend engine with new market data"""
    try:
        from backend.engines.timeframe import Timeframe

        try:
            Timeframe(timeframe)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}") from None

        engine = get_engine(symbol.upper())

        # Parse timestamp if provided
        ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now()

        engine.update(
            price=price,
            volume=volume,
            timestamp=ts,
        )

        # Invalidate TTL cache for this (symbol, timeframe) so the
        # next GET reflects the new bar.
        _trend_cache.pop(f"{symbol.upper()}:{timeframe}", None)

        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "status": "updated",
            "timestamp": _to_dashboard_tz(ts),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating trend for {symbol} {timeframe}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
