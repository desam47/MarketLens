"""
The suite must never enqueue a real backfill: ``enqueue_backfill`` talks to the live Redis, whose
RQ worker runs the job for real (provider calls plus ~14,000 bars written to the live database).
The watchlist add/import tests did that on every run.
"""

import unittest

from backend.market_data.services import backfill_queue


class TestNoRealBackfillJobs(unittest.TestCase):
    def test_the_backfill_queue_is_unavailable_under_the_suite(self):
        self.assertIsNone(backfill_queue.get_backfill_queue())

    def test_enqueue_backfill_is_a_no_op(self):
        self.assertIsNone(backfill_queue.enqueue_backfill("AAPL"))


if __name__ == "__main__":
    unittest.main()
