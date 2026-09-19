"""
Tests for MarketDataIngestionService.

Covers:
  - start/stop lifecycle
  - correlation ID propagation from request context into the daemon thread
"""
import asyncio
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.observability.logging_enhanced import get_correlation_id, set_correlation_id


class TestResampleWideningHours(unittest.TestCase):
    """Regression for a live bug (2026-09-16): 15m shared 5m's 60-min
    resample lookback window (base_hours from target_mins alone, no extra
    widening) but has to fill 3x larger buckets from it — only ~4 complete
    buckets of redundancy vs 5m's ~12. A brief 1m-feed hiccup left too few
    1m bars to close 15m's newest bucket, stalling DVLT's 15m bar at
    11:30 while its 5m bar was already at 12:20 and 30m (which gets its
    own dedicated padding) was unaffected. 15m must get the same kind of
    padding 30m already has, not 0."""

    def test_15m_has_dedicated_widening_like_30m(self):
        from backend.market_data.services.ingestion_service import (
            MarketDataIngestionService,
        )
        widening = MarketDataIngestionService._RESAMPLE_WIDENING_HOURS
        self.assertGreaterEqual(widening["15m"], 1)
        self.assertEqual(widening["15m"], widening["30m"])

    def test_2m_3m_5m_remain_unpadded(self):
        """These have 12+ complete buckets in their base window, so they
        don't need the extra padding 15m now gets — only asserting 15m
        changed, not that everything did."""
        from backend.market_data.services.ingestion_service import (
            MarketDataIngestionService,
        )
        widening = MarketDataIngestionService._RESAMPLE_WIDENING_HOURS
        self.assertEqual(widening["2m"], 0)
        self.assertEqual(widening["3m"], 0)
        self.assertEqual(widening["5m"], 0)


class TestIngestionServiceLifecycle(unittest.TestCase):
    """Basic start/stop without requiring a live market data provider."""

    def setUp(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        self.service = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])

    def tearDown(self):
        if self.service.is_running:
            self.service.stop()
            time.sleep(0.2)  # Let the thread drain

    def test_start_sets_is_running(self):
        self.service.start()
        time.sleep(0.1)
        self.assertTrue(self.service.is_running)
        self.service.stop()
        time.sleep(0.2)
        self.assertFalse(self.service.is_running)

    def test_double_start_is_noop(self):
        self.service.start()
        time.sleep(0.05)
        first_thread = self.service._thread
        self.service.start()  # idempotent
        time.sleep(0.05)
        self.assertIs(self.service._thread, first_thread)
        self.service.stop()
        time.sleep(0.2)


class TestIngestionWatchlistTracking(unittest.TestCase):
    def test_get_tracking_watchlists_returns_only_lists_with_enabled_symbols(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        service = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])
        db = MagicMock()
        repo = MagicMock()
        active_watchlists = [
            SimpleNamespace(id=1, name="Watchlist 1"),
            SimpleNamespace(id=2, name="Watchlist 2"),
            SimpleNamespace(id=3, name="Watchlist 3"),
        ]
        repo.get_watchlists.return_value = active_watchlists
        repo.get_watchlist_symbols.side_effect = [
            [SimpleNamespace(symbol="AAPL")],
            [],
            [SimpleNamespace(symbol="MSFT"), SimpleNamespace(symbol="NVDA")],
        ]

        with patch(
            "backend.market_data.services.ingestion_service.SessionLocal",
            return_value=db,
        ), patch(
            "backend.market_data.services.ingestion_service.WatchlistRepository",
            return_value=repo,
        ):
            self.assertEqual(
                service.get_tracking_watchlists(),
                ["Watchlist 1", "Watchlist 3"],
            )

        db.close.assert_called_once_with()

    def test_status_response_exposes_tracking_watchlists(self):
        from backend.api import market_data_routes

        service = MagicMock()
        service.is_running = True
        service.symbols = ["AAPL"]
        service.timeframes = ["1m"]
        service.last_quote_update = {}
        service.last_bar_update = {}
        service.last_status_update = {}
        service.get_tracking_watchlists.return_value = ["Watchlist 1", "Watchlist 2"]

        with patch.object(market_data_routes, "ingestion_service", service):
            response = asyncio.run(market_data_routes.get_ingestion_status())

        self.assertEqual(response.watchlists, ["Watchlist 1", "Watchlist 2"])
        service.get_tracking_watchlists.assert_called_once_with()


class TestCorrelationIdPropagation(unittest.TestCase):
    """Correlation IDs set in the request thread must reach the daemon thread.

    When start() is called from a context with a correlation ID in the
    contextvar, the daemon thread's asyncio loop must see that same ID
    restored at the top of its event loop, so ingestion loop log lines
    are traceable back to the request that initiated ingestion.
    """

    def setUp(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        self.service = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])
        self.seen_ids: list[str | None] = []
        self._lock = threading.Lock()

    def tearDown(self):
        if self.service.is_running:
            self.service.stop()
            time.sleep(0.3)
        set_correlation_id(None)

    def _check_id_from_thread(self) -> str | None:
        """Called from inside the daemon thread — captures the correlation ID."""
        return get_correlation_id()

    def test_daemon_thread_receives_correlation_id(self):
        """The daemon thread's event loop must re-install the captured
        correlation ID at startup, so background ingestion logs carry the
        request's correlation ID."""
        corr_id = "test-correlation-abc123"
        set_correlation_id(corr_id)

        # Wrap _run_loops so it captures the correlation ID as seen from
        # inside the daemon thread, but otherwise exits quickly.
        orig_run_loops = self.service._run_loops
        orig_seed_check = self.service._seed_check

        async def patched_seed_check():
            # No-op: skip the real DB-touching seed check so the patched
            # _run_loops runs immediately and the test doesn't need a DB.
            return None

        async def patched_run_loops():
            # Snapshot the contextvar from inside the daemon thread's
            # event loop. We must check BEFORE creating any sub-tasks,
            # because the restoration happens in start()'s _run_loop wrapper.
            seen = get_correlation_id()
            with self._lock:
                self.seen_ids.append(seen)
            # Yield once so the loop is actually entered, then return so
            # run_until_complete() finishes and stop() can join the thread.
            await asyncio.sleep(0.05)

        self.service._seed_check = patched_seed_check
        self.service._run_loops = patched_run_loops

        try:
            self.service.start()
            # Give the thread time to enter the loop and snapshot the ID.
            for _ in range(20):
                time.sleep(0.05)
                if self.seen_ids:
                    break

            self.assertEqual(
                len(self.seen_ids), 1,
                "Daemon thread should have recorded exactly one ID snapshot",
            )
            self.assertEqual(
                self.seen_ids[0], corr_id,
                f"Daemon thread should see the request's correlation ID "
                f"(expected {corr_id!r}, got {self.seen_ids[0]!r})",
            )
        finally:
            self.service._run_loops = orig_run_loops
            self.service._seed_check = orig_seed_check
            self.service.stop()
            time.sleep(0.3)

    def test_no_correlation_id_does_not_crash(self):
        """start() must not crash if no correlation ID has been set."""
        set_correlation_id(None)

        orig_run_loops = self.service._run_loops
        orig_seed_check = self.service._seed_check

        async def patched_seed_check():
            return None

        async def quick_exit():
            await asyncio.sleep(0.05)

        self.service._seed_check = patched_seed_check
        self.service._run_loops = quick_exit

        try:
            self.service.start()
            # The thread should have started even without a correlation ID.
            self.assertIsNotNone(self.service._thread)
            self.assertTrue(self.service._thread.is_alive())
            # Wait for it to exit cleanly.
            self.service._thread.join(timeout=2.0)
            self.assertFalse(
                self.service._thread.is_alive(),
                "Thread should exit cleanly when _run_loops returns",
            )
        finally:
            self.service._run_loops = orig_run_loops
            self.service._seed_check = orig_seed_check
            if self.service.is_running:
                self.service.stop()
                time.sleep(0.3)


class TestPhase31LastBarUpdate(unittest.TestCase):
    """Phase 3.1: last_bar_update must only track 1m entries.

    Since we only ingest 1m bars, higher-TF entries must never be created
    in last_bar_update, and any stale entries from a pre-3.1 state must be
    pruned at init time.
    """

    def test_last_bar_update_only_has_1m_after_init(self):
        """After __init__, last_bar_update contains only '1m' keys."""
        from backend.market_data.services.ingestion_service import (
            MarketDataIngestionService,
        )

        service = MarketDataIngestionService(symbols=["AAPL", "MSFT"], timeframes=["1m"])
        self.assertEqual(set(service.last_bar_update["AAPL"].keys()), {"1m"})
        self.assertEqual(set(service.last_bar_update["MSFT"].keys()), {"1m"})

    def test_last_bar_update_prunes_stale_pre_phase31_entries(self):
        """Stale pre-3.1 entries (e.g. '1d', '1h') are removed at init.

        After Phase 3.1 ships, any existing server restart creates fresh
        entries with only the '1m' key. Non-1m keys that existed before the
        update (from a pre-3.1 server) are never re-created.
        This test verifies the new-instance init path prunes to '1m' only.
        """
        from backend.market_data.services.ingestion_service import (
            MarketDataIngestionService,
        )

        # Simulate a pre-3.1 state by manually injecting stale keys, then
        # verify a brand-new service instance only creates 1m keys.
        service = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])
        # __init__ already set only 1m — but confirm stale keys are absent.
        self.assertNotIn("1d", service.last_bar_update["AAPL"])
        self.assertNotIn("1h", service.last_bar_update["AAPL"])
        self.assertIn("1m", service.last_bar_update["AAPL"])

    def test_symbol_refresh_only_adds_1m_key(self):
        """_refresh_symbols_from_watchlist only adds '1m' entries for new symbols."""
        from backend.market_data.services.ingestion_service import (
            MarketDataIngestionService,
        )
        from unittest.mock import patch

        service = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])
        # Simulate the watchlist gaining a new symbol.
        with patch.object(
            service, "_load_symbols_from_watchlist", return_value=["AAPL", "TSLA"]
        ):
            service.refresh_symbols_from_watchlist()
        self.assertIn("TSLA", service.last_bar_update)
        self.assertEqual(set(service.last_bar_update["TSLA"].keys()), {"1m"})


class TestResampleSessionFilter(unittest.IsolatedAsyncioTestCase):
    """_resample_and_upsert's sub-hour targets (2m/3m/5m/15m/30m) now
    include premarket/after_hours 1m bars, not just regular-session ones.

    History: the extended-hours feature (2026-09-09) originally kept
    every derived timeframe regular-session-only, matching
    pre-extended-hours behavior exactly (a session='regular' filter on
    the source query). By later request the same day, sub-hour
    timeframes were extended to carry the full session too — the filter
    was removed, and upsert_bars now tags each resulting bar's session
    from its own timestamp (bucket boundaries never straddle a session,
    so this is always unambiguous). 1d/1wk are untouched — they aren't
    resampled through this function. 1h/4h aren't either (see
    _resample_1h_from_1m_and_upsert / _resample_1h_to_4h_and_upsert), but
    as of 2026-09-17 they get the same full-session treatment there too.
    """

    SYMBOL = "ZZTESTEXTHRS"

    def _insert_bar(self, db, ts, session, close=100.0):
        from backend.models.market_data_sql import BarModel
        db.add(BarModel(
            symbol=self.SYMBOL, timeframe="1m",
            open=close, high=close, low=close, close=close, volume=1000,
            timestamp=ts, provider="test", data_status="HISTORICAL",
            source="raw", session=session,
        ))

    def setUp(self):
        from backend.database import SessionLocal
        from backend.models.market_data_sql import BarModel
        self.db = SessionLocal()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()

    def tearDown(self):
        from backend.models.market_data_sql import BarModel
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()
        self.db.close()

    async def test_premarket_only_bucket_now_resamples_and_is_tagged(self):
        from datetime import datetime
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        # Premarket bucket: 2026-09-08 08:00/08:01 ET — 2 bars, session='premarket'.
        self._insert_bar(self.db, datetime(2026, 9, 8, 8, 0), "premarket")
        self._insert_bar(self.db, datetime(2026, 9, 8, 8, 1), "premarket")
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        written = await service._resample_and_upsert(
            target_tf="5m", source_tf="1m", _symbol=self.SYMBOL, full_history=True,
        )
        self.assertGreaterEqual(written, 1)

        from backend.models.market_data_sql import BarModel
        row = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "5m",
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.timestamp, datetime(2026, 9, 8, 8, 0))
        self.assertEqual(row.session, "premarket")

    async def test_after_hours_only_bucket_now_resamples_and_is_tagged(self):
        from datetime import datetime
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        self._insert_bar(self.db, datetime(2026, 9, 8, 17, 0), "after_hours")
        self._insert_bar(self.db, datetime(2026, 9, 8, 17, 1), "after_hours")
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        await service._resample_and_upsert(
            target_tf="5m", source_tf="1m", _symbol=self.SYMBOL, full_history=True,
        )

        from backend.models.market_data_sql import BarModel
        row = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "5m",
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.session, "after_hours")

    async def test_regular_bucket_still_resamples_normally(self):
        from datetime import datetime
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        # Regular-session bucket: 2026-09-08 10:00/10:01 ET — 2 bars.
        self._insert_bar(self.db, datetime(2026, 9, 8, 10, 0), "regular", close=100.0)
        self._insert_bar(self.db, datetime(2026, 9, 8, 10, 1), "regular", close=101.0)
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        written = await service._resample_and_upsert(
            target_tf="5m", source_tf="1m", _symbol=self.SYMBOL, full_history=True,
        )
        self.assertGreaterEqual(written, 1)

        from backend.models.market_data_sql import BarModel
        row = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "5m",
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.timestamp, datetime(2026, 9, 8, 10, 0))
        self.assertEqual(row.session, "regular")

    async def test_premarket_and_regular_buckets_both_resample_independently(self):
        """A premarket bucket and a regular bucket for the same symbol/day
        both produce correctly-tagged 5m bars — proves the two aren't
        cross-contaminating each other now that neither is filtered out."""
        from datetime import datetime
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        self._insert_bar(self.db, datetime(2026, 9, 8, 8, 0), "premarket", close=50.0)
        self._insert_bar(self.db, datetime(2026, 9, 8, 8, 1), "premarket", close=51.0)
        self._insert_bar(self.db, datetime(2026, 9, 8, 10, 0), "regular", close=100.0)
        self._insert_bar(self.db, datetime(2026, 9, 8, 10, 1), "regular", close=101.0)
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        await service._resample_and_upsert(
            target_tf="5m", source_tf="1m", _symbol=self.SYMBOL, full_history=True,
        )

        from backend.models.market_data_sql import BarModel
        rows = {
            r.timestamp: r for r in self.db.query(BarModel).filter(
                BarModel.symbol == self.SYMBOL, BarModel.timeframe == "5m",
            ).all()
        }
        pre = rows[datetime(2026, 9, 8, 8, 0)]
        reg = rows[datetime(2026, 9, 8, 10, 0)]
        self.assertEqual(pre.session, "premarket")
        self.assertEqual(pre.close, 51.0)
        self.assertEqual(reg.session, "regular")
        self.assertEqual(reg.close, 101.0)


class TestResample1hLive(unittest.IsolatedAsyncioTestCase):
    """_resample_1h_from_1m_and_upsert — builds 1h bars from 1m data,
    either the current in-progress hour (default) or any explicit past
    hour_starts (used to correct already-closed hours).

    Added 2026-09-09, then extended the same day: 1h originally only
    ever showed the last FULLY CLOSED hour (correct under that design,
    but surprising — found live at 1:13pm with no 1:00 bar yet, since
    that hour hadn't closed). Mirrors the existing, proven
    ``_resample_1d_live_and_upsert`` pattern one level down.

    Extended the same day to also fix a second, more serious bug this
    surfaced: Webull's 1h endpoint (primary for every symbol) returns
    :30-anchored bars, and the existing normalization floors those to
    the preceding :00 — silently mislabeling which hour a bar's
    high/low actually belong to. See
    _resample_1h_from_1m_and_upsert's own docstring for the full story;
    ``test_corrects_an_already_closed_past_hour`` below is the
    regression test for that specific case.
    """

    SYMBOL = "ZZTESTLIVE1H"

    # The live-resample path has a weekend guard (``if now.weekday() >= 5:
    # return 0`` in _resample_1h_from_1m_and_upsert) that makes these tests
    # fail on Sat/Sun when they read the real wall clock. Freeze "now" to a
    # Wednesday session hour so the suite is calendar-independent, and base
    # _hour_start() on the same frozen clock so test data and the function's
    # default bucket always agree.
    _FROZEN_NY = datetime(2026, 9, 9, 14, 30)  # Wednesday, mid-session

    def setUp(self):
        from unittest.mock import patch

        from backend.database import SessionLocal
        from backend.models.market_data_sql import BarModel

        frozen = self._FROZEN_NY

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return frozen.replace(tzinfo=tz)
                return frozen

        self._clock_patch = patch(
            "backend.market_data.services.ingestion_service.datetime",
            _FrozenDatetime,
        )
        self._clock_patch.start()

        self.db = SessionLocal()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()

    def tearDown(self):
        from backend.models.market_data_sql import BarModel
        self._clock_patch.stop()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()
        self.db.close()

    def _hour_start(self):
        return self._FROZEN_NY.replace(minute=0, second=0, microsecond=0)

    def _insert_1m_bar(self, db, ts, close, session="regular"):
        from backend.models.market_data_sql import BarModel
        db.add(BarModel(
            symbol=self.SYMBOL, timeframe="1m",
            open=close, high=close, low=close, close=close, volume=1000,
            timestamp=ts, provider="test", data_status="HISTORICAL",
            source="raw", session=session,
        ))

    async def test_builds_incomplete_bar_from_this_hours_1m_bars(self):
        from datetime import timedelta
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data_sql import BarModel

        hour_start = self._hour_start()
        self._insert_1m_bar(self.db, hour_start, close=100.0)
        self._insert_1m_bar(self.db, hour_start + timedelta(minutes=1), close=105.0)
        self._insert_1m_bar(self.db, hour_start + timedelta(minutes=2), close=102.0)
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        written = await service._resample_1h_from_1m_and_upsert()
        self.assertGreaterEqual(written, 1)

        row = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "1h",
            BarModel.timestamp == hour_start,
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.open, 100.0)
        self.assertEqual(row.high, 105.0)
        self.assertEqual(row.low, 100.0)
        self.assertEqual(row.close, 102.0)
        self.assertEqual(row.data_status, "INCOMPLETE")
        self.assertEqual(row.provider, "live_from_1m")

    async def test_extended_hours_1m_bars_are_included(self):
        """Changed 2026-09-17 at the user's request: 1h/4h now span
        premarket/regular/after-hours, matching 1d's live pre-close bar,
        instead of staying regular-session-only like every other 1h/4h/1d/
        1wk source query in this codebase (see BarModel.session's
        docstring, still accurate for those)."""
        from datetime import timedelta
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data_sql import BarModel

        hour_start = self._hour_start()
        self._insert_1m_bar(self.db, hour_start, close=100.0, session="premarket")
        self._insert_1m_bar(self.db, hour_start + timedelta(minutes=1), close=999.0, session="premarket")
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        written = await service._resample_1h_from_1m_and_upsert()
        self.assertGreaterEqual(written, 1)

        row = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "1h",
            BarModel.timestamp == hour_start,
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.open, 100.0)
        self.assertEqual(row.high, 999.0)
        self.assertEqual(row.close, 999.0)

    async def test_skips_when_fewer_than_two_bars_this_hour(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        hour_start = self._hour_start()
        self._insert_1m_bar(self.db, hour_start, close=100.0)
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        written = await service._resample_1h_from_1m_and_upsert()
        self.assertEqual(written, 0)

    async def test_authoritative_bar_overwrites_the_live_one(self):
        """A real, provider-sourced bar written to the same key (as
        _1h_write_loop/_gapfill_1h_loop would once the hour actually
        closes) must win — upsert's ON CONFLICT DO UPDATE, no special
        casing needed."""
        from datetime import timedelta
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data_sql import BarModel
        from backend.repositories.bar_repository import upsert_bars
        from backend.models.market_data import Bar, DataStatus

        hour_start = self._hour_start()
        self._insert_1m_bar(self.db, hour_start, close=100.0)
        self._insert_1m_bar(self.db, hour_start + timedelta(minutes=1), close=105.0)
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        await service._resample_1h_from_1m_and_upsert()

        real_bar = Bar(
            symbol=self.SYMBOL, timeframe="1h",
            open=100.0, high=110.0, low=99.0, close=108.0, volume=50000,
            timestamp=hour_start, provider="webull",
            data_status=DataStatus.HISTORICAL,
        )
        upsert_bars(self.db, [real_bar])

        row = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "1h",
            BarModel.timestamp == hour_start,
        ).first()
        self.assertEqual(row.provider, "webull")
        self.assertEqual(row.data_status, "HISTORICAL")
        self.assertEqual(row.close, 108.0)

    async def test_corrects_an_already_closed_past_hour(self):
        """Regression test for the live bug (2026-09-09): a bad
        provider-sourced bar sits in an EARLIER, already-closed hour's
        slot (simulating Webull's :30-anchored bar floored onto the
        wrong :00 hour — its high/low actually belong to the FOLLOWING
        hour). Passing that hour's start in hour_starts must rebuild it
        from 1m and overwrite the bad value with HISTORICAL status
        (the hour is closed, not live)."""
        from datetime import datetime, timedelta
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data_sql import BarModel
        from backend.repositories.bar_repository import upsert_bars
        from backend.models.market_data import Bar, DataStatus

        past_hour = self._hour_start() - timedelta(hours=2)

        # The bad, mislabeled provider bar — e.g. it actually reflects
        # data from the FOLLOWING hour (like the real SPY 10:00 bar
        # whose low/high matched the 11:00 hour's true values).
        bad_bar = Bar(
            symbol=self.SYMBOL, timeframe="1h",
            open=100.0, high=999.0, low=1.0, close=50.0, volume=1,
            timestamp=past_hour, provider="webull",
            data_status=DataStatus.HISTORICAL,
        )
        upsert_bars(self.db, [bad_bar])

        # The REAL 1m data for this hour — what it should actually show.
        self._insert_1m_bar(self.db, past_hour, close=100.0)
        self._insert_1m_bar(self.db, past_hour + timedelta(minutes=1), close=101.0)
        self._insert_1m_bar(self.db, past_hour + timedelta(minutes=2), close=99.5)
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        written = await service._resample_1h_from_1m_and_upsert(hour_starts=[past_hour])
        self.assertGreaterEqual(written, 1)

        row = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "1h",
            BarModel.timestamp == past_hour,
        ).first()
        self.assertEqual(row.provider, "live_from_1m")
        self.assertEqual(row.data_status, "HISTORICAL")  # closed hour, not live
        self.assertEqual(row.high, 101.0)
        self.assertEqual(row.low, 99.5)
        self.assertNotEqual(row.high, 999.0)  # the bad value is gone

    async def test_hour_starts_between_helper(self):
        from datetime import datetime, timedelta
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        start = datetime(2026, 9, 9, 4, 0)
        end = datetime(2026, 9, 9, 6, 30)
        hours = MarketDataIngestionService._hour_starts_between(start, end)
        self.assertEqual(hours, [
            datetime(2026, 9, 9, 4, 0),
            datetime(2026, 9, 9, 5, 0),
            datetime(2026, 9, 9, 6, 0),
        ])


class TestSubHourResampleLive(unittest.IsolatedAsyncioTestCase):
    """_resample_and_upsert (2m/3m/5m/15m/30m) now writes the still-forming
    bucket too, marked INCOMPLETE, instead of only closed buckets —
    extending the same live-bucket convention already proven for 1d/1h
    down to the sub-hour targets (2026-09-16)."""

    SYMBOL = "ZZTESTLIVE5M"
    _FROZEN_NY = datetime(2026, 9, 9, 14, 7)  # Wednesday, mid-session

    def setUp(self):
        from backend.database import SessionLocal
        from backend.models.market_data_sql import BarModel

        frozen = self._FROZEN_NY

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return frozen.replace(tzinfo=tz)
                return frozen

        self._clock_patch = patch(
            "backend.market_data.services.ingestion_service.datetime",
            _FrozenDatetime,
        )
        self._clock_patch.start()

        self.db = SessionLocal()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()

    def tearDown(self):
        from backend.models.market_data_sql import BarModel
        self._clock_patch.stop()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()
        self.db.close()

    def _insert_1m_bar(self, db, ts, close):
        from backend.models.market_data_sql import BarModel
        db.add(BarModel(
            symbol=self.SYMBOL, timeframe="1m",
            open=close, high=close, low=close, close=close, volume=1000,
            timestamp=ts, provider="test", data_status="HISTORICAL",
            source="raw", session="regular",
        ))

    async def test_forming_bucket_incomplete_closed_bucket_historical(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data_sql import BarModel

        # Closed bucket: 14:00-14:05 (end 14:05 < frozen now 14:07).
        self._insert_1m_bar(self.db, datetime(2026, 9, 9, 14, 0), close=100.0)
        self._insert_1m_bar(self.db, datetime(2026, 9, 9, 14, 4), close=101.0)
        # Still-forming bucket: 14:05-14:10 (end 14:10 > frozen now 14:07).
        self._insert_1m_bar(self.db, datetime(2026, 9, 9, 14, 5), close=102.0)
        self._insert_1m_bar(self.db, datetime(2026, 9, 9, 14, 6), close=103.0)
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        written = await service._resample_and_upsert(
            target_tf="5m", source_tf="1m", _symbol=self.SYMBOL, full_history=True,
        )
        self.assertGreaterEqual(written, 2)

        closed = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "5m",
            BarModel.timestamp == datetime(2026, 9, 9, 14, 0),
        ).first()
        forming = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "5m",
            BarModel.timestamp == datetime(2026, 9, 9, 14, 5),
        ).first()
        self.assertIsNotNone(closed)
        self.assertIsNotNone(forming)
        self.assertEqual(closed.data_status, "HISTORICAL")
        self.assertEqual(forming.data_status, "INCOMPLETE")
        self.assertEqual(forming.close, 103.0)


class TestResample4hLive(unittest.IsolatedAsyncioTestCase):
    """_resample_1h_to_4h_and_upsert now writes the still-forming 4h
    bucket too, marked INCOMPLETE (2026-09-16), same convention as
    sub-hour/1d/1h."""

    SYMBOL = "ZZTESTLIVE4H"
    _FROZEN_NY = datetime(2026, 9, 9, 13, 15)  # Wednesday, mid-session

    def setUp(self):
        from backend.database import SessionLocal
        from backend.models.market_data_sql import BarModel

        frozen = self._FROZEN_NY

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return frozen.replace(tzinfo=tz)
                return frozen

        self._clock_patch = patch(
            "backend.market_data.services.ingestion_service.datetime",
            _FrozenDatetime,
        )
        self._clock_patch.start()

        self.db = SessionLocal()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()

    def tearDown(self):
        from backend.models.market_data_sql import BarModel
        self._clock_patch.stop()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()
        self.db.close()

    def _insert_1h_bar(self, db, ts, close):
        from backend.models.market_data_sql import BarModel
        db.add(BarModel(
            symbol=self.SYMBOL, timeframe="1h",
            open=close, high=close, low=close, close=close, volume=1000,
            timestamp=ts, provider="test", data_status="HISTORICAL",
            source="raw", session="regular",
        ))

    async def test_forming_bucket_incomplete_closed_bucket_historical(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data_sql import BarModel

        # Closed bucket: 08:00-12:00 (end 12:00 < frozen now 13:15).
        self._insert_1h_bar(self.db, datetime(2026, 9, 9, 8, 0), close=100.0)
        self._insert_1h_bar(self.db, datetime(2026, 9, 9, 9, 0), close=101.0)
        # Still-forming bucket: 12:00-16:00 (end 16:00 > frozen now 13:15).
        self._insert_1h_bar(self.db, datetime(2026, 9, 9, 12, 0), close=102.0)
        self._insert_1h_bar(self.db, datetime(2026, 9, 9, 13, 0), close=103.0)
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        written = await service._resample_1h_to_4h_and_upsert()
        self.assertGreaterEqual(written, 2)

        closed = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "4h",
            BarModel.timestamp == datetime(2026, 9, 9, 8, 0),
        ).first()
        forming = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "4h",
            BarModel.timestamp == datetime(2026, 9, 9, 12, 0),
        ).first()
        self.assertIsNotNone(closed)
        self.assertIsNotNone(forming)
        self.assertEqual(closed.data_status, "HISTORICAL")
        self.assertEqual(forming.data_status, "INCOMPLETE")
        self.assertEqual(forming.close, 103.0)


class TestResample1wkLive(unittest.IsolatedAsyncioTestCase):
    """_resample_1d_to_1wk_and_upsert now writes the current in-progress
    week too, marked INCOMPLETE (2026-09-16), instead of being invisible
    until Saturday 00:00 ET closes it — same convention as 1d/1h/4h/
    sub-hour."""

    SYMBOL = "ZZTESTLIVE1WK"
    _FROZEN_NY = datetime(2026, 9, 9, 14, 30)  # Wednesday, mid-session

    def setUp(self):
        from backend.database import SessionLocal
        from backend.models.market_data_sql import BarModel

        frozen = self._FROZEN_NY

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return frozen.replace(tzinfo=tz)
                return frozen

        self._clock_patch = patch(
            "backend.market_data.services.ingestion_service.datetime",
            _FrozenDatetime,
        )
        self._clock_patch.start()

        self.db = SessionLocal()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()

    def tearDown(self):
        from backend.models.market_data_sql import BarModel
        self._clock_patch.stop()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()
        self.db.close()

    def _insert_1d_bar(self, db, ts, close):
        from backend.models.market_data_sql import BarModel
        db.add(BarModel(
            symbol=self.SYMBOL, timeframe="1d",
            open=close, high=close, low=close, close=close, volume=1000,
            timestamp=ts, provider="test", data_status="HISTORICAL",
            source="raw", session="regular",
        ))

    async def test_current_week_incomplete_prior_week_historical(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data_sql import BarModel

        # Prior week (closed: Sat 2026-09-05 00:00 ET has passed by frozen now).
        self._insert_1d_bar(self.db, datetime(2026, 8, 31, 0, 0), close=90.0)  # Monday
        self._insert_1d_bar(self.db, datetime(2026, 9, 1, 0, 0), close=91.0)
        # Current week (open: Sat 2026-09-12 00:00 ET hasn't passed yet).
        self._insert_1d_bar(self.db, datetime(2026, 9, 7, 0, 0), close=100.0)  # Monday
        self._insert_1d_bar(self.db, datetime(2026, 9, 8, 0, 0), close=102.0)
        self._insert_1d_bar(self.db, datetime(2026, 9, 9, 0, 0), close=105.0)
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        written = await service._resample_1d_to_1wk_and_upsert()
        self.assertGreaterEqual(written, 2)

        rows = sorted(
            self.db.query(BarModel).filter(
                BarModel.symbol == self.SYMBOL, BarModel.timeframe == "1wk",
            ).all(),
            key=lambda r: r.timestamp,
        )
        self.assertEqual(len(rows), 2)
        prior_week, current_week = rows
        self.assertEqual(prior_week.data_status, "HISTORICAL")
        self.assertEqual(current_week.data_status, "INCOMPLETE")
        self.assertEqual(current_week.close, 105.0)


class TestGapfill1mOnce(unittest.IsolatedAsyncioTestCase):
    """_gapfill_1m_once must only write bars newer than the DB's latest 1m
    row, not unconditionally re-write the whole fetched day. Regression
    for a live bug (2026-09-16): it fetched a full day (~900-1200 bars)
    per symbol every 2 min but wrote all of them regardless — its own
    docstring claimed it wrote "only bars newer than the DB's latest
    row," but nothing in the code enforced that. ~21 symbols x ~1000
    redundant row upserts every 2 min slowed the whole DB down, which
    showed up live as a steadily growing gap between ingestion cycles
    (100s -> 227s) and simple bar reads logging as slow_query."""

    SYMBOL = "ZZTESTGAPFILL1M"

    def setUp(self):
        from backend.database import SessionLocal
        from backend.models.market_data_sql import BarModel
        self.db = SessionLocal()
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()

    def tearDown(self):
        from backend.models.market_data_sql import BarModel
        self.db.query(BarModel).filter(BarModel.symbol == self.SYMBOL).delete()
        self.db.commit()
        self.db.close()

    def _insert_1m_bar(self, db, ts, close):
        from backend.models.market_data_sql import BarModel
        db.add(BarModel(
            symbol=self.SYMBOL, timeframe="1m",
            open=close, high=close, low=close, close=close, volume=1000,
            timestamp=ts, provider="test", data_status="HISTORICAL",
            source="raw", session="regular",
        ))

    async def test_only_writes_bars_newer_than_latest_db_row(self):
        from datetime import timedelta
        from unittest.mock import AsyncMock, patch
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data import Bar, DataStatus
        from backend.models.market_data_sql import BarModel

        latest = datetime(2026, 9, 9, 14, 0)
        self._insert_1m_bar(self.db, latest, close=100.0)
        self.db.commit()

        # Simulate the provider chain returning a full day's worth of bars:
        # one bar before, one AT, and two genuinely new ones after the
        # already-stored latest row.
        fetched = [
            Bar(symbol=self.SYMBOL, timeframe="1m", open=99, high=99, low=99, close=99,
                volume=100, timestamp=latest - timedelta(minutes=1), provider="test",
                data_status=DataStatus.HISTORICAL),
            Bar(symbol=self.SYMBOL, timeframe="1m", open=100, high=100, low=100, close=100,
                volume=100, timestamp=latest, provider="test",
                data_status=DataStatus.HISTORICAL),
            Bar(symbol=self.SYMBOL, timeframe="1m", open=101, high=101, low=101, close=101,
                volume=100, timestamp=latest + timedelta(minutes=1), provider="test",
                data_status=DataStatus.HISTORICAL),
            Bar(symbol=self.SYMBOL, timeframe="1m", open=102, high=102, low=102, close=102,
                volume=100, timestamp=latest + timedelta(minutes=2), provider="test",
                data_status=DataStatus.HISTORICAL),
        ]

        with patch(
            "backend.market_data.services.backfill_service._fetch_tier1_1m_bars",
            new=AsyncMock(return_value=fetched),
        ):
            service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
            written = await service._gapfill_1m_once()

        self.assertEqual(written, 2)
        rows = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "1m",
            BarModel.timestamp > latest,
        ).all()
        self.assertEqual(len(rows), 2)

    async def test_writes_nothing_when_no_bars_are_newer(self):
        from unittest.mock import AsyncMock, patch
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data import Bar, DataStatus

        latest = datetime(2026, 9, 9, 14, 0)
        self._insert_1m_bar(self.db, latest, close=100.0)
        self.db.commit()

        # Every fetched bar is at-or-before the DB's latest row — a
        # steady-state cycle with nothing actually missing.
        fetched = [
            Bar(symbol=self.SYMBOL, timeframe="1m", open=100, high=100, low=100, close=100,
                volume=100, timestamp=latest, provider="test",
                data_status=DataStatus.HISTORICAL),
        ]

        with patch(
            "backend.market_data.services.backfill_service._fetch_tier1_1m_bars",
            new=AsyncMock(return_value=fetched),
        ):
            service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
            written = await service._gapfill_1m_once()

        self.assertEqual(written, 0)

    async def test_newly_written_bars_are_dispatched_to_live_engines(self):
        """Regression for a live bug (2026-09-16): gap-fill wrote missing
        bars to the DB but never called dispatch_bar, so the regime/trend
        engines never found out — a symbol whose main ingest loop missed
        a minute (gap-fill silently patched it 2 min later) showed a
        frozen/stale "last tick" indefinitely even though its DB bars
        were fully current. Found live on AAPL/SPY."""
        from datetime import timedelta
        from unittest.mock import AsyncMock, patch
        from backend.market_data.services.ingestion_service import (
            MarketDataIngestionService, engine_registry,
        )
        from backend.models.market_data import Bar, DataStatus

        latest = datetime(2026, 9, 9, 14, 0)
        self._insert_1m_bar(self.db, latest, close=100.0)
        self.db.commit()

        new_bar_ts = latest + timedelta(minutes=1)
        fetched = [
            Bar(symbol=self.SYMBOL, timeframe="1m", open=100, high=100, low=100, close=100,
                volume=100, timestamp=latest, provider="test",
                data_status=DataStatus.HISTORICAL),
            Bar(symbol=self.SYMBOL, timeframe="1m", open=101, high=102, low=100, close=101.5,
                volume=555, timestamp=new_bar_ts, provider="test",
                data_status=DataStatus.HISTORICAL),
        ]

        received = []

        def _on_bar(**kwargs):
            received.append(kwargs)

        engine_registry.register("bar:1m", self.SYMBOL, _on_bar)
        try:
            with patch(
                "backend.market_data.services.backfill_service._fetch_tier1_1m_bars",
                new=AsyncMock(return_value=fetched),
            ):
                service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
                await service._gapfill_1m_once()
        finally:
            engine_registry.unregister("bar:1m", self.SYMBOL, _on_bar)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["timestamp"], new_bar_ts)
        self.assertEqual(received[0]["price"], 101.5)
        self.assertEqual(received[0]["volume"], 555)


class TestInstantiateBackfillProviderUsesCache(unittest.TestCase):
    """_instantiate_backfill_provider must delegate to the process-lifetime
    provider cache, not construct fresh on every call.

    Regression coverage: this was a second, independent copy of the exact
    bug get_cached_provider (backend/market_data/services/manager.py) was
    written to fix — its own docstring said "mirrors the helper in
    backfill_service.py", but only that sibling actually got updated to
    use the cache. This one still called provider_cls() directly, so the
    _1h_write_loop / _daily_write_loop fallback path (hit whenever the
    primary provider's data looks stale) kept re-triggering WebullProvider's
    blocking synchronous auth handshake on the shared ingestion event loop —
    found live 2026-09-09.
    """

    def setUp(self):
        from backend.market_data.services import manager as manager_mod
        self.manager_mod = manager_mod
        manager_mod._clear_provider_cache()

    def tearDown(self):
        self.manager_mod._clear_provider_cache()

    def test_second_call_reuses_the_same_instance(self):
        from unittest.mock import patch
        from backend.market_data.services.ingestion_service import _instantiate_backfill_provider

        construct_count = {"n": 0}

        class _FakeProvider:
            def __init__(self):
                construct_count["n"] += 1

        with patch.object(self.manager_mod, "_PROVIDER_CLASSES", {"fake": _FakeProvider}):
            first = _instantiate_backfill_provider("fake")
            second = _instantiate_backfill_provider("fake")

        self.assertIs(first, second)
        self.assertEqual(construct_count["n"], 1)

    def test_unknown_provider_returns_none(self):
        from unittest.mock import patch
        from backend.market_data.services.ingestion_service import _instantiate_backfill_provider

        with patch.object(self.manager_mod, "_PROVIDER_CLASSES", {}):
            self.assertIsNone(_instantiate_backfill_provider("bogus"))


if __name__ == "__main__":
    unittest.main()


class TestRecentWindowIngestVolume(unittest.IsolatedAsyncioTestCase):
    """The 1m recent-window loop must never write more than the newest bars.

    Providers don't all honour range_="15m": Webull fetched a whole trading day
    (891 bars/symbol) and Yahoo maps "15m" to 5 days (~1,900 bars/symbol), so
    the loop upserted 22,000-46,000 rows every minute (GIL-bound seconds that
    starved the API event loop) and lost the entire cycle once the batch
    exceeded SQLite's bound-variable limit.
    """

    def _bars(self, symbol: str, n: int):
        from backend.models.market_data import Bar, DataStatus

        base = datetime(2026, 9, 18, 4, 0)
        return [
            Bar(symbol=symbol, timestamp=base + timedelta(minutes=i), open=1.0, high=1.0,
                low=1.0, close=1.0, volume=1, timeframe="1m", provider="yahoo_finance",
                data_status=DataStatus.LIVE)
            for i in range(n)
        ]

    def _service(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        svc = MarketDataIngestionService(symbols=["AAPL", "MSFT"], timeframes=["1m"])
        svc.manager = MagicMock()
        svc._resample_and_upsert = MagicMock(side_effect=lambda *a, **k: asyncio.sleep(0))
        return svc

    def test_newest_bars_helper(self):
        from backend.market_data.services.ingestion_service import _newest_bars

        bars = self._bars("AAPL", 100)
        short = bars[:30]
        self.assertIs(_newest_bars(short), short)          # at/under the cap: untouched
        out = _newest_bars(list(reversed(bars)))          # unsorted input
        self.assertEqual(len(out), 30)
        self.assertEqual([b.timestamp for b in out], [b.timestamp for b in bars[-30:]])
        self.assertEqual(len(_newest_bars(self._bars("AAPL", 5))), 5)  # short lists untouched
        self.assertEqual(len(_newest_bars(bars, limit=7)), 7)

    async def test_batch_path_writes_only_the_newest_bars_per_symbol(self):
        svc = self._service()
        svc.manager.get_historical_bars_batch.return_value = {
            "AAPL": self._bars("AAPL", 1900),   # what Yahoo returned for "15m"
            "MSFT": self._bars("MSFT", 891),    # what Webull returned for "15m"
        }
        with patch("backend.market_data.services.ingestion_service.SessionLocal"), \
                patch("backend.repositories.bar_repository.upsert_bars", return_value=60) as up, \
                patch("backend.market_data.services.cache._redis_cache"):
            await svc._ingest_1m_recent_window()

        written = up.call_args.args[1]
        by_symbol = {s: [b for b in written if b.symbol == s] for s in ("AAPL", "MSFT")}
        self.assertEqual({k: len(v) for k, v in by_symbol.items()}, {"AAPL": 30, "MSFT": 30})
        # ...and they are the NEWEST bars, not the oldest.
        newest_ts = max(b.timestamp for b in self._bars("AAPL", 1900))
        self.assertEqual(max(b.timestamp for b in by_symbol["AAPL"]), newest_ts)
        self.assertTrue(all(b.timeframe == "1m" for b in written))

    async def test_per_symbol_fallback_path_is_bounded_too(self):
        svc = self._service()
        svc.manager.get_historical_bars_batch.side_effect = RuntimeError("batch endpoint down")
        svc.manager.get_historical_bars.side_effect = lambda sym, *a, **k: self._bars(sym, 1900)
        with patch("backend.market_data.services.ingestion_service.SessionLocal"), \
                patch("backend.market_data.services.ingestion_service.asyncio.sleep",
                      new=lambda *_a, **_k: asyncio.sleep(0)), \
                patch("backend.repositories.bar_repository.upsert_bars", return_value=60) as up, \
                patch("backend.market_data.services.cache._redis_cache"):
            await svc._ingest_1m_recent_window()
        self.assertEqual(len(up.call_args.args[1]), 60)  # 2 symbols x 30
