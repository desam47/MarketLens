"""Tests for backend/observability/metrics.py"""

import sys
import unittest

sys.path.insert(0, ".")

from backend.observability.metrics import (
    get_snapshot,
    get_tracemalloc_snapshot,
    start_memory_profiling,
    stop_memory_profiling,
)


class TestMemoryProfiling(unittest.TestCase):
    """Task #142: tracemalloc heap profiling."""

    def setUp(self):
        # Ensure profiling is off before each test.
        stop_memory_profiling()

    def tearDown(self):
        stop_memory_profiling()

    def test_start_stop_idempotent(self):
        """start/stop called twice in a row must not raise."""
        start_memory_profiling()
        start_memory_profiling()  # already running — must not raise
        snap1 = get_tracemalloc_snapshot()
        self.assertIsNotNone(snap1)

        stop_memory_profiling()
        stop_memory_profiling()  # already stopped — must not raise
        snap2 = get_tracemalloc_snapshot()
        self.assertIsNone(snap2)

    def test_snapshot_shape_when_enabled(self):
        """With profiling on, get_tracemalloc_snapshot returns all required keys."""
        start_memory_profiling()
        snap = get_tracemalloc_snapshot()
        self.assertIsNotNone(snap)
        self.assertIn("current_mb", snap)
        self.assertIn("peak_mb", snap)
        self.assertIn("top_allocations", snap)
        self.assertIsInstance(snap["current_mb"], float)
        self.assertIsInstance(snap["peak_mb"], float)
        self.assertIsInstance(snap["top_allocations"], list)

    def test_snapshot_shape_when_disabled(self):
        """With profiling off, get_tracemalloc_snapshot returns None."""
        snap = get_tracemalloc_snapshot()
        self.assertIsNone(snap)

    def test_get_snapshot_includes_profiling_fields(self):
        """get_snapshot() includes the new heap fields."""
        start_memory_profiling()
        snap = get_snapshot()
        self.assertIn("memory_profiling_enabled", snap)
        self.assertIn("python_heap_current_mb", snap)
        self.assertIn("python_heap_peak_mb", snap)
        self.assertIn("python_heap_top_allocations", snap)
        self.assertTrue(snap["memory_profiling_enabled"])
        self.assertIsInstance(snap["python_heap_current_mb"], float)
        self.assertIsInstance(snap["python_heap_peak_mb"], float)
        self.assertIsInstance(snap["python_heap_top_allocations"], list)

    def test_get_snapshot_none_heap_fields_when_disabled(self):
        """get_snapshot() returns None for heap fields when profiling is off."""
        snap = get_snapshot()
        self.assertFalse(snap["memory_profiling_enabled"])
        self.assertIsNone(snap["python_heap_current_mb"])
        self.assertIsNone(snap["python_heap_peak_mb"])
        self.assertIsNone(snap["python_heap_top_allocations"])

    def test_top_allocations_contain_size_kb(self):
        """top_allocations entries have 'frame' and 'size_kb' keys."""
        start_memory_profiling()
        # Trigger a small allocation so tracemalloc has frames to report.
        _ = [None] * 10000  # noqa: F841
        snap = get_tracemalloc_snapshot()
        self.assertGreater(len(snap["top_allocations"]), 0)
        entry = snap["top_allocations"][0]
        self.assertIn("frame", entry)
        self.assertIn("size_kb", entry)
        self.assertIsInstance(entry["size_kb"], float)


if __name__ == "__main__":
    unittest.main()
