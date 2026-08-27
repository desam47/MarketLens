"""
Tests for the bar repository.

Uses an in-memory SQLite engine so the sqlite_master-based unique
constraint detection in upsert_bars is exercised end-to-end. We attach
the Base.metadata to the new engine and create only the bars table.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.database import Base
from backend.models.market_data import Bar, DataStatus
from backend.models.market_data_sql import BarModel
from backend.repositories import bar_repository


def _make_bar(symbol: str, ts: datetime, close: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        timeframe="1d",
        open=close - 1.0,
        high=close + 1.0,
        low=close - 2.0,
        close=close,
        volume=1_000_000,
        timestamp=ts,
        provider="yahoo_finance",
        data_status=DataStatus.HISTORICAL,
    )


class TestBarRepository(unittest.TestCase):

    def setUp(self):
        # Fresh in-memory DB per test so persistence is fully isolated.
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        # Only the bars table — keep tests focused.
        BarModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )

    def tearDown(self):
        self.engine.dispose()

    def test_upsert_bars_inserts_new_rows(self):
        """Fresh bars should be inserted; row count == len(bars)."""
        bars = [
            _make_bar("AAPL", datetime(2025, 1, 1), 100.0),
            _make_bar("AAPL", datetime(2025, 1, 2), 101.0),
            _make_bar("AAPL", datetime(2025, 1, 3), 102.0),
        ]

        with self.Session() as db:
            written = bar_repository.upsert_bars(db, bars)

        self.assertEqual(written, 3)
        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1d")
        self.assertEqual(len(stored), 3)

    def test_upsert_bars_updates_existing_rows(self):
        """Re-upserting the same (symbol, timeframe, timestamp) updates in place."""
        original = _make_bar("AAPL", datetime(2025, 1, 1), 100.0)
        updated = _make_bar("AAPL", datetime(2025, 1, 1), 150.0)

        with self.Session() as db:
            bar_repository.upsert_bars(db, [original])
        with self.Session() as db:
            bar_repository.upsert_bars(db, [updated])

        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1d")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].close, 150.0)

    def test_upsert_bars_empty_input_is_noop(self):
        with self.Session() as db:
            written = bar_repository.upsert_bars(db, [])
        self.assertEqual(written, 0)

    def test_upsert_bars_normalizes_symbol_case(self):
        """A lower-case bar should be stored as upper-case."""
        bar = _make_bar("aapl", datetime(2025, 1, 1), 100.0)

        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            stored_upper = bar_repository.get_bars(db, "AAPL", "1d")
            stored_lower = bar_repository.get_bars(db, "aapl", "1d")

        self.assertEqual(len(stored_upper), 1)
        # The code stores upper-case, and SQLite lookups are
        # case-insensitive, so both lookups find the row.
        self.assertEqual(stored_upper[0].symbol, "AAPL")
        self.assertEqual(len(stored_lower), 1)
        self.assertEqual(stored_lower[0].symbol, "AAPL")

    def test_get_bars_returns_oldest_first(self):
        """get_bars must order by timestamp ASC."""
        t0 = datetime(2025, 1, 1)
        bars = [
            _make_bar("AAPL", t0 + timedelta(days=i), 100 + i)
            for i in range(5)
        ]
        # Insert in scrambled order
        with self.Session() as db:
            bar_repository.upsert_bars(
                db, [bars[3], bars[0], bars[4], bars[1], bars[2]]
            )

        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1d")

        timestamps = [b.timestamp for b in stored]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(len(stored), 5)

    def test_get_bars_respects_limit(self):
        bars = [
            _make_bar("AAPL", datetime(2025, 1, 1) + timedelta(days=i))
            for i in range(10)
        ]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars)

        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1d", limit=3)
        self.assertEqual(len(stored), 3)
        # First 3 by oldest→newest
        self.assertEqual(stored[0].timestamp, datetime(2025, 1, 1))
        self.assertEqual(stored[2].timestamp, datetime(2025, 1, 3))

    def test_get_bars_filters_by_symbol_and_timeframe(self):
        aapl = _make_bar("AAPL", datetime(2025, 1, 1), 100.0)
        googl = _make_bar("GOOGL", datetime(2025, 1, 1), 200.0)
        aapl_1h = Bar(
            symbol="AAPL",
            timeframe="1h",
            open=100, high=101, low=99, close=100.5,
            volume=1_000,
            timestamp=datetime(2025, 1, 1, 9),
            provider="yahoo_finance",
            data_status=DataStatus.HISTORICAL,
        )
        with self.Session() as db:
            bar_repository.upsert_bars(db, [aapl, googl, aapl_1h])

        with self.Session() as db:
            aapl_daily = bar_repository.get_bars(db, "AAPL", "1d")
            aapl_hourly = bar_repository.get_bars(db, "AAPL", "1h")
            googl_daily = bar_repository.get_bars(db, "GOOGL", "1d")

        self.assertEqual(len(aapl_daily), 1)
        self.assertEqual(aapl_daily[0].close, 100.0)
        self.assertEqual(len(aapl_hourly), 1)
        self.assertEqual(aapl_hourly[0].close, 100.5)
        self.assertEqual(len(googl_daily), 1)
        self.assertEqual(googl_daily[0].close, 200.0)

    def test_upsert_bars_preserves_data_status_string(self):
        """A bar whose data_status is already a string should round-trip."""
        bar = _make_bar("AAPL", datetime(2025, 1, 1), 100.0)
        bar.data_status = DataStatus.HISTORICAL  # enum, normal path
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1d")
        self.assertEqual(stored[0].data_status, DataStatus.HISTORICAL)


if __name__ == "__main__":
    unittest.main()
