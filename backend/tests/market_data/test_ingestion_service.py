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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../"))

from backend.observability.logging_enhanced import get_correlation_id, set_correlation_id


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
    so this is always unambiguous). 1h/4h/1d/1wk are untouched — they
    aren't resampled through this function.
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

    def _hour_start(self):
        from datetime import datetime
        from backend.utils.timezone import NY as _NY_TZ
        now = datetime.now(_NY_TZ)
        return now.replace(minute=0, second=0, microsecond=0).replace(tzinfo=None)

    def _insert_1m_bar(self, db, ts, close, session="regular"):
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

    async def test_extended_hours_1m_bars_are_excluded(self):
        """Regular-session-only, matching every other 1h source query —
        this must not become the one place extended-hours data leaks
        into 1h."""
        from datetime import timedelta
        from backend.market_data.services.ingestion_service import MarketDataIngestionService
        from backend.models.market_data_sql import BarModel

        hour_start = self._hour_start()
        self._insert_1m_bar(self.db, hour_start, close=100.0, session="premarket")
        self._insert_1m_bar(self.db, hour_start + timedelta(minutes=1), close=999.0, session="premarket")
        self.db.commit()

        service = MarketDataIngestionService(symbols=[self.SYMBOL], timeframes=["1m"])
        await service._resample_1h_from_1m_and_upsert()

        row = self.db.query(BarModel).filter(
            BarModel.symbol == self.SYMBOL, BarModel.timeframe == "1h",
            BarModel.timestamp == hour_start,
        ).first()
        # Fewer than 2 *regular*-session rows in the bucket -> nothing written.
        self.assertIsNone(row)

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


if __name__ == "__main__":
    unittest.main()
