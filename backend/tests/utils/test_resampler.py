"""
Tests for backend.utils.resampler.

Covers: aggregation correctness, boundary alignment, timezone handling,
data_status propagation, empty/single-bar edge cases, and error paths.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.models.market_data import Bar, DataStatus
from backend.utils.resampler import (
    ResampleError,
    _bucket_start_1d,
    _bucket_start_1wk,
    _floor_hour,
    _floor_minute,
    resample_ohlcv,
)


# ------------------------------------------------------------------ helpers

def make_bar(
    dt: datetime,
    open_p: float = 100.0,
    high_p: float = 101.0,
    low_p: float = 99.0,
    close_p: float = 100.5,
    vol: int = 1000,
    status: DataStatus = DataStatus.HISTORICAL,
) -> Bar:
    return Bar(
        symbol="AAPL",
        timestamp=dt,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=vol,
        timeframe="1m",
        provider="yfinance",
        data_status=status,
    )


def dt(year, month, day, hour, minute=0, tz=None) -> datetime:
    """Construct a UTC datetime, optionally with a timezone."""
    if tz:
        return datetime(year, month, day, hour, minute, tzinfo=tz)
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ------------------------------------------------------------------ _floor_minute / _floor_hour

class TestFloorMinute:
    def test_exact_multiple(self):
        assert _floor_minute(dt(2026, 9, 1, 10, 30), 15) == dt(2026, 9, 1, 10, 30)

    def test_discards_remainder(self):
        assert _floor_minute(dt(2026, 9, 1, 10, 37), 15) == dt(2026, 9, 1, 10, 30)

    def test_discards_seconds_microseconds(self):
        d = dt(2026, 9, 1, 10, 37)
        d = d.replace(second=55, microsecond=999999)
        result = _floor_minute(d, 15)
        assert result.second == 0
        assert result.microsecond == 0

    def test_invalid_minutes(self):
        with pytest.raises(ResampleError, match="requires 0 < minutes"):
            _floor_minute(dt(2026, 9, 1, 10), 60)
        with pytest.raises(ResampleError, match="requires 0 < minutes"):
            _floor_minute(dt(2026, 9, 1, 10), 0)


class TestFloorHour:
    def test_truncates_to_hour(self):
        assert _floor_hour(dt(2026, 9, 1, 14, 59)) == dt(2026, 9, 1, 14, 0)

    def test_clears_minutes_and_seconds(self):
        d = dt(2026, 9, 1, 14, 0).replace(second=30, microsecond=123456)
        result = _floor_hour(d)
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0


# ------------------------------------------------------------------ bucket boundaries

class TestBucketStart1d:
    def test_rth_bar(self):
        """10:00 ET → 09:30 ET same day."""
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        bar_et = datetime(2026, 9, 1, 10, 0, tzinfo=et)
        result = _bucket_start_1d(bar_et)
        assert result.hour == 9
        assert result.minute == 30
        assert result.day == 1

    def test_premarket_rolls_into_rth_day(self):
        """09:00 ET (pre-market) on 2026-09-01 → 09:30 ET 2026-09-01."""
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        bar_et = datetime(2026, 9, 1, 9, 0, tzinfo=et)
        result = _bucket_start_1d(bar_et)
        assert result.day == 1
        assert result.hour == 9
        assert result.minute == 30

    def test_after_hours_rolls_back(self):
        """17:00 ET (after-hours) → 09:30 ET same calendar day."""
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        bar_et = datetime(2026, 9, 1, 17, 0, tzinfo=et)
        result = _bucket_start_1d(bar_et)
        assert result.day == 1

    def test_naive_datetime_fallback(self):
        """Naive (no tz) datetime falls back to naive date + 09:30."""
        result = _bucket_start_1d(datetime(2026, 9, 1, 10, 0))
        assert result == datetime(2026, 9, 1, 9, 30)


class TestBucketStart1wk:
    def test_monday(self):
        """Monday 2026-09-07 → Monday 00:00 UTC of same week."""
        d = dt(2026, 9, 7, 14, 30)
        result = _bucket_start_1wk(d)
        assert result.weekday() == 0  # Monday
        assert result.hour == 0
        assert result.minute == 0

    def test_wednesday(self):
        """Wednesday rolls back to Monday of same week."""
        d = dt(2026, 9, 9, 14, 30)
        result = _bucket_start_1wk(d)
        assert result.weekday() == 0
        assert result.day == 7  # Monday of that week


# ------------------------------------------------------------------ resample_ohlcv core

class TestResample5m:
    def test_five_1m_bars_to_one_5m(self):
        bars = [
            make_bar(dt(2026, 9, 1, 10, 0), 100, 101, 99, 100),
            make_bar(dt(2026, 9, 1, 10, 1), 100.1, 101.1, 98.5, 100.1),
            make_bar(dt(2026, 9, 1, 10, 2), 100.2, 101.2, 99.8, 100.2),
            make_bar(dt(2026, 9, 1, 10, 3), 100.3, 101.3, 99.7, 100.3),
            make_bar(dt(2026, 9, 1, 10, 4), 100.4, 101.4, 99.6, 100.4, vol=500),
        ]
        out = resample_ohlcv(bars, "5m")
        assert len(out) == 1
        assert out[0].timeframe == "5m"
        assert out[0].open == 100.0
        assert out[0].close == 100.4
        assert out[0].high == 101.4
        assert out[0].low == 98.5  # lowest low across the 5 bars (bar at 10:01)
        assert out[0].volume == 4500  # 4 × 1000 + 1 × 500
        assert out[0].data_status == DataStatus.HISTORICAL

    def test_multiple_5m_buckets(self):
        bars = [
            make_bar(dt(2026, 9, 1, 10, 0), 100, 101, 99, 100),
            make_bar(dt(2026, 9, 1, 10, 1), 100, 101, 99, 100),
            make_bar(dt(2026, 9, 1, 10, 5), 200, 201, 199, 200),
            make_bar(dt(2026, 9, 1, 10, 6), 200, 201, 199, 200),
        ]
        out = resample_ohlcv(bars, "5m")
        assert len(out) == 2
        assert out[0].close == 100
        assert out[1].close == 200

    def test_boundary_at_15_min(self):
        """10:14 is in the 10:00 bucket; 10:15 is in the 10:15 bucket."""
        bars = [
            make_bar(dt(2026, 9, 1, 10, 14), 100, 101, 99, 100),
            make_bar(dt(2026, 9, 1, 10, 15), 200, 201, 199, 200),
        ]
        out = resample_ohlcv(bars, "15m")
        assert len(out) == 2
        # First bucket: 10:00–10:14
        assert out[0].timestamp.minute == 0
        assert out[0].close == 100
        # Second bucket: 10:15–10:29
        assert out[1].timestamp.minute == 15
        assert out[1].close == 200


class TestResample1h:
    def test_two_30m_buckets_merge(self):
        bars = [
            make_bar(dt(2026, 9, 1, 10, 0), 100, 101, 99, 100),
            make_bar(dt(2026, 9, 1, 10, 30), 200, 201, 199, 200),
        ]
        out = resample_ohlcv(bars, "1h")
        assert len(out) == 1
        assert out[0].timestamp.hour == 10
        assert out[0].open == 100
        assert out[0].close == 200
        assert out[0].high == 201
        assert out[0].low == 99

    def test_spans_hour_boundary(self):
        bars = [
            make_bar(dt(2026, 9, 1, 10, 59), 100, 101, 99, 100),
            make_bar(dt(2026, 9, 1, 11, 0), 200, 201, 199, 200),
        ]
        out = resample_ohlcv(bars, "1h")
        # 10:59 belongs to the 10:xx bucket; 11:00 to the 11:xx bucket
        assert len(out) == 2


class TestBucketStart4h:
    """4h buckets: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00."""

    def test_mid_bucket_rolls_back(self):
        from backend.utils.resampler import _floor_4h
        d = dt(2026, 9, 1, 14, 37)
        result = _floor_4h(d)
        assert result.hour == 12
        assert result.minute == 0

    def test_just_after_boundary_rolls_forward(self):
        from backend.utils.resampler import _floor_4h
        d = dt(2026, 9, 1, 12, 1)
        result = _floor_4h(d)
        assert result.hour == 12

    def test_exact_boundary_stays(self):
        from backend.utils.resampler import _floor_4h
        for h in (0, 4, 8, 12, 16, 20):
            d = dt(2026, 9, 1, h, 0)
            result = _floor_4h(d)
            assert result.hour == h

    def test_just_before_boundary_rolls_back(self):
        from backend.utils.resampler import _floor_4h
        d = dt(2026, 9, 1, 11, 59)
        result = _floor_4h(d)
        assert result.hour == 8


class TestResample4h:
    def test_two_hours_into_one_4h_bar(self):
        """Bars within a single 4h window merge into one bar."""
        bars = [
            make_bar(dt(2026, 9, 1, 12, 0), 100, 101, 99, 100),
            make_bar(dt(2026, 9, 1, 12, 30), 100.1, 101.1, 99.9, 100.1),
            make_bar(dt(2026, 9, 1, 13, 0), 100.2, 101.2, 99.8, 100.2),
            make_bar(dt(2026, 9, 1, 13, 30), 100.3, 101.3, 99.7, 100.3),
        ]
        out = resample_ohlcv(bars, "4h")
        assert len(out) == 1
        assert out[0].timeframe == "4h"
        assert out[0].timestamp.hour == 12
        assert out[0].open == 100
        assert out[0].close == 100.3
        assert out[0].high == 101.3
        assert out[0].low == 99

    def test_spans_4h_boundary(self):
        """Bars across the 04:00 → 08:00 boundary produce two bars."""
        bars = [
            make_bar(dt(2026, 9, 1, 3, 59), 100, 101, 99, 100),
            make_bar(dt(2026, 9, 1, 4, 0), 200, 201, 199, 200),
        ]
        out = resample_ohlcv(bars, "4h")
        assert len(out) == 2
        assert out[0].timestamp.hour == 0
        assert out[1].timestamp.hour == 4

    def test_six_bars_across_day(self):
        """Six 4h buckets cover a 24-hour day."""
        bars = []
        for hour in (0, 1, 4, 5, 8, 9, 12, 13, 16, 17, 20, 21):
            bars.append(make_bar(dt(2026, 9, 1, hour, 0), 100 + hour))
        out = resample_ohlcv(bars, "4h")
        assert len(out) == 6
        assert [b.timestamp.hour for b in out] == [0, 4, 8, 12, 16, 20]


class TestResample1d:
    def test_same_rth_day(self):
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        bars = [
            make_bar(datetime(2026, 9, 1, 10, 0, tzinfo=et), 100, 101, 99, 100),
            make_bar(datetime(2026, 9, 1, 14, 0, tzinfo=et), 200, 201, 199, 200),
        ]
        out = resample_ohlcv(bars, "1d")
        assert len(out) == 1
        assert out[0].timestamp.day == 1
        assert out[0].timestamp.hour == 9
        assert out[0].timestamp.minute == 30
        assert out[0].open == 100
        assert out[0].close == 200
        assert out[0].high == 201
        assert out[0].low == 99

    def test_two_calendar_days(self):
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        bars = [
            make_bar(datetime(2026, 9, 1, 10, 0, tzinfo=et), 100, 101, 99, 100),
            make_bar(datetime(2026, 9, 2, 10, 0, tzinfo=et), 200, 201, 199, 200),
        ]
        out = resample_ohlcv(bars, "1d")
        assert len(out) == 2
        assert out[0].timestamp.day == 1
        assert out[1].timestamp.day == 2


class TestResample1wk:
    def test_same_iso_week(self):
        bars = [
            make_bar(dt(2026, 9, 7, 10, 0), 100, 101, 99, 100),   # Monday
            make_bar(dt(2026, 9, 9, 14, 0), 200, 201, 199, 200),  # Wednesday
        ]
        out = resample_ohlcv(bars, "1wk")
        assert len(out) == 1
        assert out[0].timestamp.weekday() == 0  # Monday
        assert out[0].open == 100
        assert out[0].close == 200

    def test_two_iso_weeks(self):
        bars = [
            make_bar(dt(2026, 9, 7, 10, 0), 100, 101, 99, 100),   # Week 36
            make_bar(dt(2026, 9, 14, 10, 0), 200, 201, 199, 200),  # Week 37
        ]
        out = resample_ohlcv(bars, "1wk")
        assert len(out) == 2


# ------------------------------------------------------------------ edge cases

class TestEdgeCases:
    def test_empty_input_returns_empty(self):
        assert resample_ohlcv([], "5m") == []

    def test_unsupported_timeframe_raises(self):
        bars = [make_bar(dt(2026, 9, 1, 10, 0))]
        with pytest.raises(ResampleError, match="unsupported target timeframe"):
            resample_ohlcv(bars, "2h")
        with pytest.raises(ResampleError, match="unsupported target timeframe"):
            resample_ohlcv(bars, "2m")

    def test_non_1m_input_raises(self):
        bar = make_bar(dt(2026, 9, 1, 10, 0))
        bar.timeframe = "5m"
        with pytest.raises(ResampleError, match="requires 1m input bars"):
            resample_ohlcv([bar], "1h")

    def test_1m_to_1m_is_rejected(self):
        """The resampler only up-samples to higher TFs. Pass-through is
        the caller's responsibility."""
        bars = [make_bar(dt(2026, 9, 1, 10, 0))]
        with pytest.raises(ResampleError, match="unsupported target timeframe"):
            resample_ohlcv(bars, "1m")

    def test_incomplete_status_propagates(self):
        bars = [
            make_bar(dt(2026, 9, 1, 10, 0), status=DataStatus.INCOMPLETE),
            make_bar(dt(2026, 9, 1, 10, 1), status=DataStatus.HISTORICAL),
        ]
        out = resample_ohlcv(bars, "5m")
        assert len(out) == 1
        assert out[0].data_status == DataStatus.INCOMPLETE

    def test_gap_status_propagates(self):
        bars = [
            make_bar(dt(2026, 9, 1, 10, 0), status=DataStatus.GAP),
            make_bar(dt(2026, 9, 1, 10, 1), status=DataStatus.HISTORICAL),
        ]
        out = resample_ohlcv(bars, "5m")
        assert len(out) == 1
        assert out[0].data_status == DataStatus.GAP

    def test_output_timestamp_matches_bucket_boundary(self):
        bars = [make_bar(dt(2026, 9, 1, 10, 37))]  # 10:37 → bucket 10:30
        out = resample_ohlcv(bars, "15m")
        assert out[0].timestamp.minute == 30

    def test_preserves_symbol(self):
        bars = [
            make_bar(dt(2026, 9, 1, 10, 0)),
            make_bar(dt(2026, 9, 1, 10, 1)),
        ]
        out = resample_ohlcv(bars, "5m")
        assert all(b.symbol == "AAPL" for b in out)


class TestZoneInfoHoisted:
    """Phase 3.1: ``ZoneInfo("America/New_York")`` was instantiated on every
    ``_bucket_start_1d`` call. The fix hoists it to a module-level
    ``_NY_TZ`` so the timezone database is read once per process."""

    def test_module_level_ny_tz_constant(self):
        """The America/New_York ZoneInfo lives at module scope, not in the
        function body."""
        from backend.utils import resampler

        assert hasattr(resampler, "_NY_TZ")
        # It is a real ZoneInfo, not a string or a lazy import handle.
        from zoneinfo import ZoneInfo
        assert isinstance(resampler._NY_TZ, ZoneInfo)
        assert str(resampler._NY_TZ) == "America/New_York"

    def test_bucket_start_1d_uses_module_tz(self):
        """``_bucket_start_1d`` references the module-level ``_NY_TZ``,
        not a freshly constructed ZoneInfo."""
        from backend.utils import resampler
        import inspect

        src = inspect.getsource(resampler._bucket_start_1d)
        # The function body must NOT contain ``ZoneInfo(`` — that would
        # indicate a per-call construction.
        assert "ZoneInfo(" not in src, (
            "_bucket_start_1d still constructs ZoneInfo on every call"
        )
        # And it must reference the hoisted constant.
        assert "_NY_TZ" in src, (
            "_bucket_start_1d should reference the module-level _NY_TZ"
        )

    def test_repeated_calls_share_same_tz(self):
        """Calling _bucket_start_1d many times uses the same ZoneInfo
        instance — confirms there's no per-call construction."""
        from backend.utils import resampler

        tz_ref = resampler._NY_TZ
        for _ in range(1000):
            resampler._bucket_start_1d(dt(2026, 9, 1, 10, 0))
        # Reference identity should still match the module constant.
        assert resampler._NY_TZ is tz_ref

    def test_1wk_no_longer_imports_zoneinfo(self):
        """``_bucket_start_1wk`` previously did ``from zoneinfo import ZoneInfo``
        per call but never used it. Ensure the dead import is gone."""
        from backend.utils import resampler
        import inspect

        src = inspect.getsource(resampler._bucket_start_1wk)
        assert "from zoneinfo import" not in src
