"""
Scheduling / startup behaviour of ``MarketDataIngestionService`` found in the
end-to-end review:

* The 1h / 4h / daily-close write loops woke every ~300 s and acted only when
  ``minute == 2 and second < 10`` -- a 10 s window sampled every ~300 s -- so a scheduled
  write ran on ~3% of its slots (the 16:02 authoritative daily write on ~1 day in 30).
* ``_seed_check`` had its comparison inverted: it queued a full backfill for every symbol
  whose history was ALREADY deep (24 jobs at every process start, 6,766 in ten days).
* A failed watchlist read looked like "the watchlist is empty", so ``refresh`` dropped every
  symbol and unsubscribed the stream.
* The 1m recent-window provider fetch was a synchronous call on the ingestion event loop.
"""
import asyncio
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.market_data.services import ingestion_service as ing
from backend.market_data.services.ingestion_service import MarketDataIngestionService

_MOD = "backend.market_data.services.ingestion_service"


def _service(symbols=("AAPL", "MSFT")) -> MarketDataIngestionService:
    svc = MarketDataIngestionService(symbols=list(symbols), timeframes=["1m"])
    svc.manager = MagicMock()
    return svc


class TestSlotFunctions(unittest.TestCase):
    def test_hourly_slot_opens_at_minute_two(self):
        self.assertIsNone(ing.MarketDataIngestionService._hourly_slot(datetime(2026, 9, 18, 10, 1, 59)))
        self.assertEqual(ing.MarketDataIngestionService._hourly_slot(datetime(2026, 9, 18, 10, 2)),
                         (datetime(2026, 9, 18).date(), 10))
        self.assertEqual(ing.MarketDataIngestionService._hourly_slot(datetime(2026, 9, 18, 10, 59)),
                         (datetime(2026, 9, 18).date(), 10))

    def test_four_hourly_slot_only_on_four_hour_boundaries(self):
        f = ing.MarketDataIngestionService._four_hourly_slot
        for hour in (0, 4, 8, 12, 16, 20):
            self.assertIsNotNone(f(datetime(2026, 9, 18, hour, 5)), hour)
            self.assertIsNone(f(datetime(2026, 9, 18, hour, 1)), hour)
        for hour in (1, 3, 9, 15, 23):
            self.assertIsNone(f(datetime(2026, 9, 18, hour, 30)), hour)

    def test_daily_close_slot_is_weekdays_from_1602(self):
        f = ing.MarketDataIngestionService._daily_close_slot
        self.assertIsNone(f(datetime(2026, 9, 18, 16, 1)))                   # Friday, too early
        self.assertEqual(f(datetime(2026, 9, 18, 16, 2)), datetime(2026, 9, 18).date())
        self.assertEqual(f(datetime(2026, 9, 18, 23, 30)), datetime(2026, 9, 18).date())
        self.assertIsNone(f(datetime(2026, 9, 19, 17, 0)))                   # Saturday
        self.assertIsNone(f(datetime(2026, 9, 20, 17, 0)))                   # Sunday


class TestSlotLoop(unittest.IsolatedAsyncioTestCase):
    async def _drive(self, slots, run, catch_up=None):
        """Poll ``_slot_loop`` once per entry of ``slots`` (the first entry is what it sees at start)."""
        svc = _service()
        it = iter(slots)
        current = {"v": next(it)}

        async def fake_sleep(*_a, **_k):
            try:
                current["v"] = next(it)
            except StopIteration:
                svc.is_running = False

        svc._jittered_sleep = fake_sleep
        svc.is_running = True
        await svc._slot_loop("test", lambda _now: current["v"], run, catch_up=catch_up)

    async def test_runs_once_per_slot(self):
        ran = []

        async def run():
            ran.append(1)

        await self._drive([None, "a", "a", "a", "b", "b", None, "c"], run)
        self.assertEqual(len(ran), 3, "one run for each of a, b, c")

    async def test_a_slot_already_under_way_at_start_is_not_rerun(self):
        ran = []

        async def run():
            ran.append(1)

        await self._drive(["a", "a", "a", "b"], run)
        self.assertEqual(len(ran), 1, "only the NEW slot b runs; a reload must not refetch slot a")

    async def test_catch_up_true_runs_the_current_slot_once(self):
        ran = []

        async def run():
            ran.append(1)

        async def missing():
            return True

        await self._drive(["a", "a", "a"], run, catch_up=missing)
        self.assertEqual(len(ran), 1)

    async def test_catch_up_false_does_not_run(self):
        ran = []

        async def run():
            ran.append(1)

        async def present():
            return False

        await self._drive(["a", "a"], run, catch_up=present)
        self.assertEqual(ran, [])

    async def test_a_failing_catch_up_check_is_logged_not_fatal(self):
        ran = []

        async def run():
            ran.append(1)

        async def broken():
            raise RuntimeError("db locked")

        with self.assertLogs(_MOD, "WARNING"):
            await self._drive(["a", "a", "b"], run, catch_up=broken)
        self.assertEqual(len(ran), 1, "the loop survives and still runs the next slot")

    async def test_a_failing_run_does_not_kill_the_loop_or_retry_the_slot(self):
        calls = []

        async def run():
            calls.append(1)
            raise RuntimeError("provider down")

        with self.assertLogs(_MOD, "ERROR"):
            await self._drive([None, "a", "a", "a", "b"], run)
        self.assertEqual(len(calls), 2, "a once, b once; a failed slot is not hammered every poll")

    async def test_real_slots_fire_every_hour_and_every_weekday_close(self):
        """The regression itself: polling every 30 s across a week must hit EVERY slot."""
        svc = _service()
        clock = {"now": datetime(2026, 9, 14, 0, 0, 10)}        # a Monday
        end = datetime(2026, 9, 21, 0, 0, 0)
        hourly, four_hourly, daily = [], [], []

        async def fake_sleep(*_a, **_k):
            clock["now"] += timedelta(seconds=30)
            if clock["now"] >= end:
                svc.is_running = False

        svc._jittered_sleep = fake_sleep
        svc.is_running = True

        def runner(bucket):
            async def run():
                bucket.append(clock["now"])
            return run

        # Three independent loops over the same simulated clock.
        for name, slot_of, bucket in (
            ("h", svc._hourly_slot, hourly),
            ("4h", svc._four_hourly_slot, four_hourly),
            ("d", svc._daily_close_slot, daily),
        ):
            clock["now"] = datetime(2026, 9, 14, 0, 0, 10)
            svc.is_running = True
            await svc._slot_loop(name, lambda _n, f=slot_of: f(clock["now"]), runner(bucket))

        self.assertEqual(len(hourly), 7 * 24, "24 hourly writes a day")
        self.assertEqual(len(four_hourly), 7 * 6, "6 four-hour writes a day")
        self.assertEqual(len(daily), 5, "one close write per weekday, none on the weekend")
        self.assertTrue(all((t.hour, t.minute) == (16, 2) for t in daily),
                        f"daily write at 16:02 sharp: {[t.time() for t in daily]}")


class _DbCase(unittest.TestCase):
    """Real repository/model code against a throwaway SQLite database."""

    def setUp(self):
        from backend.models import BackfillJob
        from backend.models.market_data_sql import BarModel

        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        BarModel.__table__.create(self.engine)
        BackfillJob.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        p = patch(f"{_MOD}.SessionLocal", self.Session)
        p.start()
        self.addCleanup(p.stop)

    def _bar(self, symbol, timeframe, ts, provider="webull"):
        from backend.models.market_data_sql import BarModel

        with self.Session() as db:
            db.add(BarModel(symbol=symbol, timeframe=timeframe, open=1.0, high=1.0, low=1.0,
                            close=1.0, volume=1, timestamp=ts, provider=provider,
                            data_status="historical"))
            db.commit()

    def _job(self, symbol, created_at):
        from backend.models import BackfillJob

        with self.Session() as db:
            db.add(BackfillJob(job_id=f"{symbol}-{created_at.isoformat()}", symbol=symbol,
                               status="partial", created_at=created_at))
            db.commit()


class TestDailyCloseBarsMissing(_DbCase):
    def _today(self):
        return datetime.now(ing._NY_TZ).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    def test_missing_when_nothing_is_stored(self):
        self.assertTrue(_service()._daily_close_bars_missing())

    def test_our_own_1m_aggregate_does_not_count_as_the_close_write(self):
        for sym in ("AAPL", "MSFT"):
            self._bar(sym, "1d", self._today(), provider="live_from_1m")
        self.assertTrue(_service()._daily_close_bars_missing())

    def test_missing_if_only_some_symbols_have_the_provider_bar(self):
        self._bar("AAPL", "1d", self._today())
        self.assertTrue(_service()._daily_close_bars_missing())

    def test_not_missing_once_every_symbol_has_it(self):
        for sym in ("AAPL", "MSFT"):
            self._bar(sym, "1d", self._today())
        self.assertFalse(_service()._daily_close_bars_missing())

    def test_yesterdays_bar_does_not_count(self):
        for sym in ("AAPL", "MSFT"):
            self._bar(sym, "1d", self._today() - timedelta(days=1))
        self.assertTrue(_service()._daily_close_bars_missing())

    def test_no_symbols_means_nothing_to_write(self):
        self.assertFalse(_service(symbols=()) ._daily_close_bars_missing())


class TestSeedCheck(_DbCase, unittest.IsolatedAsyncioTestCase):
    """History depth decides whether a symbol is queued for backfill at startup."""

    async def _enqueued(self, symbols=("AAPL",)):
        svc = _service(symbols)
        with patch("backend.market_data.services.backfill_queue.enqueue_backfill") as enqueue:
            await svc._seed_check()
        return [c.args[0] for c in enqueue.call_args_list]

    async def test_deep_history_is_not_requeued_at_every_start(self):
        # The live database at review time: every symbol's oldest bar was 3 years old.
        self._bar("AAPL", "1d", datetime.now() - timedelta(days=1095))
        self.assertEqual(await self._enqueued(), [])

    async def test_no_data_at_all_is_queued(self):
        self.assertEqual(await self._enqueued(), ["AAPL"])

    async def test_shallow_history_is_queued(self):
        self._bar("AAPL", "1d", datetime.now() - timedelta(days=30))
        self.assertEqual(await self._enqueued(), ["AAPL"])

    async def test_shallow_history_is_not_requeued_within_a_day_of_a_job(self):
        self._bar("AAPL", "1d", datetime.now() - timedelta(days=30))
        self._job("AAPL", datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
        self.assertEqual(await self._enqueued(), [])

    async def test_shallow_history_is_retried_after_the_retry_window(self):
        self._bar("AAPL", "1d", datetime.now() - timedelta(days=30))
        self._job("AAPL", datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3))
        self.assertEqual(await self._enqueued(), ["AAPL"])

    async def test_only_the_shallow_symbol_of_two_is_queued(self):
        self._bar("AAPL", "1d", datetime.now() - timedelta(days=1095))
        self._bar("MSFT", "1d", datetime.now() - timedelta(days=10))
        self.assertEqual(await self._enqueued(("AAPL", "MSFT")), ["MSFT"])


class TestRefreshSymbols(unittest.TestCase):
    def _refresh(self, svc, query):
        stream = MagicMock()
        with patch.object(svc, "_query_active_watchlists", **query), \
             patch("backend.market_data.streaming.webull_stream.get_webull_stream_client",
                   return_value=stream):
            result = svc.refresh_symbols_from_watchlist()
        return result, stream

    def test_a_failed_read_keeps_the_current_symbols(self):
        svc = _service(("AAPL", "MSFT"))
        with self.assertLogs(_MOD, "WARNING"):
            result, stream = self._refresh(svc, {"side_effect": RuntimeError("database is locked")})
        self.assertEqual(result, ["AAPL", "MSFT"])
        self.assertEqual(svc.symbols, ["AAPL", "MSFT"])
        stream.unsubscribe.assert_not_called()

    def test_a_genuinely_empty_watchlist_still_removes_symbols(self):
        svc = _service(("AAPL", "MSFT"))
        result, stream = self._refresh(svc, {"return_value": ([], [])})
        self.assertEqual(result, [])
        stream.unsubscribe.assert_called_once_with({"AAPL", "MSFT"})

    def test_a_new_symbol_is_added_and_subscribed(self):
        svc = _service(("AAPL",))
        result, stream = self._refresh(svc, {"return_value": (["AAPL", "TSLA"], ["wl"])})
        self.assertEqual(result, ["AAPL", "TSLA"])
        self.assertIn("TSLA", svc.last_bar_update)

    def test_the_swallowing_loader_still_returns_empty_lists_for_start(self):
        """start() / get_tracking_watchlists() keep the old contract."""
        svc = _service(())
        with patch.object(svc, "_query_active_watchlists", side_effect=RuntimeError("boom")), \
             self.assertLogs(_MOD, "WARNING"):
            self.assertEqual(svc._load_symbols_and_watchlists_from_all_active_watchlists(), ([], []))


class TestProviderFetchIsOffTheIngestionLoop(unittest.IsolatedAsyncioTestCase):
    def _bars(self, symbol):
        from backend.models.market_data import Bar, DataStatus

        return [Bar(symbol=symbol, timestamp=datetime(2026, 9, 18, 10, 0), open=1.0, high=1.0,
                    low=1.0, close=1.0, volume=1, timeframe="1m", provider="webull",
                    data_status=DataStatus.LIVE)]

    async def _cycle(self, svc):
        real_sleep = asyncio.sleep
        svc._resample_and_upsert = MagicMock(side_effect=lambda *a, **k: real_sleep(0))
        with patch(f"{_MOD}.SessionLocal"), \
             patch(f"{_MOD}.engine_registry", MagicMock()), \
             patch(f"{_MOD}.asyncio.sleep", new=lambda *_a, **_k: real_sleep(0)), \
             patch("backend.repositories.bar_repository.upsert_bars", return_value=0), \
             patch("backend.market_data.services.cache._redis_cache"):
            await svc._ingest_1m_recent_window()

    async def test_batch_fetch_runs_in_a_worker_thread(self):
        svc = _service()
        seen = []

        def fetch(*_a, **_k):
            seen.append(threading.get_ident())
            return {"AAPL": self._bars("AAPL")}

        svc.manager.get_historical_bars_batch.side_effect = fetch
        await self._cycle(svc)
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], threading.get_ident(), "provider call ran ON the event loop")

    async def test_per_symbol_fallback_runs_in_a_worker_thread(self):
        svc = _service()
        seen = []
        svc.manager.get_historical_bars_batch.side_effect = RuntimeError("batch down")

        def fetch(symbol, *_a, **_k):
            seen.append(threading.get_ident())
            return self._bars(symbol)

        svc.manager.get_historical_bars.side_effect = fetch
        await self._cycle(svc)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(t != threading.get_ident() for t in seen))


if __name__ == "__main__":
    unittest.main()
