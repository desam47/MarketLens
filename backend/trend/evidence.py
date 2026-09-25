"""Canonical per-timeframe market-data evidence for Trend and Confluence."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.engines.market_calendar import SessionType, daily_bar_reference_time, us_market_calendar
from backend.engines.timeframe import Timeframe


REQUIRED_WARMUP_BARS = 50
_EASTERN = ZoneInfo("America/New_York")
_REGULAR_CLOSE = time(16, 0)
TIMEFRAME_SECONDS = {
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


def _as_aware_timestamp(value: object | None) -> datetime | None:
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


def _weekly_bucket_reference_time(timestamp: datetime) -> datetime:
    """Return a completed derived weekly bar's final regular-session close.

    The 1d-to-1wk resampler stores a weekly bar at its ISO Monday 00:00 UTC
    bucket start.  That key is useful for aggregation, but it is *not* when
    the week's market data became available.  Resolve it to the Friday close
    (or the preceding trading-day close for a Friday holiday) for trader
    facing age and as-of fields.
    """
    friday = timestamp.astimezone(UTC).date() + timedelta(days=4)
    for _ in range(7):
        candidate = datetime.combine(friday, time(12, 0), tzinfo=_EASTERN)
        if us_market_calendar.is_trading_day(candidate):
            return datetime.combine(friday, _REGULAR_CLOSE, tzinfo=_EASTERN)
        friday -= timedelta(days=1)
    # Defensive fallback: preserve the raw source rather than inventing an
    # as-of time if the exchange-calendar search cannot resolve a session.
    return timestamp


def _bar_reference_time(
    timeframe: str, timestamp: datetime | None, provider: object | None
) -> datetime | None:
    """Return the market-data as-of time used for display and freshness."""
    if timestamp is None:
        return None
    if timeframe == "1d":
        return daily_bar_reference_time(timestamp) or timestamp
    if timeframe == "1wk" and str(provider or "").startswith("aggregated_from_"):
        return _weekly_bucket_reference_time(timestamp)
    return timestamp


def build_trend_evidence(
    engine,
    timeframe: str,
    trend_signal,
    metadata: dict | None,
    *,
    now: datetime | None = None,
) -> dict:
    """Build one source-of-truth evidence envelope for a Trend timeframe.

    The result intentionally keeps timestamps as datetimes. API serializers own
    wire formatting, while Trend and Confluence use these exact same truth
    fields for validity and freshness decisions.
    """
    metadata = metadata if isinstance(metadata, dict) else {}
    now = now or datetime.now(UTC)
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

    signal_timestamp = _as_aware_timestamp(getattr(trend_signal, "timestamp", None))
    raw_source_timestamp = _as_aware_timestamp(metadata.get("timestamp"))
    # Retain a legacy signal timestamp only when no metadata record exists at
    # all. A present record with a missing timestamp is a broken evidence chain
    # and must render unavailable rather than being guessed from the signal.
    source_timestamp = raw_source_timestamp if metadata else signal_timestamp
    provider = metadata.get("provider") if metadata else None
    source_as_of = _bar_reference_time(timeframe, source_timestamp, provider)
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
    timeframe_seconds = TIMEFRAME_SECONDS.get(timeframe, 0)
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
    elif warmup_bars < REQUIRED_WARMUP_BARS:
        freshness_state = "warming"
        invalid_reason = "insufficient_warmup"
    elif us_market_calendar.get_session_type(now) == SessionType.CLOSED:
        freshness_state = "closed_session"
    elif age_seconds is not None and timeframe_seconds and age_seconds <= timeframe_seconds:
        freshness_state = "live"
    elif age_seconds is not None and timeframe_seconds and age_seconds <= timeframe_seconds * 2:
        freshness_state = "recent"
    else:
        freshness_state = "stale"
        invalid_reason = "source_stale"

    return {
        "freshness_state": freshness_state,
        "age_seconds": age_seconds,
        "valid": freshness_state in {"live", "recent", "closed_session"},
        "invalid_reason": invalid_reason,
        "warmup_bars": warmup_bars,
        "required_warmup_bars": REQUIRED_WARMUP_BARS,
        "source_timestamp": raw_source_timestamp,
        "source_as_of": source_as_of,
        "data_status": data_status,
        "bar_closed": bar_closed,
        "provider": provider,
        "session": metadata.get("session") if metadata else None,
    }
