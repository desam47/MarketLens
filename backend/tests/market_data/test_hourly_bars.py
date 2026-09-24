"""
MD-01: 1h and 4h bars on the clock-hour grid (backend/market_data/hourly_bars.py).

A stored 1h bar covers one clock hour, so its close must be the close of the
last 1m bar of that hour. Webull and Yahoo hourly bars start on the half hour,
and flooring them onto the hour stored every one 30 minutes early.
"""

import unittest
from datetime import datetime, timedelta

from backend.market_data.hourly_bars import (
    aggregate_1h_to_4h,
    build_1h_from_1m,
    closed_provider_hours,
    hour_session,
)
from backend.models import Bar, DataStatus

# Thursday 2025-01-02, a full trading day.
DAY = datetime(2025, 1, 2)


def _bar(ts, close, timeframe="1m", session="regular", volume=100):
    return Bar(
        symbol="SPY",
        timeframe=timeframe,
        open=close - 0.5,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
        timestamp=ts,
        provider="webull",
        data_status=DataStatus.HISTORICAL,
        session=session,
    )


class TestBuild1hFrom1m(unittest.TestCase):
    def test_close_is_the_last_minute_of_its_clock_hour(self):
        hour = DAY.replace(hour=10)
        members = [_bar(hour + timedelta(minutes=m), 500 + m) for m in range(60)]
        bar = build_1h_from_1m("spy", hour, members, now=DAY.replace(hour=12))

        self.assertEqual(bar.timestamp, hour)
        self.assertEqual(bar.close, members[-1].close)  # 10:59, not 11:29
        self.assertEqual(bar.open, members[0].open)
        self.assertEqual(bar.high, 559 + 1)
        self.assertEqual(bar.low, 500 - 1)
        self.assertEqual(bar.volume, 6000)
        self.assertEqual((bar.symbol, bar.timeframe, bar.provider), ("SPY", "1h", "live_from_1m"))
        self.assertEqual(bar.data_status, DataStatus.HISTORICAL)

    def test_open_hour_is_incomplete(self):
        hour = DAY.replace(hour=10)
        members = [_bar(hour, 1), _bar(hour + timedelta(minutes=1), 2)]
        bar = build_1h_from_1m("SPY", hour, members, now=hour + timedelta(minutes=30))
        self.assertEqual(bar.data_status, DataStatus.INCOMPLETE)

    def test_one_minute_is_not_an_hour(self):
        hour = DAY.replace(hour=10)
        self.assertIsNone(build_1h_from_1m("SPY", hour, [_bar(hour, 1)], now=DAY.replace(hour=12)))

    def test_session_comes_from_the_members(self):
        hour = DAY.replace(hour=9)
        members = [
            _bar(hour, 1, session="premarket"),
            _bar(hour + timedelta(minutes=45), 2, session="regular"),
        ]
        bar = build_1h_from_1m("SPY", hour, members, now=DAY.replace(hour=12))
        self.assertEqual(bar.session, "mixed")


class TestHourSession(unittest.TestCase):
    def test_sessions_of_clock_hours(self):
        cases = {4: "premarket", 8: "premarket", 9: "mixed", 10: "regular", 15: "regular"}
        cases |= {16: "after_hours", 19: "after_hours"}
        for hour, session in cases.items():
            with self.subTest(hour=hour):
                self.assertEqual(hour_session(DAY.replace(hour=hour)), session)


class TestAggregate1hTo4h(unittest.TestCase):
    def test_buckets_start_every_four_hours(self):
        hourly = [_bar(DAY.replace(hour=h), 100 + h, timeframe="1h") for h in range(8, 16)]
        bars = aggregate_1h_to_4h("SPY", hourly, now=DAY.replace(hour=20))

        self.assertEqual([b.timestamp.hour for b in bars], [8, 12])
        morning = bars[0]
        self.assertEqual(morning.open, hourly[0].open)
        self.assertEqual(morning.close, hourly[3].close)  # the 11:00 bar
        self.assertEqual(morning.volume, 400)
        self.assertEqual(morning.provider, "aggregated_from_1h")
        self.assertEqual(morning.data_status, DataStatus.HISTORICAL)

    def test_a_lone_hour_is_not_a_bucket_except_at_16(self):
        hourly = [
            _bar(DAY.replace(hour=12), 1, timeframe="1h"),
            _bar(DAY.replace(hour=16), 2, timeframe="1h"),
        ]
        bars = aggregate_1h_to_4h("SPY", hourly, now=DAY.replace(hour=23))
        self.assertEqual([b.timestamp.hour for b in bars], [16])

    def test_the_open_bucket_is_incomplete(self):
        hourly = [_bar(DAY.replace(hour=h), 1, timeframe="1h") for h in (12, 13)]
        bars = aggregate_1h_to_4h("SPY", hourly, now=DAY.replace(hour=14))
        self.assertEqual(bars[0].data_status, DataStatus.INCOMPLETE)


class TestClosedProviderHours(unittest.TestCase):
    def test_keeps_only_hours_ended_by_the_cutoff(self):
        bars = [_bar(DAY.replace(hour=h), 1, timeframe="1h") for h in (12, 13, 14)]
        kept = closed_provider_hours(bars, available_until=DAY.replace(hour=14, minute=45))
        self.assertEqual([b.timestamp.hour for b in kept], [12, 13])


if __name__ == "__main__":
    unittest.main()
