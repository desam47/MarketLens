"""
Tests for backend.utils.gc_tuning.freeze_startup_heap.

A full garbage collection walks every tracked object and holds the GIL throughout;
with the app's ~540k long-lived startup objects it cost ~99 ms and stalled the whole
API every few minutes (2 stalls / 6 min on the live server, each matched to a
generation-2 collection). Freezing the startup heap removes those objects from it.
"""

import gc
import inspect
import time
import unittest
import weakref

from backend.utils import gc_tuning


def _full_collect_ms() -> float:
    t = time.perf_counter()
    gc.collect()
    return (time.perf_counter() - t) * 1000.0


class TestFreezeStartupHeap(unittest.TestCase):
    def setUp(self):
        self.addCleanup(
            gc.unfreeze
        )  # don't leave a permanent generation behind in the test process

    def test_a_frozen_heap_no_longer_costs_a_full_collection(self):
        heap = [[i] for i in range(400_000)]  # long-lived tracked containers
        self.addCleanup(heap.clear)
        before = min(_full_collect_ms() for _ in range(3))
        frozen = gc_tuning.freeze_startup_heap()
        after = min(_full_collect_ms() for _ in range(3))
        self.assertGreaterEqual(frozen, 400_000)
        self.assertGreater(before, 20.0, "test heap too small to be meaningful on this machine")
        self.assertLess(after, before / 4, f"full collection {before:.0f} ms -> {after:.0f} ms")

    def test_refcounting_still_frees_frozen_objects(self):
        class Thing:
            pass

        thing = Thing()
        ref = weakref.ref(thing)
        gc_tuning.freeze_startup_heap()  # thing is now in the permanent generation
        del thing
        self.assertIsNone(ref(), "acyclic frozen objects must still be freed by refcounting")

    def test_garbage_is_collected_before_freezing_so_it_is_not_frozen(self):
        class Node:
            pass

        a, b = Node(), Node()
        a.other, b.other = b, a  # a reference cycle, only the GC can free it
        ref = weakref.ref(a)
        del a, b  # now unreachable garbage
        gc_tuning.freeze_startup_heap()  # must collect it first, not freeze it
        self.assertIsNone(ref(), "startup garbage should have been collected, not frozen")

    def test_idempotent_and_returns_a_count(self):
        first = gc_tuning.freeze_startup_heap()
        second = gc_tuning.freeze_startup_heap()
        self.assertIsInstance(first, int)
        self.assertGreaterEqual(second, first)


class TestLifespanFreezesBeforeServing(unittest.TestCase):
    def test_startup_freezes_the_heap_just_before_the_app_starts_serving(self):
        """Source-level check: running the real lifespan starts ingestion against the
        real DB and providers, which tests must not do."""
        from backend.api.main import lifespan

        src = inspect.getsource(lifespan)
        freeze_at = src.index("freeze_startup_heap")
        yield_at = src.index("\n    yield\n")
        self.assertLess(freeze_at, yield_at, "freeze must run during startup, before `yield`")


if __name__ == "__main__":
    unittest.main()
