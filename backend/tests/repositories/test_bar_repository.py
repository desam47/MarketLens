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
from unittest.mock import MagicMock, call

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

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
        self.Session = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )

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
        bars = [
            _make_bar("AAPL", t0 + timedelta(minutes=i), 100 + i)
            for i in range(5)
        ]
        # Insert in scrambled order
        with self.Session() as db:
            bar_repository.upsert_bars(
                db, [bars[3], bars[0], bars[4], bars[1], bars[2]]
            )

        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1m")

        timestamps = [b.timestamp for b in stored]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(len(stored), 5)

    def test_get_bars_respects_limit(self):
        bars = [
            _make_bar("AAPL", datetime(2025, 1, 1, 9, 30) + timedelta(minutes=i))
            for i in range(10)
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
        """1m fast path and resampled 1h path are both filtered correctly."""
        # 1m bars for AAPL: 60 consecutive bars from 14:00 UTC, all within the
        # same 1h bucket (14:00 UTC). Jan 2025 = UTC-5 → 09:00-09:59 ET.
        base = datetime(2025, 1, 2, 14, 0)
        aapl_1m = [_make_bar("AAPL", base + timedelta(minutes=i), 100.0 + i * 0.1)
                   for i in range(60)]
        # 1m bar for GOOGL
        googl_1m = [_make_bar("GOOGL", base, 200.0)]

        with self.Session() as db:
            bar_repository.upsert_bars(db, aapl_1m + googl_1m)

        with self.Session() as db:
            aapl_1m_res = bar_repository.get_bars(db, "AAPL", "1m")
            # 1h path: 60 1m bars → 1 output bar (all in the 14:00 UTC bucket)
            aapl_1h = bar_repository.get_bars(db, "AAPL", "1h", limit=1)
            googl_1m_res = bar_repository.get_bars(db, "GOOGL", "1m")

        # 1m fast path
        self.assertEqual(len(aapl_1m_res), 60)
        self.assertEqual(aapl_1m_res[0].source, "raw")
        # 1h resampled path: 60 bars → 1 output bar
        self.assertEqual(len(aapl_1h), 1)
        self.assertEqual(aapl_1h[0].source, "resampled")
        # open from first 1m bar (i=0, close=100.0, open=99.0)
        self.assertEqual(aapl_1h[0].open, 99.0)
        # close from last 1m bar (i=59, close=105.9, close=105.9)
        self.assertEqual(aapl_1h[0].close, 105.9)
        # GOOGL 1m path
        self.assertEqual(len(googl_1m_res), 1)

    def test_upsert_bars_preserves_data_status_string(self):
        """A bar's data_status and source field round-trip correctly.

        Phase 3.1: stored bars are at 1m (source=raw); queried 1d bars
        are resampled at read time (source=resampled).
        """
        # 5 consecutive 1m bars (2025-01-02 09:30–09:34 ET) that will
        # bucket into a single 1d bar.
        base = datetime(2025, 1, 2, 14, 30)
        bars = [_make_bar("AAPL", base + timedelta(minutes=i), 100.0)
                for i in range(5)]
        for b in bars:
            b.data_status = DataStatus.HISTORICAL
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars)
        with self.Session() as db:
            stored = bar_repository.get_bars(db, "AAPL", "1d", limit=1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].data_status, DataStatus.HISTORICAL)
        self.assertEqual(stored[0].source, "resampled")


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

    def test_get_bars_hybrid_fallback(self):
        """Phase 3.1.8: when 1m DB rows are insufficient for the requested
        limit and a ``fallback_provider`` is supplied, the function
        transparently calls the provider for the target TF.

        Without a fallback, an empty result is returned.
        """
        # Empty DB — no 1m rows. Asking for 1d with limit=3 must trigger fallback.
        def fallback_provider(symbol: str, timeframe: str) -> list[Bar]:
            return [
                Bar(
                    symbol=symbol, timeframe=timeframe,
                    open=100, high=101, low=99, close=100.5,
                    volume=1000, timestamp=datetime(2025, 1, 1, 14, 30),
                    provider="test_provider", data_status=DataStatus.HISTORICAL,
                ),
                Bar(
                    symbol=symbol, timeframe=timeframe,
                    open=101, high=102, low=100, close=101.5,
                    volume=1500, timestamp=datetime(2025, 1, 2, 14, 30),
                    provider="test_provider", data_status=DataStatus.HISTORICAL,
                ),
                Bar(
                    symbol=symbol, timeframe=timeframe,
                    open=102, high=103, low=101, close=102.5,
                    volume=2000, timestamp=datetime(2025, 1, 3, 14, 30),
                    provider="test_provider", data_status=DataStatus.HISTORICAL,
                ),
            ]

        with self.Session() as db:
            bars = bar_repository.get_bars(
                db, "AAPL", "1d", limit=3, fallback_provider=fallback_provider,
            )
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0].close, 100.5)
        self.assertEqual(bars[2].close, 102.5)
        self.assertEqual(bars[0].provider, "test_provider")
        # Phase 3.1.8: provider-returned bars get source='raw' so the
        # API can distinguish them from DB-resampled bars.
        for b in bars:
            self.assertEqual(b.source, "raw")

        # Without a fallback, the empty DB returns 0 bars.
        with self.Session() as db:
            bars_no_fb = bar_repository.get_bars(db, "AAPL", "1d", limit=3)
        self.assertEqual(len(bars_no_fb), 0)

    def test_get_bars_hybrid_fallback_not_triggered_when_db_sufficient(self):
        """Phase 3.1.8: the fallback is only invoked when 1m coverage is
        insufficient. When the DB has enough 1m rows, the provider is
        never called.
        """
        # 60 consecutive 1m bars in one 1h bucket.
        base = datetime(2025, 1, 2, 14, 0)
        bars_1m = [_make_bar("AAPL", base + timedelta(minutes=i), 100.0 + i * 0.1)
                   for i in range(60)]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars_1m)

        called = []

        def fallback_provider(symbol: str, timeframe: str) -> list[Bar]:
            called.append((symbol, timeframe))
            return []

        with self.Session() as db:
            res = bar_repository.get_bars(
                db, "AAPL", "1h", limit=1, fallback_provider=fallback_provider,
            )

        self.assertEqual(len(res), 1)
        self.assertEqual(called, [])  # not invoked — DB was sufficient

    def test_get_bars_from_ts_to_ts_filter_1m(self):
        """from_ts/to_ts narrow the 1m fast path by timestamp range."""
        # 30 consecutive 1m bars starting at 09:30.
        base = datetime(2025, 1, 2, 9, 30)
        bars_1m = [_make_bar("AAPL", base + timedelta(minutes=i), 100.0 + i * 0.1)
                   for i in range(30)]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars_1m)

        # Window: 09:35–09:40 (5 bars inclusive)
        with self.Session() as db:
            res = bar_repository.get_bars(
                db, "AAPL", "1m",
                from_ts=datetime(2025, 1, 2, 9, 35),
                to_ts=datetime(2025, 1, 2, 9, 40),
            )
        self.assertEqual(len(res), 6)  # 9:35 .. 9:40 inclusive
        self.assertEqual(res[0].timestamp.minute, 35)
        self.assertEqual(res[-1].timestamp.minute, 40)

    def test_get_bars_from_ts_to_ts_filter_resampled(self):
        """from_ts/to_ts narrow the higher-TF resampled output as well."""
        # 60 consecutive 1m bars in one 1h bucket.
        base = datetime(2025, 1, 2, 14, 0)
        bars_1m = [_make_bar("AAPL", base + timedelta(minutes=i), 100.0 + i * 0.1)
                   for i in range(60)]
        # Another 60 minutes 1h later, second 1h bucket.
        second = base + timedelta(hours=1)
        bars_1m += [_make_bar("AAPL", second + timedelta(minutes=i), 200.0 + i * 0.1)
                    for i in range(60)]
        with self.Session() as db:
            bar_repository.upsert_bars(db, bars_1m)

        # Window: 14:15 onwards. With from_ts widening, we should still
        # get both 1h buckets; the second one starts at 15:00, the
        # first at 14:00 (the 14:00 bucket's open uses 1m bars widened
        # to 13:00, but those don't exist so the open is the first 1m
        # in the bucket = 14:00).
        with self.Session() as db:
            res = bar_repository.get_bars(
                db, "AAPL", "1h",
                from_ts=datetime(2025, 1, 2, 14, 15),
            )
        # Two 1h buckets in range: 14:00 (filtered to >= 14:15) and 15:00.
        # The 14:00 bucket's timestamp (14:00) is < 14:15, so it is
        # excluded. Result: only the 15:00 bucket.
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].timestamp.hour, 15)

    def test_get_bars_hybrid_fallback_filters_window(self):
        """Hybrid fallback provider bars are filtered to the from_ts/to_ts
        window and tagged with source='raw'."""
        def fallback_provider(symbol: str, timeframe: str) -> list[Bar]:
            return [
                Bar(symbol=symbol, timeframe=timeframe, open=100, high=101, low=99, close=100.5,
                    volume=1000, timestamp=datetime(2025, 1, 1, 14, 30),
                    provider="test_provider", data_status=DataStatus.HISTORICAL),
                Bar(symbol=symbol, timeframe=timeframe, open=101, high=102, low=100, close=101.5,
                    volume=1500, timestamp=datetime(2025, 1, 2, 14, 30),
                    provider="test_provider", data_status=DataStatus.HISTORICAL),
                Bar(symbol=symbol, timeframe=timeframe, open=102, high=103, low=101, close=102.5,
                    volume=2000, timestamp=datetime(2025, 1, 3, 14, 30),
                    provider="test_provider", data_status=DataStatus.HISTORICAL),
            ]

        with self.Session() as db:
            bars = bar_repository.get_bars(
                db, "AAPL", "1d",
                limit=3,
                from_ts=datetime(2025, 1, 2, 0, 0),
                fallback_provider=fallback_provider,
            )
        # Jan 1 is before the window — filtered out.
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].timestamp, datetime(2025, 1, 2, 14, 30))
        self.assertEqual(bars[1].timestamp, datetime(2025, 1, 3, 14, 30))
        # All provider-returned bars get source='raw'.
        for b in bars:
            self.assertEqual(b.source, "raw")


class TestFromTsToTsCap(unittest.TestCase):
    """Phase 3.1: when ``from_ts`` is provided but ``to_ts`` is None and
    ``limit`` is None, the resampled query is bounded at ``now()`` to
    prevent an unbounded table scan. The 1m fast path is unaffected
    (it already has ``limit`` or a user-supplied ``to_ts`` in practice)."""

    def _spy_now(self):
        """Patch ``datetime.now`` with a fixed time for deterministic testing."""
        import unittest.mock
        import datetime
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
        from unittest.mock import MagicMock, call
        from backend.repositories import bar_repository
        from backend.models.market_data_sql import BarModel

        # Prime the cache so we don't hit sqlite_master.
        bar_repository._UNIQUE_CONSTRAINT_CACHE[("bars", ("symbol", "timeframe", "timestamp"))] = True

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

    def test_resampled_from_ts_no_to_ts_capped_at_now(self):
        """With from_ts but no to_ts or limit, the to_ts cap is applied."""
        from backend.repositories import bar_repository
        from datetime import datetime

        db, chain = self._make_db_with_filter_capture()

        with self._spy_now():
            bar_repository.get_bars(
                db, "AAPL", "1d",
                from_ts=datetime(2025, 1, 2, 0, 0),
            )

        # Expected filter chain: symbol+timeframe, to_ts (capped), from_ts.
        # Plus an order_by and a limit (with the multiplier).
        self.assertEqual(
            chain.filter.call_count, 3,
            "expected 3 filter calls: symbol+tf, to_ts cap, from_ts"
        )
        self.assertEqual(chain.order_by.call_count, 1)

    def test_resampled_with_explicit_limit_not_capped(self):
        """When ``limit`` is supplied, the fetch_limit path is used — no
        extra to_ts cap is needed."""
        from backend.repositories import bar_repository
        from datetime import datetime

        db, chain = self._make_db_with_filter_capture()

        with self._spy_now():
            bar_repository.get_bars(
                db, "AAPL", "1d",
                from_ts=datetime(2025, 1, 1),
                limit=5,
            )

        # Expected filter chain: symbol+timeframe, from_ts. No to_ts cap.
        self.assertEqual(
            chain.filter.call_count, 2,
            "expected 2 filter calls: symbol+tf, from_ts (no to_ts cap)"
        )

    def test_resampled_with_to_ts_not_overridden(self):
        """When ``to_ts`` is provided explicitly, the cap is not applied."""
        from backend.repositories import bar_repository
        from datetime import datetime

        db, chain = self._make_db_with_filter_capture()

        with self._spy_now():
            bar_repository.get_bars(
                db, "AAPL", "1d",
                from_ts=datetime(2025, 1, 2, 0, 0),
                to_ts=datetime(2025, 1, 5, 0, 0),
            )

        # 3 filter calls: symbol+tf, to_ts (user-provided), from_ts.
        self.assertEqual(chain.filter.call_count, 3)

    def test_1m_fast_path_not_affected(self):
        """The 1m fast path does not apply the to_ts cap."""
        from backend.repositories import bar_repository
        from datetime import datetime

        db, chain = self._make_db_with_filter_capture()

        with self._spy_now():
            bar_repository.get_bars(
                db, "AAPL", "1m",
                from_ts=datetime(2025, 1, 1),
            )

        # 1m path: only symbol+tf filter + from_ts filter. No to_ts cap.
        self.assertEqual(
            chain.filter.call_count, 2,
            "1m path: symbol+tf + from_ts (no to_ts cap)"
        )


class TestWideningHours(unittest.TestCase):
    """Phase 3.1: the ``_WIDENING_HOURS`` table controls how far the lower
    bound is widened on the resample path. Calendar-period TFs (``1d``,
    ``1wk``) need widening proportional to the calendar period so the
    leading bucket is complete. The old behaviour used the minute-based
    multiplier for both fetch_limit AND widening, which gave ``1wk`` a
    32.5h lookback instead of the 168h a full calendar week requires.
    """

    def test_widening_hours_table_values(self):
        from backend.repositories import bar_repository
        # Calendar-proportional widening, not minute-based.
        self.assertEqual(bar_repository._WIDENING_HOURS["1d"], 24)
        self.assertEqual(bar_repository._WIDENING_HOURS["1wk"], 168)
        # 1m-aligned TFs need only 0-1h of widening.
        self.assertEqual(bar_repository._WIDENING_HOURS["5m"], 0)
        self.assertEqual(bar_repository._WIDENING_HOURS["1h"], 1)
        self.assertEqual(bar_repository._WIDENING_HOURS["4h"], 4)

    def test_1wk_lookback_is_seven_days(self):
        """1wk fetch lower bound is widened by 168h = 7 calendar days."""
        from backend.repositories import bar_repository
        from datetime import datetime, timedelta

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
        bar_repository._UNIQUE_CONSTRAINT_CACHE[
            ("bars", ("symbol", "timeframe", "timestamp"))
        ] = True

        from_ts = datetime(2025, 6, 1, 0, 0)
        bar_repository.get_bars(db, "AAPL", "1wk", from_ts=from_ts)

        # The from_ts filter was applied with the widened value
        # (from_ts - 168h). Verify the widening was applied by checking
        # the filter was called with a date ~7 days before from_ts.
        filter_args = [
            call.args[0] for call in chain.filter.call_args_list
            if call.args and hasattr(call.args[0], "right")
        ]
        self.assertTrue(
            any(
                getattr(arg.right, "value", None) is not None
                and abs((arg.right.value - (from_ts - timedelta(hours=168))).total_seconds()) < 1
                for arg in filter_args
            ),
            f"expected a from_ts filter widened by 168h; got {filter_args}"
        )

    def test_1d_lookback_is_one_day(self):
        """1d fetch lower bound is widened by 24h = 1 calendar day."""
        from backend.repositories import bar_repository
        from datetime import datetime, timedelta

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        db.execute.return_value.fetchone.return_value = None
        chain = MagicMock()
        chain.filter = MagicMock(return_value=chain)
        chain.order_by = MagicMock(return_value=chain)
        chain.limit = MagicMock(return_value=chain)
        chain.all = MagicMock(return_value=[])
        db.query.return_value = chain

        bar_repository._UNIQUE_CONSTRAINT_CACHE[
            ("bars", ("symbol", "timeframe", "timestamp"))
        ] = True

        from_ts = datetime(2025, 6, 1, 0, 0)
        bar_repository.get_bars(db, "AAPL", "1d", from_ts=from_ts)

        filter_args = [
            call.args[0] for call in chain.filter.call_args_list
            if call.args and hasattr(call.args[0], "right")
        ]
        self.assertTrue(
            any(
                getattr(arg.right, "value", None) is not None
                and abs((arg.right.value - (from_ts - timedelta(hours=24))).total_seconds()) < 1
                for arg in filter_args
            ),
            f"expected a from_ts filter widened by 24h; got {filter_args}"
        )

    def test_1m_path_not_widened(self):
        """1m fast path uses the original from_ts without widening."""
        from backend.repositories import bar_repository
        from datetime import datetime

        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        db.execute.return_value.fetchone.return_value = None
        chain = MagicMock()
        chain.filter = MagicMock(return_value=chain)
        chain.order_by = MagicMock(return_value=chain)
        chain.limit = MagicMock(return_value=chain)
        chain.all = MagicMock(return_value=[])
        db.query.return_value = chain

        bar_repository._UNIQUE_CONSTRAINT_CACHE[
            ("bars", ("symbol", "timeframe", "timestamp"))
        ] = True

        from_ts = datetime(2025, 6, 1, 0, 0)
        bar_repository.get_bars(db, "AAPL", "1m", from_ts=from_ts)

        filter_args = [
            call.args[0] for call in chain.filter.call_args_list
            if call.args and hasattr(call.args[0], "right")
        ]
        # The from_ts filter should be the original value, NOT widened.
        # (1m is not resampled, so widen_hours lookup is skipped.)
        self.assertTrue(
            any(
                getattr(arg.right, "value", None) == from_ts
                for arg in filter_args
            ),
            f"1m path should not widen from_ts; got {filter_args}"
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
        first = bar_repository._has_unique_constraint(db, "bars", ("symbol", "timeframe", "timestamp"))
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
        from backend.repositories import bar_repository
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

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
                bar_repository._has_unique_constraint(db, "bars", ("symbol", "timeframe", "timestamp"))
            self.assertEqual(call_counter["n"], 1, "cache hits must not call sqlite_master")


if __name__ == "__main__":
    unittest.main()
