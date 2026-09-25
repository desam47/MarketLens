"""
API endpoints for trend analysis

All TrendEngine instances are sourced from the shared registry
(``backend.api.trend.registry``). This guarantees that the trend API,
multi-timeframe API, and any future consumer share the same warmed-up
engine per symbol — no signal divergence from independent warmup paths.
"""

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from backend.api.ttl_cache import _trend_cache, _trend_history_cache
from backend.engines.timeframe import Timeframe

from .registry import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trend", tags=["trend"])

# Convert UTC timestamps → America/New_York (auto EST/EDT) for the
# dashboard. Without this the browser sees "2026-08-31T15:18:08" with no
# offset and JavaScript interprets it as local time — wrong for users
# outside the server's timezone.
_DASHBOARD_TZ = ZoneInfo("America/New_York")

_REQUIRED_WARMUP_BARS = 50
_TIMEFRAME_SECONDS = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1wk": 604800,
}


def _to_dashboard_tz(value: datetime | None) -> str | None:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


def _as_aware_timestamp(value: object | None) -> datetime | None:
    """Return an aware UTC timestamp from engine metadata when possible."""
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)


def _normalized_data_status(value: object | None) -> str:
    raw = getattr(value, "value", value)
    normalized = str(raw or "unknown").strip().lower()
    return "ok" if normalized in {"historical", "live", "ok"} else normalized


def _bar_reference_time(timeframe: str, timestamp: datetime | None) -> datetime | None:
    """Return the time the source data is actually as-of for display/age.

    Providers commonly date daily bars at midnight. The data is nevertheless
    as-of that session's close, so preserve the raw source timestamp separately
    while using the normalized close reference for freshness and display.
    """
    if timestamp is None:
        return None
    if timeframe == "1d":
        from backend.engines.market_calendar import daily_bar_reference_time

        return daily_bar_reference_time(timestamp) or timestamp
    return timestamp


def _trend_evidence(
    engine,
    timeframe: str,
    trend_signal,
    metadata: dict,
    *,
    now: datetime | None = None,
) -> dict:
    """Build the server-owned per-timeframe evidence contract for Trend.

    Signal generation time and the source bar's as-of time are not
    interchangeable. In particular, higher timeframes must never inherit a
    recent intraday signal timestamp and thereby look fresher than their own
    source data. This helper intentionally reads only metadata for source
    provenance and falls back to the signal timestamp only for legacy engines
    that have not supplied a metadata record yet.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)

    signal_timestamp = _as_aware_timestamp(getattr(trend_signal, "timestamp", None))
    raw_source_timestamp = _as_aware_timestamp(metadata.get("timestamp"))
    # A legacy engine without *any* metadata can retain its signal timestamp
    # for backwards-compatible diagnostics, but a present metadata record
    # without a timestamp is explicitly unavailable — guessing from the
    # signal would conceal a broken source-evidence chain.
    source_timestamp = raw_source_timestamp if metadata else signal_timestamp
    source_as_of = _bar_reference_time(timeframe, source_timestamp)
    data_status = _normalized_data_status(
        metadata.get("data_status", getattr(trend_signal, "data_quality", None))
    )
    bar_closed_value = metadata.get("bar_closed")
    bar_closed = bar_closed_value if isinstance(bar_closed_value, bool) else None
    try:
        warmup_bars = engine.get_bar_count(Timeframe(timeframe))
    except (AttributeError, ValueError, TypeError):
        warmup_bars = None
    if not isinstance(warmup_bars, int):
        warmup_bars = None

    age_seconds = (
        int(max(0.0, (now - source_as_of).total_seconds())) if source_as_of is not None else None
    )
    timeframe_seconds = _TIMEFRAME_SECONDS.get(timeframe, 0)
    invalid_reason: str | None = None
    if trend_signal is None:
        freshness_state = "unavailable"
        invalid_reason = "signal_unavailable"
    elif source_timestamp is None:
        freshness_state = "unavailable"
        invalid_reason = "source_timestamp_missing"
    elif not metadata:
        freshness_state = "unavailable"
        invalid_reason = "source_metadata_unavailable"
    elif data_status != "ok":
        freshness_state = "unavailable"
        invalid_reason = f"data_status_{data_status}"
    elif bar_closed is False:
        freshness_state = "unavailable"
        invalid_reason = "bar_forming"
    elif warmup_bars is None:
        freshness_state = "unavailable"
        invalid_reason = "warmup_unknown"
    elif warmup_bars < _REQUIRED_WARMUP_BARS:
        freshness_state = "warming"
        invalid_reason = "insufficient_warmup"
    else:
        from backend.engines.market_calendar import SessionType, us_market_calendar

        if us_market_calendar.get_session_type(now) == SessionType.CLOSED:
            freshness_state = "closed_session"
        elif age_seconds is not None and timeframe_seconds and age_seconds <= timeframe_seconds:
            freshness_state = "live"
        elif age_seconds is not None and timeframe_seconds and age_seconds <= timeframe_seconds * 2:
            freshness_state = "recent"
        else:
            freshness_state = "stale"
            invalid_reason = "source_stale"

    valid = freshness_state in {"live", "recent", "closed_session"}
    return {
        "freshness_state": freshness_state,
        "age_seconds": age_seconds,
        "valid": valid,
        "invalid_reason": invalid_reason,
        "warmup_bars": warmup_bars,
        "required_warmup_bars": _REQUIRED_WARMUP_BARS,
        "source_timestamp": _to_dashboard_tz(raw_source_timestamp),
        "source_as_of": _to_dashboard_tz(source_as_of),
        "data_status": data_status,
        "bar_closed": bar_closed,
        "provider": metadata.get("provider") if metadata else None,
        "session": metadata.get("session") if metadata else None,
    }


def _build_trend_payload(engine, sym: str, timeframe: str, tf) -> dict:
    trend_signal = engine.get_current_trend(tf)
    metadata = engine.get_timeframe_metadata(tf)
    if not isinstance(metadata, dict):
        metadata = {}
    evidence = _trend_evidence(engine, timeframe, trend_signal, metadata)
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
            "data_age_seconds": evidence["age_seconds"],
            "data_status": evidence["data_status"],
            "provider": evidence["provider"],
            "session": evidence["session"],
            "bar_closed": evidence["bar_closed"],
            "evidence": evidence,
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
        # The public timestamp is the source bar's true as-of time, not the
        # last signal-generation timestamp. This keeps daily/weekly cards
        # from inheriting a recent intraday display time.
        "timestamp": evidence["source_as_of"],
        "data_age_seconds": evidence["age_seconds"],
        "data_status": evidence["data_status"],
        "provider": evidence["provider"],
        "session": evidence["session"],
        "bar_closed": evidence["bar_closed"],
        "evidence": evidence,
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
    from backend.engines.timeframe import Timeframe

    invalid = [tf for tf in requested if tf not in {candidate.value for candidate in Timeframe}]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {invalid[0]}")
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
async def update_trend(
    symbol: str, timeframe: str, price: float, volume: float, timestamp: str | None = None
):
    """Update trend engine with new market data"""
    try:
        from backend.engines.timeframe import Timeframe

        try:
            Timeframe(timeframe)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}") from None

        engine = get_engine(symbol.upper())

        # Parse timestamp if provided. Invalid client input is a 400, not an
        # internal server error from the generic handler below.
        try:
            ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now()
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid timestamp") from exc

        engine.update(
            price=price,
            volume=volume,
            timestamp=ts,
            timeframe=timeframe,
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
