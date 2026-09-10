"""Tests for backend.repositories.tape_repository (in-memory SQLite)."""
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.market_data_sql import TapeBarModel
from backend.repositories.tape_repository import (
    get_tape_bars,
    prune_tape_bars,
    upsert_tape_bars,
)


def _row(sym="AAPL", ts=None, signed=100, **kw):
    ts = ts or datetime(2026, 9, 10, 10, 0, 0)
    base = dict(symbol=sym, timestamp=ts, open=100.0, high=100.5, low=99.5, close=100.2,
                volume=1000, buy_volume=600, sell_volume=400, signed_volume=signed,
                trade_count=12, block_count=0, vwap=100.1)
    base.update(kw)
    return base


class TestTapeRepository(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        TapeBarModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_upsert_inserts_then_updates_on_conflict(self):
        db = self.Session()
        t = datetime(2026, 9, 10, 10, 0, 0)
        self.assertEqual(upsert_tape_bars(db, [_row(ts=t, signed=100)]), 1)
        upsert_tape_bars(db, [_row(ts=t, signed=-250, block_count=2)])  # same (symbol, ts)
        rows = get_tape_bars(db, "AAPL")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].signed_volume, -250)
        self.assertEqual(rows[0].block_count, 2)

    def test_get_bars_respects_since_and_order(self):
        db = self.Session()
        base = datetime(2026, 9, 10, 10, 0, 0)
        upsert_tape_bars(db, [_row(ts=base + timedelta(seconds=i), signed=i) for i in range(10)])
        rows = get_tape_bars(db, "AAPL", since=base + timedelta(seconds=5))
        self.assertEqual([r.signed_volume for r in rows], [5, 6, 7, 8, 9])  # ascending

    def test_prune_deletes_old_rows(self):
        db = self.Session()
        old = datetime(2026, 9, 1, 10, 0, 0)
        new = datetime(2026, 9, 10, 10, 0, 0)
        upsert_tape_bars(db, [_row(ts=old), _row(ts=new)])
        deleted = prune_tape_bars(db, cutoff=datetime(2026, 9, 5))
        self.assertEqual(deleted, 1)
        self.assertEqual(len(get_tape_bars(db, "AAPL")), 1)

    def test_empty_upsert_is_noop(self):
        self.assertEqual(upsert_tape_bars(self.Session(), []), 0)


if __name__ == "__main__":
    unittest.main()
