"""
Tests for SignalRepository.

Uses an in-memory SQLite per test so signals are fully isolated.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.models.market_data_sql import BarModel
from backend.models.signal import HistoricalSignal
from backend.repositories.signal_repository import SignalRepository


class TestSignalRepository(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        HistoricalSignal.__table__.create(self.engine, checkfirst=True)
        BarModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _repo(self, db=None):
        return SignalRepository(db or self.Session())

    def _create_signal(self, **fields):
        """Create a HistoricalSignal directly in the DB."""
        defaults = dict(
            symbol="AAPL",
            timestamp=datetime(2025, 1, 1),
            timeframe="1d",
            price=150.0,
            trend_score=25.0,
            trend_state="bullish",
            market_regime="risk_on",
            return_5b=1.2,
            return_10b=2.5,
            return_20b=5.0,
            mfe=3.0,
            mae=-1.5,
            _outcome_missing=False,
        )
        defaults.update(fields)
        # If a test sets return_5b=None explicitly, mark outcome as missing
        if "return_5b" in fields and fields["return_5b"] is None:
            defaults["_outcome_missing"] = True
        with self.Session() as db:
            s = HistoricalSignal(**defaults)
            db.add(s)
            db.commit()
            db.refresh(s)
            return s

    # --- create ---

    def test_create_inserts_row(self):
        with self.Session() as db:
            sig = self._repo(db).create(
                symbol="AAPL",
                timestamp=datetime(2025, 1, 1),
                timeframe="1d",
                price=150.0,
            )
        self.assertIsNotNone(sig.id)
        self.assertEqual(sig.symbol, "AAPL")

    def test_create_stores_symbol_as_provided(self):
        """Repo stores the symbol as-is; normalization happens in the service layer."""
        with self.Session() as db:
            sig = self._repo(db).create(
                symbol="aapl",
                timestamp=datetime(2025, 1, 1),
                timeframe="1d",
            )
        self.assertEqual(sig.symbol, "aapl")

    # --- bulk_create ---

    def test_bulk_create_returns_count(self):
        records = [
            dict(symbol="AAPL", timestamp=datetime(2025, 1, i), timeframe="1d", price=100.0 + i)
            for i in range(1, 4)
        ]
        with self.Session() as db:
            n = self._repo(db).bulk_create(records)
        self.assertEqual(n, 3)

    def test_bulk_create_empty_is_noop(self):
        with self.Session() as db:
            n = self._repo(db).bulk_create([])
        self.assertEqual(n, 0)

    # --- get_by_id ---

    def test_get_by_id_returns_signal(self):
        created = self._create_signal(symbol="MSFT", price=200.0)
        with self.Session() as db:
            found = self._repo(db).get_by_id(created.id)
        self.assertEqual(found.symbol, "MSFT")

    def test_get_by_id_returns_none_for_missing(self):
        with self.Session() as db:
            found = self._repo(db).get_by_id(99999)
        self.assertIsNone(found)

    # --- get_latest ---

    def test_get_latest_returns_most_recent(self):
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 1), price=100.0)
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 3), price=150.0)
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 2), price=120.0)
        with self.Session() as db:
            latest = self._repo(db).get_latest("AAPL", "1d")
        self.assertEqual(latest.price, 150.0)

    def test_get_latest_normalizes_symbol(self):
        """Repo normalizes the query symbol to upper-case on lookup."""
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 1))
        with self.Session() as db:
            latest = self._repo(db).get_latest("aapl", "1d")
        self.assertIsNotNone(latest)

    # --- get_history ---

    def test_get_history_returns_all_by_symbol(self):
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 1))
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 2))
        self._create_signal(symbol="MSFT", timestamp=datetime(2025, 1, 1))
        with self.Session() as db:
            rows = self._repo(db).get_history(symbol="AAPL")
        self.assertEqual(len(rows), 2)

    def test_get_history_filters_by_timeframe(self):
        self._create_signal(symbol="AAPL", timeframe="1d", timestamp=datetime(2025, 1, 1))
        self._create_signal(symbol="AAPL", timeframe="1h", timestamp=datetime(2025, 1, 1))
        with self.Session() as db:
            rows = self._repo(db).get_history(symbol="AAPL", timeframe="1d")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].timeframe, "1d")

    def test_get_history_respects_limit(self):
        for i in range(5):
            self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, i + 1))
        with self.Session() as db:
            rows = self._repo(db).get_history(symbol="AAPL", limit=3)
        self.assertEqual(len(rows), 3)

    def test_get_history_orders_by_timestamp_desc(self):
        for i in range(3):
            self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, i + 1))
        with self.Session() as db:
            rows = self._repo(db).get_history(symbol="AAPL")
        timestamps = [r.timestamp for r in rows]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_get_history_filters_by_time_range(self):
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 1))
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 6, 1))
        with self.Session() as db:
            rows = self._repo(db).get_history(
                symbol="AAPL",
                start_time=datetime(2025, 1, 1),
                end_time=datetime(2025, 3, 1),
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].timestamp, datetime(2025, 1, 1))

    # --- signal identity (HS-12) ---

    def test_bulk_create_skips_rows_that_already_exist(self):
        """A second writer's copy of the same bar is a no-op, not an error or a duplicate."""
        records = [
            dict(symbol="AAPL", timestamp=datetime(2025, 1, i), timeframe="1d", price=100.0 + i)
            for i in range(1, 4)
        ]
        with self.Session() as db:
            self.assertEqual(self._repo(db).bulk_create(records[:2]), 2)
        with self.Session() as db:
            self.assertEqual(self._repo(db).bulk_create(records), 1)  # only Jan 3 is new
        with self.Session() as db:
            self.assertEqual(db.query(HistoricalSignal).count(), 3)

    def test_database_rejects_a_duplicate_signal(self):
        from sqlalchemy.exc import IntegrityError

        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 1), timeframe="1d")
        with self.assertRaises(IntegrityError):
            self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 1), timeframe="1d")

    # --- get_signals_needing_outcomes ---

    def _bars(self, symbol, start, count, timeframe="1d"):
        """``count`` daily bars for ``symbol``, the first one day after ``start``."""
        with self.Session() as db:
            for i in range(count):
                db.add(BarModel(
                    symbol=symbol, timeframe=timeframe, timestamp=start + timedelta(days=i + 1),
                    open=100.0, high=101.0, low=99.0, close=100.0, volume=1_000,
                    provider="test", data_status="historical",
                ))
            db.commit()

    def test_get_signals_needing_outcomes_returns_null_return_5b(self):
        self._create_signal(symbol="AAPL", return_5b=None)  # needs outcome
        self._create_signal(symbol="MSFT", return_5b=1.0)  # has outcome
        self._bars("AAPL", datetime(2025, 1, 1), 5)
        self._bars("MSFT", datetime(2025, 1, 1), 5)
        with self.Session() as db:
            rows = self._repo(db).get_signals_needing_outcomes(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].symbol, "AAPL")

    def test_get_signals_needing_outcomes_orders_oldest_first(self):
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 3), return_5b=None)
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 1), return_5b=None)
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 2), return_5b=None)
        self._create_signal(symbol="MSFT", timestamp=datetime(2024, 12, 31), return_5b=None)
        self._bars("AAPL", datetime(2025, 1, 1), 10)
        self._bars("MSFT", datetime(2024, 12, 31), 10)
        with self.Session() as db:
            rows = self._repo(db).get_signals_needing_outcomes(limit=10)
        timestamps = [r.timestamp for r in rows]
        self.assertEqual(len(rows), 4)
        self.assertEqual(timestamps, sorted(timestamps))

    def test_get_signals_needing_outcomes_skips_rows_that_cannot_advance(self):
        """HS-15: rows without enough later bars must not fill the batch ahead of newer rows."""
        # A symbol that stopped receiving bars: three old pending rows, only two later bars.
        for day in (1, 2, 3):
            self._create_signal(symbol="NOK", timestamp=datetime(2025, 1, day), return_5b=None)
        self._bars("NOK", datetime(2025, 1, 3), 2)
        # A newer row whose pair has all the bars it needs.
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 3, 1), return_5b=None)
        self._bars("AAPL", datetime(2025, 3, 1), 20)
        with self.Session() as db:
            rows = self._repo(db).get_signals_needing_outcomes(limit=2)
        self.assertEqual([(r.symbol, r.timestamp) for r in rows], [("AAPL", datetime(2025, 3, 1))])

    def test_get_signals_needing_outcomes_waits_for_the_next_missing_window(self):
        """A partial row returns only once enough bars exist to fill its next window."""
        partial = dict(return_5b=1.0, return_10b=None, return_20b=None, mfe=None, mae=None)
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 1), **partial)
        self._bars("AAPL", datetime(2025, 1, 1), 9)  # 5b possible, 10b not yet
        with self.Session() as db:
            self.assertEqual(self._repo(db).get_signals_needing_outcomes(limit=10), [])
        self._bars("AAPL", datetime(2025, 1, 10), 1)  # the 10th later bar arrives
        with self.Session() as db:
            rows = self._repo(db).get_signals_needing_outcomes(limit=10)
        self.assertEqual(len(rows), 1)

    def test_get_signals_needing_outcomes_needs_twenty_bars_for_the_final_window(self):
        partial = dict(return_5b=1.0, return_10b=2.0, return_20b=None, mfe=None, mae=None)
        self._create_signal(symbol="AAPL", timestamp=datetime(2025, 1, 1), **partial)
        self._bars("AAPL", datetime(2025, 1, 1), 19)
        with self.Session() as db:
            self.assertEqual(self._repo(db).get_signals_needing_outcomes(limit=10), [])
        self._bars("AAPL", datetime(2025, 1, 20), 1)
        with self.Session() as db:
            self.assertEqual(len(self._repo(db).get_signals_needing_outcomes(limit=10)), 1)

    # --- update_outcomes ---

    def test_update_outcomes_sets_all_fields(self):
        sig = self._create_signal(symbol="AAPL", return_5b=None)
        with self.Session() as db:
            updated = self._repo(db).update_outcomes(
                signal_id=sig.id,
                return_5b=1.5,
                return_10b=2.5,
                return_20b=5.0,
                mfe=3.0,
                mae=-1.0,
            )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.return_5b, 1.5)
        self.assertEqual(updated.return_10b, 2.5)
        self.assertEqual(updated.return_20b, 5.0)
        self.assertEqual(updated.mfe, 3.0)
        self.assertEqual(updated.mae, -1.0)
        self.assertEqual(updated._outcome_missing, False)

    def test_update_outcomes_returns_none_for_missing_id(self):
        with self.Session() as db:
            result = self._repo(db).update_outcomes(
                signal_id=99999,
                return_5b=1.0,
                return_10b=2.0,
                return_20b=3.0,
                mfe=1.0,
                mae=-0.5,
            )
        self.assertIsNone(result)

    # --- count_by_regime ---

    def test_count_by_regime_groups_correctly(self):
        self._create_signal(symbol="AAPL", market_regime="risk_on")
        self._create_signal(symbol="MSFT", market_regime="risk_on")
        self._create_signal(symbol="GOOGL", market_regime="risk_off")
        with self.Session() as db:
            rows = self._repo(db).count_by_regime()
        rows_map = {r["regime"]: r["count"] for r in rows}
        self.assertEqual(rows_map.get("risk_on", 0), 2)
        self.assertEqual(rows_map.get("risk_off", 0), 1)

    def test_count_by_regime_excludes_null_regime(self):
        self._create_signal(symbol="AAPL", market_regime="risk_on")
        self._create_signal(symbol="MSFT", market_regime=None)
        with self.Session() as db:
            rows = self._repo(db).count_by_regime()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["regime"], "risk_on")

    # --- get_performance_by_regime ---

    def test_get_performance_by_regime_aggregates_returns(self):
        # Two risk_on signals with known returns
        self._create_signal(
            symbol="AAPL",
            market_regime="risk_on",
            return_5b=1.0,
            return_10b=2.0,
            return_20b=4.0,
            mfe=3.0,
            mae=-1.0,
        )
        self._create_signal(
            symbol="MSFT",
            market_regime="risk_on",
            return_5b=3.0,
            return_10b=4.0,
            return_20b=6.0,
            mfe=5.0,
            mae=-2.0,
        )
        # risk_off signal should not affect risk_on aggregation
        self._create_signal(
            symbol="GOOGL",
            market_regime="risk_off",
            trend_state="bearish",
            return_5b=-1.0,
            return_10b=-2.0,
            return_20b=-3.0,
            mfe=1.0,
            mae=-4.0,
        )
        with self.Session() as db:
            rows = self._repo(db).get_performance_by_regime()
        rows_map = {r["regime"]: r for r in rows}
        r_on = rows_map["risk_on"]
        self.assertEqual(r_on["count"], 2)
        self.assertAlmostEqual(r_on["avg_return_5b"], 2.0)
        self.assertAlmostEqual(r_on["avg_return_10b"], 3.0)
        self.assertAlmostEqual(r_on["avg_return_20b"], 5.0)
        self.assertAlmostEqual(rows_map["risk_off"]["avg_return_5b"], 1.0)

    def test_get_performance_by_regime_preserves_a_zero_average(self):
        self._create_signal(
            symbol="AAPL", market_regime="risk_on", trend_state="bullish",
            return_5b=1.0, return_10b=1.0, return_20b=1.0, mfe=2.0, mae=-1.0,
        )
        self._create_signal(
            symbol="MSFT", market_regime="risk_on", trend_state="bearish",
            return_5b=1.0, return_10b=1.0, return_20b=1.0, mfe=2.0, mae=-1.0,
        )
        with self.Session() as db:
            rows = self._repo(db).get_performance_by_regime()
        self.assertEqual(rows[0]["avg_return_5b"], 0.0)

    def test_get_performance_by_regime_requires_return_5b(self):
        """A signal with return_5b=None should not appear in performance."""
        self._create_signal(symbol="AAPL", market_regime="risk_on", return_5b=None)
        self._create_signal(symbol="MSFT", market_regime="risk_on", return_5b=1.0)
        with self.Session() as db:
            rows = self._repo(db).get_performance_by_regime()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 1)

    # --- get_signals_by_trend_state ---

    def test_get_signals_by_trend_state_filters(self):
        self._create_signal(symbol="AAPL", trend_state="bullish")
        self._create_signal(symbol="MSFT", trend_state="bearish")
        self._create_signal(symbol="GOOGL", trend_state="bullish")
        with self.Session() as db:
            rows = self._repo(db).get_signals_by_trend_state("bullish")
        self.assertEqual(len(rows), 2)

    # --- get_signal_count ---

    def test_get_signal_count_returns_total(self):
        self._create_signal(symbol="AAPL")
        self._create_signal(symbol="MSFT")
        with self.Session() as db:
            count = self._repo(db).get_signal_count()
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
