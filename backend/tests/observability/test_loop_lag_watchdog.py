"""
Tests for the opt-in event-loop stall watchdog.

It exists to name the code running while the API loop stalls (py-spy isn't
available, and a sleep-loop heartbeat under-reports GIL convoys 10x), so the
tests check the property that matters: a stall produces a report naming the
blocking function, on the right thread, and healthy loops produce nothing.
"""
import asyncio
import threading
import time
import unittest

from fastapi.testclient import TestClient

from backend.observability import loop_lag_watchdog as wd_mod
from backend.observability.loop_lag_watchdog import LoopLagWatchdog


def _block_the_loop(seconds: float) -> None:
    time.sleep(seconds)  # deliberately synchronous, on the event-loop thread


def _other_thread_work(stop: threading.Event) -> None:
    while not stop.is_set():
        time.sleep(0.002)


class TestWatchdogDetection(unittest.IsolatedAsyncioTestCase):
    def _start(self, **kw) -> LoopLagWatchdog:
        wd = LoopLagWatchdog(asyncio.get_running_loop(), **kw)
        wd.start()
        self.addCleanup(wd.stop)
        return wd

    async def test_reports_a_blocking_call_and_names_it(self):
        wd = self._start(threshold_ms=30.0)
        await asyncio.sleep(0.05)              # let it tick a few times first
        _block_the_loop(0.25)                  # stalls the loop
        await asyncio.sleep(0.15)              # loop recovers; watchdog reports
        self.assertGreaterEqual(wd.stall_count, 1)
        report = wd.reports[-1]
        self.assertGreaterEqual(report["stall_ms"], 150)
        self.assertGreater(report["samples"], 5)
        loop_threads = [t for t in report["threads"] if t["is_event_loop"]]
        self.assertEqual(len(loop_threads), 1)
        frames = " ".join(loop_threads[0]["top_stacks"][0]["stack"])
        self.assertIn("_block_the_loop", frames)
        self.assertFalse(loop_threads[0]["idle"])
        # Blocked in one place: that one frame accounts for (nearly) every sample.
        # Not exactly all: the stall's first/last samples can catch a neighbouring frame.
        top = loop_threads[0]["top_stacks"][0]
        self.assertGreaterEqual(top["count"], 0.8 * loop_threads[0]["samples"])
        self.assertLessEqual(loop_threads[0]["distinct_innermost_frames"], 3)

    async def test_snapshots_every_thread_not_just_the_loop(self):
        stop = threading.Event()
        worker = threading.Thread(target=_other_thread_work, args=(stop,), name="ingest-like")
        worker.start()
        self.addCleanup(lambda: (stop.set(), worker.join()))
        wd = self._start(threshold_ms=30.0)
        await asyncio.sleep(0.05)
        _block_the_loop(0.2)
        await asyncio.sleep(0.15)
        names = {t["thread"] for t in wd.reports[-1]["threads"]}
        self.assertIn("ingest-like", names)
        self.assertNotIn("loop-lag-watchdog", names, "must not report on itself")

    async def test_healthy_loop_produces_no_report(self):
        wd = self._start(threshold_ms=50.0)
        for _ in range(20):
            await asyncio.sleep(0.025)
        self.assertEqual(wd.stall_count, 0)
        self.assertEqual(list(wd.reports), [])

    async def test_below_threshold_is_ignored(self):
        wd = self._start(threshold_ms=200.0)
        await asyncio.sleep(0.05)
        _block_the_loop(0.08)
        await asyncio.sleep(0.1)
        self.assertEqual(wd.stall_count, 0)

    async def test_stop_ends_the_thread_and_is_idempotent(self):
        wd = self._start(threshold_ms=30.0)
        self.assertTrue(wd.running)
        wd.stop()
        wd.stop()
        self.assertFalse(wd.running)


class TestWatcherLifecycle(unittest.TestCase):
    def test_watcher_exits_by_itself_when_its_loop_closes(self):
        """Otherwise a closed loop would look like an endless stall."""
        loop = asyncio.new_event_loop()
        wd = LoopLagWatchdog(loop, threshold_ms=20.0)
        wd.start()
        loop.run_until_complete(asyncio.sleep(0.05))
        loop.close()
        time.sleep(0.15)
        self.assertFalse(wd.running)
        self.assertEqual(wd.stall_count, 0)


class TestModuleApi(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        wd_mod.disable()

    async def test_off_by_default(self):
        wd_mod.disable()
        s = wd_mod.status()
        self.assertFalse(s["enabled"])
        self.assertEqual(s["recent_stalls"], [])

    async def test_endpoint_handlers_toggle_and_report_status(self):
        from backend.api.system.router import (
            LoopLagWatchdogToggle,
            get_loop_lag_watchdog,
            toggle_loop_lag_watchdog,
        )

        r = await toggle_loop_lag_watchdog(LoopLagWatchdogToggle(enabled=True, threshold_ms=75))
        self.assertTrue(r["enabled"])
        self.assertEqual(r["threshold_ms"], 75)
        # Idempotent: enabling again just updates the threshold on the same watcher.
        r = await toggle_loop_lag_watchdog(LoopLagWatchdogToggle(enabled=True, threshold_ms=120))
        self.assertEqual(r["threshold_ms"], 120)
        self.assertTrue((await get_loop_lag_watchdog())["enabled"])
        r = await toggle_loop_lag_watchdog(LoopLagWatchdogToggle(enabled=False))
        self.assertFalse(r["enabled"])

    async def test_a_stall_shows_up_in_the_status_the_endpoint_returns(self):
        from backend.api.system.router import (
            LoopLagWatchdogToggle,
            get_loop_lag_watchdog,
            toggle_loop_lag_watchdog,
        )

        await toggle_loop_lag_watchdog(LoopLagWatchdogToggle(enabled=True, threshold_ms=30))
        await asyncio.sleep(0.05)
        _block_the_loop(0.2)
        await asyncio.sleep(0.15)
        status = await get_loop_lag_watchdog()
        self.assertGreaterEqual(status["stall_count"], 1)
        self.assertIn("_block_the_loop", str(status["recent_stalls"][-1]))


class TestEndpointValidation(unittest.TestCase):
    """Bare TestClient (no ``with``): it must NOT run the app lifespan, which
    starts real ingestion against the real DB and providers."""

    def test_rejects_silly_thresholds(self):
        from backend.api.main import app

        client = TestClient(app)
        for bad in (0, 5, 10_000, -1):
            r = client.post("/api/system/loop_lag_watchdog", json={"enabled": True, "threshold_ms": bad})
            self.assertEqual(r.status_code, 422, bad)

    def test_get_reports_the_off_state(self):
        from backend.api.main import app

        wd_mod.disable()
        body = TestClient(app).get("/api/system/loop_lag_watchdog").json()
        self.assertEqual(set(body), {"enabled", "threshold_ms", "stall_count", "recent_stalls"})
        self.assertFalse(body["enabled"])


if __name__ == "__main__":
    unittest.main()
