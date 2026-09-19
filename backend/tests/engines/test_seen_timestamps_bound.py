"""
TimeframeEngine's duplicate-tick memory must be bounded.

``_seen_timestamps`` was documented as "all tick timestamps ever accepted" and only
cleared by reset(): ~90 bytes per entry and ~52k new entries a day across the 25
symbols x 10 timeframe engines (~4.7 MB/day, ~140 MB per month of uptime). It is now
a FIFO-bounded set; duplicate detection for anything recent is unchanged.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.engines import timeframe as tf_mod
from backend.engines.timeframe import TimeframeEngine, _BoundedSeen

T0 = datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc)


def _t(i: int) -> datetime:
    return T0 + timedelta(seconds=i)


class TestBoundedSeen(unittest.TestCase):
    def test_membership_and_idempotent_add(self):
        s = _BoundedSeen(10)
        s.add(_t(1))
        s.add(_t(1))
        self.assertIn(_t(1), s)
        self.assertNotIn(_t(2), s)
        self.assertEqual(len(s), 1)

    def test_evicts_oldest_first_and_never_exceeds_maxlen(self):
        s = _BoundedSeen(3)
        for i in range(1, 7):
            s.add(_t(i))
            self.assertLessEqual(len(s), 3)
        self.assertEqual([_t(i) in s for i in range(1, 7)], [False, False, False, True, True, True])

    def test_re_adding_a_present_item_does_not_refresh_its_age(self):
        s = _BoundedSeen(2)
        s.add(_t(1))
        s.add(_t(2))
        s.add(_t(1))          # no-op: still the oldest
        s.add(_t(3))          # evicts t1, not t2
        self.assertNotIn(_t(1), s)
        self.assertIn(_t(2), s)

    def test_clear(self):
        s = _BoundedSeen(3)
        s.add(_t(1))
        s.clear()
        self.assertEqual(len(s), 0)
        self.assertNotIn(_t(1), s)


class TestEngineDedupeIsBounded(unittest.TestCase):
    def _engine(self, cap):
        with patch.object(tf_mod, "_SEEN_TIMESTAMPS_MAX", cap):
            return TimeframeEngine("TEST")

    def test_memory_is_bounded_no_matter_how_many_distinct_ticks_arrive(self):
        engine = self._engine(50)
        for i in range(2_000):
            engine.update_tick(100.0 + i * 0.01, 10, _t(i))
        self.assertEqual(len(engine._seen_timestamps), 50)

    def test_a_recent_duplicate_is_still_detected_and_skipped(self):
        engine = self._engine(50)
        for i in range(200):
            engine.update_tick(100.0, 10, _t(i))
        before = engine.duplicate_count
        engine.update_tick(100.0, 10, _t(199))          # same timestamp as the latest tick
        engine.update_tick(100.0, 10, _t(180))          # and one from within the window
        self.assertEqual(engine.duplicate_count, before + 2)

    def test_default_cap_covers_days_of_one_minute_bars(self):
        self.assertGreaterEqual(tf_mod._SEEN_TIMESTAMPS_MAX, 960 * 3)   # 3 days of 1m bars, extended hours
        self.assertEqual(TimeframeEngine("X")._seen_timestamps.maxlen, tf_mod._SEEN_TIMESTAMPS_MAX)

    def test_reset_clears_the_memory(self):
        engine = self._engine(50)
        for i in range(10):
            engine.update_tick(100.0, 10, _t(i))
        engine.reset()
        self.assertEqual(len(engine._seen_timestamps), 0)
        engine.update_tick(100.0, 10, _t(3))            # not a duplicate any more
        self.assertEqual(engine.duplicate_count, 0)


if __name__ == "__main__":
    unittest.main()
