"""
OHLCV timeframe resampling.

Converts 1-minute bars to higher timeframes (5m, 15m, 30m, 1h, 1d, 1wk) by
aggregating open/high/low/close/volume over a fixed boundary.

This is a pure module — no I/O, no DB, no clock dependency beyond what
the input bars carry. Boundary logic is deterministic given the input
timestamps.

Usage:
    from backend.utils.resampler import resample_ohlcv
    daily = resample_ohlcv(one_minute_bars, "1d")

Phase 3.1.1: the foundation of the 1m-only storage change. Higher TFs
are no longer stored; they are derived from 1m bars at read time.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from backend.models.market_data import Bar, DataStatus
from backend.utils.timezone import NY

# Phase 3.1: the America/New_York ZoneInfo instance is shared across all
# ``_bucket_start_1d`` calls. We reuse the canonical ``NY`` singleton from
# ``backend.utils.timezone`` rather than constructing our own.
_NY_TZ = NY


# ---------------------------------------------------------------------- timeframes

# Minute counts keyed by timeframe string. Anything not in this table
# raises ValueError from resample_ohlcv so callers fail fast.
# Phase 3.7: added 2m/3m to support resample-at-write for sub-hour TFs.
_TF_MINUTES: dict[str, int] = {
    "2m": 2,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
}

# 1d and 1wk are special: they span calendar boundaries, not fixed
# minute counts.
_CALENDAR_TIMEFRAMES: set[str] = {"1d", "1wk"}


class ResampleError(ValueError):
    """Raised for invalid inputs or unsupported target timeframes."""


# Module-level constant so the validation check in resample_ohlcv doesn't
# rebuild the set on every call.
_SUPPORTED = frozenset(_TF_MINUTES) | _CALENDAR_TIMEFRAMES


# ---------------------------------------------------------------------- boundary

def _floor_minute(dt: datetime, minutes: int) -> datetime:
    """Return the start of the containing `minutes`-aligned bucket.

    Example: floor_minute(10:37, 15) = 10:30
    """
    if minutes <= 0 or minutes >= 60:
        raise ResampleError(f"_floor_minute requires 0 < minutes < 60, got {minutes}")
    discarded = dt.minute % minutes
    return dt.replace(minute=dt.minute - discarded, second=0, microsecond=0)


def _floor_hour(dt: datetime) -> datetime:
    """Truncate to the start of the hour (minute/second/ms = 0)."""
    return dt.replace(minute=0, second=0, microsecond=0)


def _floor_4h(dt: datetime) -> datetime:
    """Snap to the start of the 4-hour bucket containing dt.

    Buckets: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 (UTC or local —
    this is a wall-clock helper, callers pass already-tz-normalised
    timestamps). Example: 14:37 → 12:00; 00:01 → 00:00; 04:00 → 04:00.
    """
    bucket = (dt.hour // 4) * 4
    return dt.replace(hour=bucket, minute=0, second=0, microsecond=0)


def _bucket_start_1d(dt: datetime) -> datetime:
    """NYSE calendar day boundary: 9:30 ET on the day the bar belongs to.

    A 1m bar timestamped 10:00 ET on 2026-09-01 → bucket_start = 2026-09-01 09:30 ET.
    A 1m bar timestamped 09:00 ET (pre-market) on 2026-09-01 → 2026-09-01 09:30 ET
    (pre-market bars roll into the same bucket as their RTH day).

    Returns a naive datetime if the input is naive; carries the input's tzinfo otherwise.
    """
    # ZoneInfo instance is module-level (``_NY_TZ``) — no per-call constructor.
    # If ``dt`` is naive, project convention says it's NY local. Treat it as such.
    if dt.tzinfo is None:
        # Naive = NY local per project convention.
        et = dt.replace(tzinfo=_NY_TZ)
    else:
        # tz-aware: convert to NY.
        et = dt.astimezone(_NY_TZ)

    bucket = et.replace(hour=9, minute=30, second=0, microsecond=0)
    if dt.tzinfo is None:
        return datetime(bucket.year, bucket.month, bucket.day, 9, 30)
    return bucket


def _bucket_start_1wk(dt: datetime) -> datetime:
    """ISO week boundary: Monday 00:00 UTC of the bar's ISO week."""
    try:
        dt_utc = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        dt_utc = dt_utc.astimezone(UTC)
    except Exception:
        dt_utc = dt
    monday = dt_utc - timedelta(days=dt_utc.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _bucket_start(dt: datetime, timeframe: str) -> datetime:
    """Return the canonical start of the timeframe-bucket that contains dt."""
    if timeframe in _TF_MINUTES:
        m = _TF_MINUTES[timeframe]
        if m == 240:
            return _floor_4h(dt)
        if m == 60:
            return _floor_hour(dt)
        return _floor_minute(dt, m)
    if timeframe == "1d":
        return _bucket_start_1d(dt)
    if timeframe == "1wk":
        return _bucket_start_1wk(dt)
    raise ResampleError(f"unsupported target timeframe: {timeframe!r}")


# ---------------------------------------------------------------------- main

def resample_ohlcv(bars: Iterable[Bar], target_tf: str) -> list[Bar]:
    """Aggregate 1m bars into `target_tf` bars.

    Args:
        bars: input bars, expected at 1m granularity. They do not need
            to be pre-sorted; the function sorts internally.
        target_tf: one of "5m", "15m", "30m", "1h", "1d", "1wk".

    Returns:
        New list of Bar objects at the target timeframe, oldest → newest.
        Output bars carry ``timeframe=target_tf``, the first bar's
        ``provider`` and ``data_status``, and a ``volume`` that is the
        sum of the contributing 1m volumes.

    Raises:
        ResampleError: if ``target_tf`` is not a supported resample target
            (e.g. "2h" or "1m") or if the input contains bars that are
            not at 1m granularity.
    """
    # Validate target timeframe.
    if target_tf not in _SUPPORTED:
        raise ResampleError(
            f"unsupported target timeframe: {target_tf!r}. "
            f"Supported: {sorted(_SUPPORTED)}"
        )

    # Materialise, validate, sort.
    items: list[Bar] = list(bars)
    if not items:
        return []
    if target_tf == "1m":
        # Resampling 1m to 1m is a no-op but we still copy the list.
        return list(items)

    for b in items:
        if b.timeframe != "1m":
            raise ResampleError(
                f"resample_ohlcv requires 1m input bars; got timeframe={b.timeframe!r}"
            )

    items.sort(key=lambda b: b.timestamp)

    # Group bars by their bucket start.
    buckets: dict[datetime, list[Bar]] = {}
    bucket_order: list[datetime] = []
    for b in items:
        bucket = _bucket_start(b.timestamp, target_tf)
        if bucket not in buckets:
            buckets[bucket] = []
            bucket_order.append(bucket)
        buckets[bucket].append(b)

    # Preserve the first bar's provider + data_status for output metadata.
    first = items[0]
    symbol = first.symbol

    out: list[Bar] = []
    for bucket in bucket_order:
        members = buckets[bucket]
        # OHLCV: open = first.open, close = last.close, high = max, low = min.
        first_m = members[0]
        agg_open = first_m.open
        agg_close = members[-1].close
        # Single-pass minmax instead of max() + min() = 2 full iterations.
        agg_high = first_m.high
        agg_low = first_m.low
        agg_volume = first_m.volume
        # Seed status from the first bar so first-bar INCOMPLETE/GAP is honoured.
        agg_status = first_m.data_status
        if agg_status == DataStatus.HISTORICAL:
            incomplete_found = False
            for m in members[1:]:
                if m.high > agg_high:
                    agg_high = m.high
                if m.low < agg_low:
                    agg_low = m.low
                agg_volume += m.volume
                if not incomplete_found:
                    if m.data_status == DataStatus.INCOMPLETE:
                        agg_status = DataStatus.INCOMPLETE
                        incomplete_found = True  # INCOMPLETE wins; stop GAP scan
                    elif m.data_status == DataStatus.GAP:
                        agg_status = DataStatus.GAP
                        # Keep scanning: GAP can still be upgraded by INCOMPLETE
        else:
            # First bar is already non-HISTORICAL (INCOMPLETE or GAP); keep
            # high/low/volume accurate but don't bother checking status.
            for m in members[1:]:
                if m.high > agg_high:
                    agg_high = m.high
                if m.low < agg_low:
                    agg_low = m.low
                agg_volume += m.volume

        out.append(Bar(
            symbol=symbol,
            timestamp=bucket,
            open=agg_open,
            high=agg_high,
            low=agg_low,
            close=agg_close,
            volume=agg_volume,
            timeframe=target_tf,
            provider=first.provider,
            data_status=agg_status,
            source="resampled",
        ))
    return out
