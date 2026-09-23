"""
Tests for the bar repository.

Uses an in-memory SQLite engine so the sqlite_master-based unique
constraint detection in upsert_bars is exercised end-to-end. We attach
the Base.metadata to the new engine and create only the bars table.
"""

import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.models.market_data import Bar, DataStatus
from backend.models.market_data_sql import BarModel
from backend.repositories import bar_repository


def _make_bar(symbol: str, ts: datetime, close: float = 100.0, timeframe: str = "1m") -> Bar:
    """Create a test bar.

    Phase 3.1: all bars stored in the DB are at ``timeframe="1m"``.
    ``timeframe`` defaults to "1m" so callers don't accidentally test the
    pre-3.1 path of storing higher TFs directly.
    """
    return Bar(
        symbol=symbol,
        timeframe=timeframe,
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
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_upsert_bars_inserts_new_rows(self):
        """Fresh 1m bars should be inserted; row count == len(bars)."""
        bars = [
            _make_bar("AAPL", datetime(2025, 1, 1, 9, 30), 100.0),
            _make_bar("AAPL", datetime(2025, 1, 1, 9, 31), 101.0),
            _make_bar("AAPL", datetime(2025, 1, 1, 9, 32), 102.0),
        ]

        with self.Session() as db:
            written = bar_repository.upsert_bars(db, bars)

        self.assertEqual(written, 3)
        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1m")
        self.assertEqual(len(stored), 3)

    def test_upsert_bars_updates_existing_rows(self):
        """Re-upserting the same (symbol, timeframe, timestamp) updates in place."""
        original = _make_bar("AAPL", datetime(2025, 1, 1, 9, 30), 100.0)
        updated = _make_bar("AAPL", datetime(2025, 1, 1, 9, 30), 150.0)

        with self.Session() as db:
            bar_repository.upsert_bars(db, [original])
        with self.Session() as db:
            bar_repository.upsert_bars(db, [updated])

        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1m")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].close, 150.0)

    def test_stream_upsert_revises_its_own_row(self):
        original = _make_bar("AAPL", datetime(2025, 1, 1, 9, 30), 100.0)
        original.provider = "webull_stream"
        revised = _make_bar("AAPL", datetime(2025, 1, 1, 9, 30), 101.5)
        revised.provider = "webull_stream"

        with self.Session() as db:
            self.assertEqual(bar_repository.upsert_stream_bars(db, [original]), 1)
            self.assertEqual(bar_repository.upsert_stream_bars(db, [revised]), 1)
            row = db.query(BarModel).filter(BarModel.symbol == "AAPL").one()
            self.assertEqual(row.close, 101.5)
            self.assertEqual(row.provider, "webull_stream")

    def test_stream_upsert_does_not_replace_authoritative_rest_row(self):
        rest_bar = _make_bar("AAPL", datetime(2025, 1, 1, 9, 30), 150.0)
        stream_bar = _make_bar("AAPL", datetime(2025, 1, 1, 9, 30), 99.0)
        stream_bar.provider = "webull_stream"

        with self.Session() as db:
            bar_repository.upsert_bars(db, [rest_bar])
            self.assertEqual(bar_repository.upsert_stream_bars(db, [stream_bar]), 0)
            row = db.query(BarModel).filter(BarModel.symbol == "AAPL").one()
            self.assertEqual(row.close, 150.0)
            self.assertEqual(row.provider, "yahoo_finance")

    def test_upsert_bars_empty_input_is_noop(self):
        with self.Session() as db:
            written = bar_repository.upsert_bars(db, [])
        self.assertEqual(written, 0)

    def test_upsert_bars_normalizes_symbol_case(self):
        """A lower-case bar should be stored as upper-case."""
        bar = _make_bar("aapl", datetime(2025, 1, 1, 9, 30), 100.0)

        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            stored_upper = bar_repository.get_bars(db, "AAPL", "1m")
            stored_lower = bar_repository.get_bars(db, "aapl", "1m")

        self.assertEqual(len(stored_upper), 1)
        # The code stores upper-case, and SQLite lookups are
        # case-insensitive, so both lookups find the row.
        self.assertEqual(stored_upper[0].symbol, "AAPL")
        self.assertEqual(len(stored_lower), 1)
        self.assertEqual(stored_lower[0].symbol, "AAPL")

    def test_get_bars_returns_oldest_first(self):
        """get_bars must order by timestamp ASC."""
        t0 = datetime(2025, 1, 1, 9, 30)
        bars = [_make_bar("AAPL", t0 + timedelta(minutes=i), 100 + i) for i in range(5)]
        # Insert in scrambled order
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bars[3], bars[0], bars[4], bars[1], bars[2]])

        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1m")

        timestamps = [b.timestamp for b in stored]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(len(stored), 5)

    def test_get_bars_respects_limit(self):
        bars = [
            _make_bar("AAPL", datetime(2025, 1, 1, 9, 30) + timedelta(minutes=i)) for i in range(10)
        ]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars)

        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1m", limit=3)
        self.assertEqual(len(stored), 3)
        # First 3 by oldest→newest
        self.assertEqual(stored[0].timestamp, datetime(2025, 1, 1, 9, 30))
        self.assertEqual(stored[2].timestamp, datetime(2025, 1, 1, 9, 32))

    def test_get_bars_filters_by_symbol_and_timeframe(self):
        """get_bars filters strictly by (symbol, timeframe).

        Phase 3.7: all 10 timeframes are stored as direct rows — the
        ingestion service's resample-write loops populate 1h (and every
        other higher TF) directly via upsert_bars, so get_bars is a
        straight indexed query with no read-time resampling. (The older
        Phase 3.1 behaviour, where only 1m was stored and higher TFs were
        derived from it at read time, no longer applies.)
        """
        base = datetime(2025, 1, 2, 14, 0)
        aapl_1m = [
            _make_bar("AAPL", base + timedelta(minutes=i), 100.0 + i * 0.1, timeframe="1m")
            for i in range(60)
        ]
        # A directly-stored 1h bar for AAPL (as the resample-write loop
        # would write), distinct from the 1m rows above.
        aapl_1h = [_make_bar("AAPL", base, 999.0, timeframe="1h")]
        # 1m bar for GOOGL
        googl_1m = [_make_bar("GOOGL", base, 200.0)]

        with self.Session() as db:
            bar_repository.upsert_bars(db, aapl_1m + aapl_1h + googl_1m)

        with self.Session() as db:
            aapl_1m_res = bar_repository.get_bars(db, "AAPL", "1m")
            aapl_1h_res = bar_repository.get_bars(db, "AAPL", "1h")
            googl_1m_res = bar_repository.get_bars(db, "GOOGL", "1m")

        # 1m rows for AAPL only, not GOOGL's or the 1h row.
        self.assertEqual(len(aapl_1m_res), 60)
        self.assertEqual(aapl_1m_res[0].source, "raw")
        # The directly-stored 1h row, filtered independently of the 1m rows.
        self.assertEqual(len(aapl_1h_res), 1)
        self.assertEqual(aapl_1h_res[0].close, 999.0)
        # GOOGL 1m path filtered independently of AAPL's rows.
        self.assertEqual(len(googl_1m_res), 1)

    def test_upsert_bars_preserves_data_status_string(self):
        """A bar's data_status and source field round-trip correctly.

        Phase 3.7: every timeframe (including 1d) is a directly-stored row —
        upsert_bars always writes source="raw" regardless of timeframe.
        """
        bar = _make_bar("AAPL", datetime(2025, 1, 2, 0, 0), 100.0, timeframe="1d")
        bar.data_status = DataStatus.HISTORICAL
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1d", limit=1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].data_status, DataStatus.HISTORICAL)
        self.assertEqual(stored[0].source, "raw")

    def test_slow_query_logging_threshold(self):
        """``get_bars`` logs a WARNING when the slow-query threshold is breached.

        We insert one bar, then verify the slow-log fires when
        ``_SLOW_QUERY_THRESHOLD_MS`` is patched to 0 and does not fire
        at the real 100ms threshold. The EXPLAIN step is exercised via
        the same threshold override.
        """
        import logging

        from backend.repositories import bar_repository

        bar = _make_bar("AAPL", datetime(2025, 1, 1), 100.0)
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])

        # Real threshold (100ms) — in-memory SQLite with 1 row won't breach.
        original = bar_repository._SLOW_QUERY_THRESHOLD_MS
        try:
            bar_repository._SLOW_QUERY_THRESHOLD_MS = 0.0  # force every call to log
            with self.assertLogs(
                "backend.repositories.bar_repository", level=logging.WARNING
            ) as cm:
                with self.Session() as db:
                    bar_repository.get_bars(db, "AAPL", "1d", limit=None)
            warning_lines = [line for line in cm.output if "slow_query" in line]
            self.assertGreater(len(warning_lines), 0)
            self.assertIn("get_bars", warning_lines[0])
            self.assertIn("AAPL", warning_lines[0])
        finally:
            bar_repository._SLOW_QUERY_THRESHOLD_MS = original

    def test_get_bars_fallback_provider_param_is_inert(self):
        """``fallback_provider`` is accepted for API-signature compatibility
        but no longer used — Phase 3.7 stores every timeframe directly
        (populated by the ingestion service's resample-write loops), so
        get_bars never needs to synthesize missing higher-TF bars from a
        live provider call at read time (that was Phase 3.1's read-time
        hybrid-fallback path, since removed). An empty DB returns an empty
        list even when a fallback_provider is supplied.
        """

        def fallback_provider(symbol: str, timeframe: str) -> list[Bar]:
            raise AssertionError("fallback_provider should never be invoked")

        with self.Session() as db:
            bars = bar_repository.get_bars(
                db,
                "AAPL",
                "1d",
                limit=3,
                fallback_provider=fallback_provider,
            )
        self.assertEqual(bars, [])

    def test_get_bars_from_ts_to_ts_filter_1m(self):
        """from_ts/to_ts narrow the 1m fast path by timestamp range."""
        # 30 consecutive 1m bars starting at 09:30.
        base = datetime(2025, 1, 2, 9, 30)
        bars_1m = [
            _make_bar("AAPL", base + timedelta(minutes=i), 100.0 + i * 0.1) for i in range(30)
        ]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars_1m)

        # Window: 09:35–09:40 (5 bars inclusive)
        with self.Session() as db:
            res = bar_repository.get_bars(
                db,
                "AAPL",
                "1m",
                from_ts=datetime(2025, 1, 2, 9, 35),
                to_ts=datetime(2025, 1, 2, 9, 40),
            )
        self.assertEqual(len(res), 6)  # 9:35 .. 9:40 inclusive
        self.assertEqual(res[0].timestamp.minute, 35)
        self.assertEqual(res[-1].timestamp.minute, 40)

    def test_get_bars_from_ts_to_ts_filter_higher_tf(self):
        """from_ts/to_ts narrow directly-stored higher-TF rows too.

        Phase 3.7: 1h (and every other TF) is a direct row, filtered the
        same way as 1m — no read-time bucket resampling or lower-bound
        widening.
        """
        base = datetime(2025, 1, 2, 14, 0)
        bars_1h = [
            _make_bar("AAPL", base, 100.0, timeframe="1h"),
            _make_bar("AAPL", base + timedelta(hours=1), 200.0, timeframe="1h"),
        ]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars_1h)

        # Window: 14:15 onwards excludes the 14:00 bar, keeps the 15:00 bar.
        with self.Session() as db:
            res = bar_repository.get_bars(
                db,
                "AAPL",
                "1h",
                from_ts=datetime(2025, 1, 2, 14, 15),
            )
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].timestamp.hour, 15)


class TestFromTsToTsCap(unittest.TestCase):
    """Phase 3.7: get_bars is a straight indexed query — it applies exactly
    the filters the caller passes (symbol+timeframe, optionally from_ts,
    optionally to_ts, optionally limit) and nothing more. The Phase 3.1
    behaviour these tests originally covered — auto-capping an unbounded
    ``from_ts``-only query at ``now()`` on the read-time-resampled path —
    no longer exists (there is no read-time resample path to protect from
    an unbounded table scan); these tests now document that no such
    implicit filter is added."""

    def _spy_now(self):
        """Patch ``datetime.now`` with a fixed time for deterministic testing."""
        import datetime
        import unittest.mock

        original = datetime.datetime

        class FixedDatetime(original):
            @staticmethod
            def now(tz=None):
                return original(2025, 6, 15, 12, 0, 0, tzinfo=tz)

        return unittest.mock.patch.object(datetime, "datetime", FixedDatetime)

    def _make_db_with_filter_capture(self):
        """Build a mock Session that records the cumulative filter chain
        as SQLAlchemy filter conditions are added. We use a list of args
        that the chain accumulates via .filter()."""
        from unittest.mock import MagicMock

        from backend.repositories import bar_repository

        # Prime the cache so we don't hit sqlite_master.
        bar_repository._UNIQUE_CONSTRAINT_CACHE[("bars", ("symbol", "timeframe", "timestamp"))] = (
            True
        )

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        # db.execute is the only side-effecting call; we don't need it.
        db.execute.return_value.fetchone.return_value = None

        # Each .filter() returns the same chain object that records the call.
        chain = MagicMock()
        chain.filter = MagicMock(return_value=chain)
        chain.order_by = MagicMock(return_value=chain)
        chain.limit = MagicMock(return_value=chain)
        chain.all = MagicMock(return_value=[])
        db.query.return_value = chain

        return db, chain

    def test_from_ts_no_to_ts_no_limit_is_unbounded(self):
        """With from_ts but no to_ts or limit, no implicit to_ts cap is added."""
        from datetime import datetime

        from backend.repositories import bar_repository

        db, chain = self._make_db_with_filter_capture()

        with self._spy_now():
            bar_repository.get_bars(
                db,
                "AAPL",
                "1d",
                from_ts=datetime(2025, 1, 2, 0, 0),
            )

        # Expected filter chain: symbol+timeframe, from_ts. No to_ts cap.
        self.assertEqual(
            chain.filter.call_count,
            2,
            "expected 2 filter calls: symbol+tf, from_ts (no implicit to_ts cap)",
        )
        self.assertEqual(chain.order_by.call_count, 1)

    def test_resampled_with_explicit_limit_not_capped(self):
        """When ``limit`` is supplied, no implicit to_ts filter is added."""
        from datetime import datetime

        from backend.repositories import bar_repository

        db, chain = self._make_db_with_filter_capture()

        with self._spy_now():
            bar_repository.get_bars(
                db,
                "AAPL",
                "1d",
                from_ts=datetime(2025, 1, 1),
                limit=5,
            )

        # Expected filter chain: symbol+timeframe, from_ts. No to_ts cap.
        self.assertEqual(
            chain.filter.call_count, 2, "expected 2 filter calls: symbol+tf, from_ts (no to_ts cap)"
        )

    def test_resampled_with_to_ts_not_overridden(self):
        """When ``to_ts`` is provided explicitly, it is used as-is."""
        from datetime import datetime

        from backend.repositories import bar_repository

        db, chain = self._make_db_with_filter_capture()

        with self._spy_now():
            bar_repository.get_bars(
                db,
                "AAPL",
                "1d",
                from_ts=datetime(2025, 1, 2, 0, 0),
                to_ts=datetime(2025, 1, 5, 0, 0),
            )

        # 3 filter calls: symbol+tf, to_ts (user-provided), from_ts.
        self.assertEqual(chain.filter.call_count, 3)

    def test_1m_fast_path_not_affected(self):
        """The 1m path does not apply any implicit to_ts filter either."""
        from datetime import datetime

        from backend.repositories import bar_repository

        db, chain = self._make_db_with_filter_capture()

        with self._spy_now():
            bar_repository.get_bars(
                db,
                "AAPL",
                "1m",
                from_ts=datetime(2025, 1, 1),
            )

        # 1m path: only symbol+tf filter + from_ts filter. No to_ts cap.
        self.assertEqual(chain.filter.call_count, 2, "1m path: symbol+tf + from_ts (no to_ts cap)")


class TestWideningHours(unittest.TestCase):
    """Phase 3.7: ``_WIDENING_HOURS`` is unused dead code — it was Phase
    3.1's table controlling how far the lower bound was widened on the
    (since-removed) read-time resample path. get_bars no longer widens
    from_ts for any timeframe; every TF is a direct-row query. These tests
    document that current behaviour (and the dict's literal values, kept
    around in case a future read-time optimization needs them again).
    """

    def test_widening_hours_table_values(self):
        from backend.repositories import bar_repository

        # Dict values are unchanged, but unused by get_bars/_fetch_bars —
        # see class docstring.
        self.assertEqual(bar_repository._WIDENING_HOURS["1d"], 24)
        self.assertEqual(bar_repository._WIDENING_HOURS["1wk"], 168)
        self.assertEqual(bar_repository._WIDENING_HOURS["5m"], 0)
        self.assertEqual(bar_repository._WIDENING_HOURS["1h"], 1)
        self.assertEqual(bar_repository._WIDENING_HOURS["4h"], 4)

    def test_1wk_from_ts_not_widened(self):
        """1wk fetch uses from_ts as-is — no lower-bound widening."""
        from datetime import datetime

        from backend.repositories import bar_repository

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        db.execute.return_value.fetchone.return_value = None
        chain = MagicMock()
        chain.filter = MagicMock(return_value=chain)
        chain.order_by = MagicMock(return_value=chain)
        chain.limit = MagicMock(return_value=chain)
        chain.all = MagicMock(return_value=[])
        db.query.return_value = chain

        # Prime the unique-constraint cache to bypass the sqlite_master check.
        bar_repository._UNIQUE_CONSTRAINT_CACHE[("bars", ("symbol", "timeframe", "timestamp"))] = (
            True
        )

        from_ts = datetime(2025, 6, 1, 0, 0)
        bar_repository.get_bars(db, "AAPL", "1wk", from_ts=from_ts)

        filter_args = [
            call.args[0]
            for call in chain.filter.call_args_list
            if call.args and hasattr(call.args[0], "right")
        ]
        self.assertTrue(
            any(getattr(arg.right, "value", None) == from_ts for arg in filter_args),
            f"1wk path should not widen from_ts; got {filter_args}",
        )

    def test_1d_from_ts_not_widened(self):
        """1d fetch uses from_ts as-is — no lower-bound widening."""
        from datetime import datetime

        from backend.repositories import bar_repository

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        db.execute.return_value.fetchone.return_value = None
        chain = MagicMock()
        chain.filter = MagicMock(return_value=chain)
        chain.order_by = MagicMock(return_value=chain)
        chain.limit = MagicMock(return_value=chain)
        chain.all = MagicMock(return_value=[])
        db.query.return_value = chain

        bar_repository._UNIQUE_CONSTRAINT_CACHE[("bars", ("symbol", "timeframe", "timestamp"))] = (
            True
        )

        from_ts = datetime(2025, 6, 1, 0, 0)
        bar_repository.get_bars(db, "AAPL", "1d", from_ts=from_ts)

        filter_args = [
            call.args[0]
            for call in chain.filter.call_args_list
            if call.args and hasattr(call.args[0], "right")
        ]
        self.assertTrue(
            any(getattr(arg.right, "value", None) == from_ts for arg in filter_args),
            f"1d path should not widen from_ts; got {filter_args}",
        )

    def test_1m_path_not_widened(self):
        """1m fast path uses the original from_ts without widening."""
        from datetime import datetime

        from backend.repositories import bar_repository

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        db.execute.return_value.fetchone.return_value = None
        chain = MagicMock()
        chain.filter = MagicMock(return_value=chain)
        chain.order_by = MagicMock(return_value=chain)
        chain.limit = MagicMock(return_value=chain)
        chain.all = MagicMock(return_value=[])
        db.query.return_value = chain

        bar_repository._UNIQUE_CONSTRAINT_CACHE[("bars", ("symbol", "timeframe", "timestamp"))] = (
            True
        )

        from_ts = datetime(2025, 6, 1, 0, 0)
        bar_repository.get_bars(db, "AAPL", "1m", from_ts=from_ts)

        filter_args = [
            call.args[0]
            for call in chain.filter.call_args_list
            if call.args and hasattr(call.args[0], "right")
        ]
        # The from_ts filter should be the original value, NOT widened.
        # (1m is not resampled, so widen_hours lookup is skipped.)
        self.assertTrue(
            any(getattr(arg.right, "value", None) == from_ts for arg in filter_args),
            f"1m path should not widen from_ts; got {filter_args}",
        )


class TestUniqueConstraintCache(unittest.TestCase):
    """Phase 3.1: ``_has_unique_constraint`` is called on every ``upsert_bars``
    invocation. The result is constant for the process lifetime (the schema
    only changes via Alembic migrations which restart the server), so it
    must be cached after the first call."""

    def setUp(self):
        # Reset the module-level cache so each test starts clean.
        from backend.repositories import bar_repository

        bar_repository._UNIQUE_CONSTRAINT_CACHE.clear()

    def _make_db(self) -> MagicMock:
        """A Session-shaped mock whose ``.execute()`` returns a fetchone
        result indicating the unique index exists.  Must set ``db.bind.dialect.name``
        to ``"sqlite"`` so the function enters the real query path —
        an unset/mocked ``db.bind`` is truthy MagicMock and the function
        short-circuits to ``True`` (non-SQLite path) without any DB call."""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        result = MagicMock()
        result.fetchone.return_value = ("uq_bars_symbol_timeframe_timestamp",)
        db.execute.return_value = result
        return db

    def test_cache_hit_skips_sqlite_master_query(self):
        """Second call with the same (table, columns) does NOT query sqlite_master."""
        from backend.repositories import bar_repository

        db = self._make_db()
        # First call populates the cache.
        first = bar_repository._has_unique_constraint(
            db, "bars", ("symbol", "timeframe", "timestamp")
        )
        self.assertTrue(first)
        self.assertEqual(db.execute.call_count, 1)

        # Second and third calls reuse the cache — no extra DB queries.
        bar_repository._has_unique_constraint(db, "bars", ("symbol", "timeframe", "timestamp"))
        bar_repository._has_unique_constraint(db, "bars", ("symbol", "timeframe", "timestamp"))
        self.assertEqual(db.execute.call_count, 1)  # still 1

    def test_cache_miss_on_different_columns(self):
        """Different (table, columns) keys do not share cached results."""
        from backend.repositories import bar_repository

        db = self._make_db()
        bar_repository._has_unique_constraint(db, "bars", ("symbol", "timeframe", "timestamp"))
        # Different columns → new cache key → new sqlite_master query.
        bar_repository._has_unique_constraint(db, "bars", ("symbol",))
        self.assertEqual(db.execute.call_count, 2)

    def test_cache_miss_on_different_table(self):
        """Different table name → separate cache entry."""
        from backend.repositories import bar_repository

        db = self._make_db()
        bar_repository._has_unique_constraint(db, "bars", ("symbol",))
        bar_repository._has_unique_constraint(db, "quotes", ("symbol",))
        self.assertEqual(db.execute.call_count, 2)

    def test_non_sqlite_dialect_does_not_query(self):
        """Postgres/other dialects short-circuit to True without any DB query."""
        from backend.repositories import bar_repository

        db = MagicMock()
        db.bind.dialect.name = "postgresql"
        result = bar_repository._has_unique_constraint(db, "bars", ("symbol",))
        self.assertTrue(result)
        db.execute.assert_not_called()
        # Subsequent calls also bypass the DB.
        bar_repository._has_unique_constraint(db, "bars", ("symbol",))
        db.execute.assert_not_called()

    def test_upsert_bars_does_not_repeat_query(self):
        """The whole point: repeated ``upsert_bars`` calls don't repeat the
        sqlite_master lookup after the first one.

        We patch ``db.execute`` with a sentinel wrapper that counts calls,
        wrapping the real SQLAlchemy ``Session.execute`` so the SQLite query
        still runs (and the cache priming is real). Subsequent cache hits
        don't reach the wrapper.
        """
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from backend.repositories import bar_repository

        engine = create_engine("sqlite:///:memory:")
        Session = sessionmaker(bind=engine)
        # sqlite_master is read at engine init; build a real session first
        # so the dialect is "sqlite" before we wrap execute.
        with Session() as db:
            real_execute = db.execute
            call_counter = {"n": 0}

            def counting_execute(*a, **kw):
                call_counter["n"] += 1
                return real_execute(*a, **kw)

            db.execute = counting_execute

            # Prime the cache.
            bar_repository._has_unique_constraint(db, "bars", ("symbol", "timeframe", "timestamp"))
            self.assertEqual(call_counter["n"], 1, "first call must hit sqlite_master")

            # Five more calls — all cache hits, no new sqlite_master queries.
            for _ in range(5):
                bar_repository._has_unique_constraint(
                    db, "bars", ("symbol", "timeframe", "timestamp")
                )
            self.assertEqual(call_counter["n"], 1, "cache hits must not call sqlite_master")


class TestFindDuplicateCalendarBars(unittest.TestCase):
    """Regression coverage for the 2026-09-09 duplicate-1d-bar incident:
    webull stamps 1d bars at 00:00, yahoo_finance at 09:30 — two rows for
    the same trading day, neither caught by the exact-timestamp unique
    constraint. find_duplicate_calendar_bars is the audit that would have
    caught it."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        BarModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_no_duplicates_on_clean_data(self):
        bars = [
            _make_bar("AAPL", datetime(2025, 1, 2, 0, 0), timeframe="1d"),
            _make_bar("AAPL", datetime(2025, 1, 3, 0, 0), timeframe="1d"),
        ]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars)
        with self.Session() as db:
            dupes = bar_repository.find_duplicate_calendar_bars(db, "1d")
        self.assertEqual(dupes, [])

    def test_detects_same_day_different_timestamp(self):
        """Two rows for the same calendar day at different times of day —
        exactly the webull-00:00 vs yahoo_finance-09:30 pattern — must be
        flagged even though their exact timestamps differ (so the DB's
        unique constraint alone never catches this)."""
        bars = [
            _make_bar("AAPL", datetime(2025, 1, 2, 0, 0), close=100.0, timeframe="1d"),
            _make_bar("AAPL", datetime(2025, 1, 2, 9, 30), close=102.5, timeframe="1d"),
        ]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars)
        with self.Session() as db:
            dupes = bar_repository.find_duplicate_calendar_bars(db, "1d")
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["symbol"], "AAPL")
        self.assertEqual(dupes[0]["date"], "2025-01-02")
        self.assertEqual(dupes[0]["count"], 2)
        closes = {r["close"] for r in dupes[0]["rows"]}
        self.assertEqual(closes, {100.0, 102.5})

    def test_does_not_flag_different_symbols_or_days(self):
        bars = [
            _make_bar("AAPL", datetime(2025, 1, 2, 0, 0), timeframe="1d"),
            _make_bar("MSFT", datetime(2025, 1, 2, 9, 30), timeframe="1d"),  # different symbol
            _make_bar("AAPL", datetime(2025, 1, 3, 9, 30), timeframe="1d"),  # different day
        ]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars)
        with self.Session() as db:
            dupes = bar_repository.find_duplicate_calendar_bars(db, "1d")
        self.assertEqual(dupes, [])

    def test_rejects_intraday_timeframe(self):
        """Only calendar-based timeframes (1d/1wk) have this failure mode —
        an exact-timestamp collision on a fixed-width intraday bucket IS
        the correct de-dup key, so this check would misfire there."""
        with self.Session() as db:
            with self.assertRaises(ValueError):
                bar_repository.find_duplicate_calendar_bars(db, "1m")

    def test_symbol_filter_scopes_the_search(self):
        bars = [
            _make_bar("AAPL", datetime(2025, 1, 2, 0, 0), timeframe="1d"),
            _make_bar("AAPL", datetime(2025, 1, 2, 9, 30), timeframe="1d"),
            _make_bar("MSFT", datetime(2025, 1, 2, 0, 0), timeframe="1d"),
            _make_bar("MSFT", datetime(2025, 1, 2, 9, 30), timeframe="1d"),
        ]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars)
        with self.Session() as db:
            dupes = bar_repository.find_duplicate_calendar_bars(db, "1d", symbol="AAPL")
        self.assertEqual(len(dupes), 1)
        self.assertEqual(dupes[0]["symbol"], "AAPL")


class TestUpsertBarsSessionRecompute(unittest.TestCase):
    """upsert_bars must recompute session from the bar's own timestamp for
    every 1m bar, never trust the caller/provider's value.

    Regression coverage for a live bug (2026-09-09): WebullProvider tags
    session correctly on bars it returns, but Alpaca (used as a 1m
    gap-fill provider) returns genuine premarket ticks from its own IEX
    feed without tagging them — its Bar objects default to 'regular'
    (Bar model's default) regardless of the real timestamp. That mistagged
    bar then passed ingestion_service._resample_and_upsert's
    session='regular' filter and leaked into a resampled 5m/15m/30m
    bucket. _make_bar() defaults provider="yahoo_finance" and never sets
    session — exactly this failure shape.
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        BarModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_untagged_premarket_bar_is_corrected_on_write(self):
        """A bar timestamped 08:16 ET with no explicit session (defaults
        to 'regular' per the Bar model) must be stored as 'premarket' —
        upsert_bars must override the untrustworthy default."""
        bar = _make_bar("NVDA", datetime(2026, 9, 9, 8, 16), timeframe="1m")
        self.assertEqual(bar.session, "regular")  # the buggy starting state

        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])

        with self.Session() as db:
            row = db.query(BarModel).filter(BarModel.symbol == "NVDA").first()
        self.assertEqual(row.session, "premarket")

    def test_untagged_after_hours_bar_is_corrected_on_write(self):
        bar = _make_bar("NVDA", datetime(2026, 9, 9, 17, 30), timeframe="1m")
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            row = db.query(BarModel).filter(BarModel.symbol == "NVDA").first()
        self.assertEqual(row.session, "after_hours")

    def test_untagged_regular_hours_bar_stays_regular(self):
        bar = _make_bar("NVDA", datetime(2026, 9, 9, 10, 0), timeframe="1m")
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            row = db.query(BarModel).filter(BarModel.symbol == "NVDA").first()
        self.assertEqual(row.session, "regular")

    def test_incorrectly_tagged_1m_bar_is_overridden_not_trusted(self):
        """Even a bar that explicitly (wrongly) claims 'regular' must be
        corrected — upsert_bars never trusts the incoming value for 1m."""
        bar = _make_bar("NVDA", datetime(2026, 9, 9, 6, 0), timeframe="1m")
        bar.session = "regular"  # explicitly wrong, simulating the Alpaca bug
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            row = db.query(BarModel).filter(BarModel.symbol == "NVDA").first()
        self.assertEqual(row.session, "premarket")

    def test_sub_hour_1m_derived_timeframes_are_also_recomputed(self):
        """By request 2026-09-09, session recompute extends to the
        sub-hour timeframes resampled from 1m (2m/3m/5m/15m/30m) — a
        premarket-timestamped 5m bar must be tagged 'premarket', not
        left at the Bar model's 'regular' default."""
        bar = _make_bar("NVDA", datetime(2026, 9, 9, 8, 0), timeframe="5m")
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            row = db.query(BarModel).filter(BarModel.symbol == "NVDA").first()
        self.assertEqual(row.session, "premarket")

    def test_1h_and_4h_session_is_trusted_from_the_caller_not_recomputed(self):
        """1h/4h buckets are wall-clock windows (1h floors to :00, 4h to
        00:00/04:00/08:00/...), not aligned to the 9:30 ET open, so a
        bucket like 08:00-12:00 (4h) or 09:00-10:00 (1h) genuinely spans
        two sessions. Classifying from the bucket-start timestamp alone
        mislabeled a whole straddling bar with only its first sub-session
        (found live 2026-09-23: the 4h Multi-Timeframe Trend card showed
        "Premarket" during regular hours). Only the resample builders
        (backend.market_data.services.ingestion_service) have the member
        bars needed to compute this correctly via aggregate_bar_session(),
        so upsert_bars must trust whatever session they set for 1h/4h
        instead of overriding it — unlike 1m/2m/3m/5m/15m/30m above, whose
        sub-30-min buckets never straddle a session edge."""
        bar = _make_bar("NVDA", datetime(2026, 9, 9, 8, 0), timeframe="4h")
        bar.session = "mixed"  # what aggregate_bar_session() would compute for 08:00-12:00
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            row = db.query(BarModel).filter(BarModel.symbol == "NVDA").first()
        self.assertEqual(row.session, "mixed")

    def test_1h_untagged_defaults_to_regular_not_reclassified(self):
        """An 1h bar with no explicit session (e.g. a provider-fetched
        historical backfill row, which never sets extended-hours session)
        keeps the Bar model's 'regular' default — upsert_bars no longer
        reclassifies 1h/4h from the bucket timestamp."""
        bar = _make_bar("NVDA", datetime(2026, 9, 9, 8, 0), timeframe="1h")
        self.assertEqual(bar.session, "regular")
        with self.Session() as db:
            bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            row = db.query(BarModel).filter(BarModel.symbol == "NVDA").first()
        self.assertEqual(row.session, "regular")

    def test_fallback_merge_path_also_recomputes_session(self):
        """The no-unique-constraint fallback path (existing.session = ...)
        must apply the same recompute, not just the bulk ON CONFLICT path."""
        from unittest.mock import patch

        bar = _make_bar("NVDA", datetime(2026, 9, 9, 8, 30), timeframe="1m")
        with patch.object(bar_repository, "_has_unique_constraint", return_value=False):
            with self.Session() as db:
                bar_repository.upsert_bars(db, [bar])
        with self.Session() as db:
            row = db.query(BarModel).filter(BarModel.symbol == "NVDA").first()
        self.assertEqual(row.session, "premarket")


if __name__ == "__main__":
    unittest.main()


class TestUpsertBarsBulkWrite(unittest.TestCase):
    """upsert_bars writes with ONE executemany, not a giant multi-VALUES statement.

    A multi-VALUES statement for a big batch binds rows x 12 variables: SQLite
    rejects it past SQLITE_MAX_VARIABLE_NUMBER ("too many SQL variables" lost
    whole 1m-ingest cycles in production), and even below the limit SQLAlchemy
    compiling thousands of bound parameters is pure-Python work that holds the
    GIL (53 ms for 721 rows vs 3.9 ms with executemany, measured) and starved the
    API's event loop on every ingest cycle.
    """

    def setUp(self):
        # _has_unique_constraint caches its answer process-wide; an earlier test
        # can leave False behind, which would silently route these tests through
        # the per-row fallback instead of the bulk path under test.
        patcher = patch.dict(bar_repository._UNIQUE_CONSTRAINT_CACHE, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _engine(self, variable_limit=None):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        if variable_limit is not None:

            @event.listens_for(engine, "connect")
            def _lower_limit(dbapi_conn, _record):
                dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, variable_limit)

        BarModel.__table__.create(engine, checkfirst=True)
        self.addCleanup(engine.dispose)
        return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)

    @staticmethod
    def _bars(n, close=100.0):
        base = datetime(2025, 1, 1, 9, 30)
        return [_make_bar("AAPL", base + timedelta(minutes=i), close + i) for i in range(n)]

    def test_one_executemany_with_a_twelve_variable_statement(self):
        engine, Session = self._engine()
        seen = []
        event.listen(
            engine,
            "before_cursor_execute",
            lambda conn, cur, stmt, params, ctx, many: (
                seen.append((stmt, many, len(params) if many else 1))
                if stmt.lstrip().upper().startswith("INSERT")
                else None
            ),
        )
        with Session() as db:
            written = bar_repository.upsert_bars(db, self._bars(250))
        self.assertEqual(written, 250)
        self.assertEqual(len(seen), 1, "expected a single INSERT execution")
        stmt, many, n_rows = seen[0]
        self.assertTrue(many)
        self.assertEqual(n_rows, 250)
        self.assertLessEqual(stmt.count("?"), 12 + 8, "statement must not embed per-row parameters")

    def test_inserts_and_reports_rowcount(self):
        _, Session = self._engine()
        with Session() as db:
            self.assertEqual(bar_repository.upsert_bars(db, self._bars(25)), 25)
        with Session() as db:
            self.assertEqual(len(bar_repository.get_bars(db, "AAPL", "1m")), 25)

    def test_updates_existing_rows_last_write_wins(self):
        _, Session = self._engine()
        with Session() as db:
            bar_repository.upsert_bars(db, self._bars(20, close=100.0))
        with Session() as db:
            written = bar_repository.upsert_bars(db, self._bars(20, close=500.0))
        self.assertEqual(written, 20)
        with Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1m")
        self.assertEqual(len(stored), 20)
        self.assertEqual(sorted(b.close for b in stored), [500.0 + i for i in range(20)])

    def test_batch_beyond_the_sqlite_variable_limit_succeeds(self):
        """Emulate the production failure by lowering SQLite's own variable limit:
        2000 rows x 12 = 24,000 variables vs a limit of 1200."""
        _, Session = self._engine(variable_limit=1200)
        with Session() as db:
            # Self-check that the emulation is real: the old statement shape fails.
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            rows = [
                {
                    "symbol": "AAPL",
                    "timeframe": "1m",
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1,
                    "timestamp": datetime(2025, 1, 1) + timedelta(minutes=i),
                    "provider": "p",
                    "data_status": "LIVE",
                    "source": "raw",
                    "session": "regular",
                }
                for i in range(200)
            ]
            with self.assertRaises(OperationalError) as ctx:
                db.execute(sqlite_insert(BarModel).values(rows))
            self.assertIn("too many SQL variables", str(ctx.exception))
            db.rollback()
        with Session() as db:
            self.assertEqual(bar_repository.upsert_bars(db, self._bars(2000)), 2000)

    def test_batch_is_all_or_nothing_if_a_row_fails(self):
        _, Session = self._engine()
        bars = self._bars(10)
        bars[7].volume = None  # violates NOT NULL for one row mid-batch
        with Session() as db:
            with self.assertRaises(IntegrityError):
                bar_repository.upsert_bars(db, bars)
            db.rollback()
        with Session() as db:
            self.assertEqual(bar_repository.get_bars(db, "AAPL", "1m"), [])
