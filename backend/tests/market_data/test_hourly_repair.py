"""
MD-01: the one-off repair of 1h/4h bars stored 30 minutes early
(backend/market_data/hourly_repair.py, run by scripts/repair_hourly_bars.py).

The seeded symbol has shifted Webull bars on an old day and inside the 1m
window, Alpaca IEX bars, an exact 1h bar built from 1m before the window,
stale 4h bars, and 1h/4h signals scored on the shifted bars.
"""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from backend.market_data.hourly_repair import misplaced_hours, repair_symbol
from backend.models import Bar, BarModel, DataStatus, HistoricalSignal

OLD_DAY = datetime(2025, 1, 3)  # Friday, before the 1m window
LIVE_DAY = datetime(2025, 1, 2)  # an hour already built from 1m, before the window
WINDOW_DAY = datetime(2025, 1, 6)  # Monday, inside the 1m window
NOW = WINDOW_DAY.replace(hour=13)


def _row(ts, close, timeframe="1h", provider="webull", session="regular"):
    return BarModel(
        symbol="SPY",
        timeframe=timeframe,
        open=close - 0.5,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000,
        timestamp=ts,
        provider=provider,
        data_status="HISTORICAL",
        session=session,
    )


def _sip(ts, close):
    return Bar(
        symbol="SPY",
        timeframe="1h",
        open=close - 0.5,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=50_000,
        timestamp=ts,
        provider="alpaca",
        data_status=DataStatus.HISTORICAL,
    )


class TestRepairSymbol(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        BarModel.__table__.create(self.engine)
        HistoricalSignal.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

        rows = [_row(OLD_DAY.replace(hour=h), 100 + h) for h in range(9, 16)]  # shifted Webull
        rows += [_row(OLD_DAY.replace(hour=h), 1, provider="alpaca") for h in (8, 16)]  # IEX
        rows += [_row(LIVE_DAY.replace(hour=14), 77, provider="live_from_1m")]
        rows += [_row(WINDOW_DAY.replace(hour=h), 999) for h in (10, 11)]  # shifted, in window
        rows += [_row(OLD_DAY.replace(hour=8), 5, timeframe="4h", provider="aggregated_from_1h")]
        # 1m from 09:37: the 09:00 hour is cut short by the retention prune and is left alone.
        start = WINDOW_DAY.replace(hour=9, minute=37)
        rows += [
            _row(start + timedelta(minutes=m), 200 + m, timeframe="1m", provider="webull")
            for m in range(0, 143)  # through 11:59
        ]
        self.db.add_all(rows)
        self.db.add_all(
            HistoricalSignal(symbol=s, timeframe=tf, timestamp=OLD_DAY.replace(hour=10))
            for s, tf in (("SPY", "1h"), ("SPY", "4h"), ("SPY", "1d"), ("QQQ", "1h"))
        )
        self.db.commit()

        # Alpaca SIP: every old-day hour but 12:00, a half-hour bar that must be ignored,
        # and hours that 1m bars or an existing 1m-built bar already cover.
        self.sip = [_sip(OLD_DAY.replace(hour=h), 300 + h) for h in range(8, 18) if h != 12]
        self.sip += [_sip(OLD_DAY.replace(hour=9, minute=30), 1)]
        self.sip += [_sip(WINDOW_DAY.replace(hour=10), 2), _sip(LIVE_DAY.replace(hour=14), 3)]
        self.sip += [_sip(WINDOW_DAY.replace(hour=12, minute=0), 4)]  # still open at NOW - 15 min
        self.fetches = []

    def _fetch(self, symbol, start, end):
        self.fetches.append((symbol, start, end))
        return [b.model_copy() for b in self.sip]

    def _hourly(self):
        return {
            r.timestamp: r
            for r in self.db.query(BarModel).filter(
                BarModel.symbol == "SPY", BarModel.timeframe == "1h"
            )
        }

    def test_rebuilds_the_hourly_series_on_the_clock_hour(self):
        self.assertEqual(misplaced_hours(self.db, "SPY"), 2)

        report = repair_symbol(self.db, "SPY", self._fetch, NOW)
        hourly = self._hourly()

        # In the 1m window: built from 1m; the 10:00 close is the 10:59 1m close.
        for hour in (10, 11):
            self.assertEqual(hourly[WINDOW_DAY.replace(hour=hour)].provider, "live_from_1m")
        self.assertEqual(hourly[WINDOW_DAY.replace(hour=10)].close, 200 + 82)
        self.assertNotIn(WINDOW_DAY.replace(hour=9), hourly)
        self.assertNotIn(WINDOW_DAY.replace(hour=12), hourly)
        # An exact 1m-built hour is kept.
        self.assertEqual(hourly[LIVE_DAY.replace(hour=14)].close, 77)
        # Old day: every hour Alpaca has, sessions from the clock hour; 12:00 is dropped.
        old = {ts.hour: r for ts, r in hourly.items() if ts.date() == OLD_DAY.date()}
        self.assertEqual(sorted(old), [8, 9, 10, 11, 13, 14, 15, 16, 17])
        self.assertTrue(all(r.provider == "alpaca" for r in old.values()))
        self.assertEqual(old[10].close, 310)
        self.assertEqual(
            (old[8].session, old[9].session, old[16].session), ("premarket", "mixed", "after_hours")
        )
        self.assertTrue(all(ts.minute == 0 for ts in hourly))

        self.assertEqual((report.built_from_1m, report.from_provider, report.dropped), (2, 9, 1))
        self.assertEqual(misplaced_hours(self.db, "SPY"), 0)
        self.assertEqual(self.fetches, [("SPY", LIVE_DAY, NOW)])  # from the first 1h day

    def test_rebuilds_4h_from_the_repaired_hours(self):
        repair_symbol(self.db, "SPY", self._fetch, NOW)
        four = {r.timestamp: r for r in self.db.query(BarModel).filter(BarModel.timeframe == "4h")}
        morning = four[OLD_DAY.replace(hour=8)]
        self.assertEqual(morning.open, 308 - 0.5)
        self.assertEqual(morning.close, 311)  # the 11:00 bar
        self.assertEqual(morning.volume, 4 * 50_000)
        self.assertIn(WINDOW_DAY.replace(hour=8), four)  # 10:00 and 11:00 from 1m

    def test_deletes_only_this_symbols_1h_and_4h_signals(self):
        report = repair_symbol(self.db, "SPY", self._fetch, NOW)
        left = sorted((s.symbol, s.timeframe) for s in self.db.query(HistoricalSignal).all())
        self.assertEqual(left, [("QQQ", "1h"), ("SPY", "1d")])
        self.assertEqual(dict(report.signals_deleted), {"1h": 1, "4h": 1})

    def test_writes_nothing_until_the_caller_commits(self):
        before = self.db.query(func.count()).select_from(BarModel).scalar()
        repair_symbol(self.db, "SPY", self._fetch, NOW)
        self.db.rollback()
        self.assertEqual(self.db.query(func.count()).select_from(BarModel).scalar(), before)
        self.assertEqual(misplaced_hours(self.db, "SPY"), 2)
        self.assertEqual(self.db.query(HistoricalSignal).count(), 4)

    def test_a_failed_fetch_leaves_the_symbol_untouched(self):
        def failing(*_):
            raise RuntimeError("Alpaca down")

        with self.assertRaises(RuntimeError):
            repair_symbol(self.db, "SPY", failing, NOW)
        self.assertEqual(len(self._hourly()), 12)
        self.assertEqual(self.db.query(HistoricalSignal).count(), 4)

    def test_symbol_without_hourly_bars_is_skipped(self):
        report = repair_symbol(self.db, "QQQ", self._fetch, NOW)
        self.assertIsNone(report.first_hour)
        self.assertEqual(self.fetches, [])


if __name__ == "__main__":
    unittest.main()
