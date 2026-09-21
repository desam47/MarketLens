"""
Tests for ``backend.api.ttl_cache.ttl_cached``.

The decorator has two jobs beyond plain memoisation, and both are easy to
break: async *single-flight* (N concurrent misses for a key run the wrapped
function once) and safe handling of failures / cancellation. Until now the
suite had no tests for it at all.
"""

import asyncio
import gc
import unittest

from cachetools import TTLCache

from backend.api.ttl_cache import ttl_cached


class _Clock:
    """Manually advanced clock so TTL expiry is deterministic."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestAsyncSingleFlight(unittest.IsolatedAsyncioTestCase):
    def _make(self, *, fn_delay=0.05, ttl=30, timer=None, result=lambda s: f"r-{s}"):
        cache = TTLCache(maxsize=16, ttl=ttl, **({"timer": timer} if timer else {}))
        calls: list[str] = []

        @ttl_cached(cache, key_fn=lambda s: s.upper())
        async def fetch(s: str):
            calls.append(s)
            await asyncio.sleep(fn_delay)
            return result(s)

        return cache, calls, fetch

    async def test_concurrent_misses_compute_once(self):
        cache, calls, fetch = self._make()
        results = await asyncio.gather(*[fetch("aapl") for _ in range(10)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(results), {"r-aapl"})
        self.assertEqual(cache["AAPL"], "r-aapl")

    async def test_hit_is_served_without_recompute(self):
        _, calls, fetch = self._make()
        await fetch("aapl")
        await fetch("AAPL")  # key_fn normalises case -> same entry
        self.assertEqual(len(calls), 1)

    async def test_distinct_keys_compute_independently_and_concurrently(self):
        _, calls, fetch = self._make(fn_delay=0.2)
        loop = asyncio.get_running_loop()
        start = loop.time()
        results = await asyncio.gather(fetch("aapl"), fetch("msft"))
        elapsed = loop.time() - start
        self.assertEqual(sorted(calls), ["aapl", "msft"])
        self.assertEqual(results, ["r-aapl", "r-msft"])
        self.assertLess(elapsed, 0.35, "different keys must not serialise behind each other")

    async def test_none_result_is_cached(self):
        _, calls, fetch = self._make(result=lambda s: None)
        self.assertIsNone(await fetch("x"))
        self.assertIsNone(await fetch("x"))
        self.assertEqual(len(calls), 1)

    async def test_entry_expires_and_recomputes(self):
        clock = _Clock()
        _, calls, fetch = self._make(ttl=10, timer=clock)
        await fetch("x")
        clock.now = 9.0
        await fetch("x")
        self.assertEqual(len(calls), 1)  # still fresh
        clock.now = 11.0
        await fetch("x")
        self.assertEqual(len(calls), 2)  # expired -> recomputed, no KeyError


class TestFailureHandling(unittest.IsolatedAsyncioTestCase):
    async def test_failure_is_shared_and_not_cached(self):
        cache = TTLCache(maxsize=4, ttl=30)
        attempts = 0

        @ttl_cached(cache, key_fn=lambda s: s)
        async def boom(s: str):
            nonlocal attempts
            attempts += 1
            await asyncio.sleep(0.05)
            raise RuntimeError("upstream down")

        outcomes = await asyncio.gather(*[boom("k") for _ in range(5)], return_exceptions=True)
        self.assertEqual(attempts, 1, "concurrent callers must share one failing computation")
        self.assertTrue(all(isinstance(o, RuntimeError) for o in outcomes))
        self.assertNotIn("k", cache)

        with self.assertRaises(RuntimeError):
            await boom("k")
        self.assertEqual(attempts, 2, "a failure must not poison the key")

    async def test_success_after_failure_is_cached(self):
        cache = TTLCache(maxsize=4, ttl=30)
        state = {"fail": True, "calls": 0}

        @ttl_cached(cache, key_fn=lambda s: s)
        async def flaky(s: str):
            state["calls"] += 1
            if state["fail"]:
                raise ValueError("first try fails")
            return "ok"

        with self.assertRaises(ValueError):
            await flaky("k")
        state["fail"] = False
        self.assertEqual(await flaky("k"), "ok")
        self.assertEqual(await flaky("k"), "ok")
        self.assertEqual(state["calls"], 2)

    async def test_abandoned_failed_computation_logs_no_unretrieved_exception(self):
        """A failing task nobody awaits any more must not emit
        'Task exception was never retrieved'."""
        loop = asyncio.get_running_loop()
        reported: list[dict] = []
        loop.set_exception_handler(lambda _loop, ctx: reported.append(ctx))

        @ttl_cached(TTLCache(maxsize=4, ttl=30), key_fn=lambda s: s)
        async def boom(s: str):
            await asyncio.sleep(0.1)
            raise RuntimeError("late failure")

        caller = asyncio.create_task(boom("k"))
        await asyncio.sleep(0.02)
        caller.cancel()  # the only waiter goes away; the shared task keeps running
        with self.assertRaises(asyncio.CancelledError):
            await caller
        await asyncio.sleep(0.2)  # let the orphaned computation fail
        gc.collect()
        self.assertEqual(reported, [])


class TestCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_first_caller_does_not_cancel_shared_computation(self):
        cache = TTLCache(maxsize=4, ttl=30)
        finished = 0

        @ttl_cached(cache, key_fn=lambda s: s)
        async def work(s: str):
            nonlocal finished
            await asyncio.sleep(0.2)
            finished += 1
            return "done"

        first = asyncio.create_task(work("k"))
        await asyncio.sleep(0.05)
        follower = asyncio.create_task(work("k"))
        await asyncio.sleep(0.01)

        first.cancel()  # e.g. the first HTTP client disconnected
        with self.assertRaises(asyncio.CancelledError):
            await first

        self.assertEqual(await follower, "done")
        self.assertEqual(finished, 1)
        self.assertEqual(cache["k"], "done")

    async def test_result_is_cached_even_if_every_caller_cancelled(self):
        cache = TTLCache(maxsize=4, ttl=30)

        @ttl_cached(cache, key_fn=lambda s: s)
        async def work(s: str):
            await asyncio.sleep(0.1)
            return "done"

        caller = asyncio.create_task(work("k"))
        await asyncio.sleep(0.02)
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        await asyncio.sleep(0.2)
        self.assertEqual(cache.get("k"), "done")  # work wasn't wasted


class TestSyncWrapper(unittest.TestCase):
    def test_sync_function_is_memoised(self):
        cache = TTLCache(maxsize=4, ttl=30)
        calls = 0

        @ttl_cached(cache, key_fn=lambda s: s)
        def square(s: int) -> int:
            nonlocal calls
            calls += 1
            return s * s

        self.assertEqual(square(4), 16)
        self.assertEqual(square(4), 16)
        self.assertEqual(calls, 1)

    def test_sync_exception_propagates_and_is_not_cached(self):
        cache = TTLCache(maxsize=4, ttl=30)

        @ttl_cached(cache, key_fn=lambda s: s)
        def bad(s: str):
            raise KeyError(s)

        with self.assertRaises(KeyError):
            bad("k")
        self.assertNotIn("k", cache)

    def test_sync_expiry_is_a_miss_not_a_keyerror(self):
        clock = _Clock()
        cache = TTLCache(maxsize=4, ttl=10, timer=clock)
        calls = 0

        @ttl_cached(cache, key_fn=lambda s: s)
        def f(s: str):
            nonlocal calls
            calls += 1
            return calls

        self.assertEqual(f("k"), 1)
        clock.now = 11.0
        self.assertEqual(f("k"), 2)


if __name__ == "__main__":
    unittest.main()
