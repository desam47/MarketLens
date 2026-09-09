"""
Tests for backfill_queue.py — the RQ-based single-flight replacement for
the old per-event-loop asyncio.Lock/Semaphore in backfill_service.py.

Single-flight is now a DB+RQ check (backend/models/backfill_job.py rows,
confirmed against RQ job status) instead of an in-process primitive — see
backfill_queue.py's module docstring for the rationale.
"""
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.market_data.services import backfill_queue


class TestGetBackfillQueue(unittest.TestCase):

    def setUp(self):
        backfill_queue.reset_for_tests()

    def tearDown(self):
        backfill_queue.reset_for_tests()

    def test_returns_none_when_background_disabled(self):
        with patch.object(backfill_queue.settings.background, "enabled", False):
            self.assertIsNone(backfill_queue.get_backfill_queue())

    def test_returns_none_when_redis_unavailable(self):
        with patch("backend.ai.background.get_redis", return_value=None):
            self.assertIsNone(backfill_queue.get_backfill_queue())

    def test_uses_separate_queue_name_from_ai_jobs(self):
        fake_redis = MagicMock()
        fake_queue_cls = MagicMock()
        with (
            patch("backend.ai.background.get_redis", return_value=fake_redis),
            patch("rq.Queue", fake_queue_cls),
        ):
            backfill_queue.get_backfill_queue()
        fake_queue_cls.assert_called_once()
        _, kwargs = fake_queue_cls.call_args
        args = fake_queue_cls.call_args.args
        queue_name = args[0] if args else kwargs.get("name")
        self.assertEqual(queue_name, backfill_queue.settings.background.backfill_queue_name)
        self.assertNotEqual(
            backfill_queue.settings.background.backfill_queue_name,
            backfill_queue.settings.background.queue_name,
        )


class TestEnqueueBackfill(unittest.TestCase):

    def setUp(self):
        backfill_queue.reset_for_tests()

    def tearDown(self):
        backfill_queue.reset_for_tests()

    def test_returns_none_when_queue_unavailable(self):
        with patch.object(backfill_queue, "get_backfill_queue", return_value=None):
            self.assertIsNone(backfill_queue.enqueue_backfill("AAPL"))

    def test_skips_enqueue_when_already_in_flight(self):
        fake_queue = MagicMock()
        with (
            patch.object(backfill_queue, "get_backfill_queue", return_value=fake_queue),
            patch.object(backfill_queue, "_in_flight_job_id", return_value="backfill:AAPL:existing"),
        ):
            result = backfill_queue.enqueue_backfill("AAPL")
        self.assertIsNone(result)
        fake_queue.enqueue.assert_not_called()

    def test_enqueues_and_creates_db_row_when_not_in_flight(self):
        fake_queue = MagicMock()
        mock_db = MagicMock()
        mock_session_local = MagicMock(return_value=mock_db)
        with (
            patch.object(backfill_queue, "get_backfill_queue", return_value=fake_queue),
            patch.object(backfill_queue, "_in_flight_job_id", return_value=None),
            patch("backend.database.SessionLocal", mock_session_local),
        ):
            job_id = backfill_queue.enqueue_backfill("aapl")

        self.assertIsNotNone(job_id)
        assert job_id is not None
        # job_id deliberately does NOT embed the symbol (see
        # enqueue_backfill's comment — some real tickers, e.g. "BRK.B",
        # contain characters RQ's job-id validator rejects). Every lookup
        # goes through the BackfillJob.symbol DB column instead, asserted
        # below via added_row.symbol.
        self.assertTrue(job_id.startswith("backfill-"))
        self.assertRegex(job_id, r"^backfill-[0-9a-f]+$")
        fake_queue.enqueue.assert_called_once()
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        added_row = mock_db.add.call_args.args[0]
        self.assertEqual(added_row.symbol, "AAPL")
        self.assertEqual(added_row.status, "queued")

    def test_enqueue_failure_rolls_back_and_returns_none(self):
        fake_queue = MagicMock()
        fake_queue.enqueue.side_effect = RuntimeError("redis down mid-call")
        mock_db = MagicMock()
        with (
            patch.object(backfill_queue, "get_backfill_queue", return_value=fake_queue),
            patch.object(backfill_queue, "_in_flight_job_id", return_value=None),
            patch("backend.database.SessionLocal", return_value=mock_db),
        ):
            result = backfill_queue.enqueue_backfill("AAPL")
        self.assertIsNone(result)
        mock_db.rollback.assert_called_once()


class TestInFlightJobId(unittest.TestCase):

    def _db_returning(self, latest_job):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = latest_job
        return mock_db

    def test_none_when_no_prior_job(self):
        db = self._db_returning(None)
        with patch("backend.database.SessionLocal", return_value=db):
            self.assertIsNone(backfill_queue._in_flight_job_id("AAPL"))

    def test_none_when_latest_job_is_terminal(self):
        latest = MagicMock(status="completed", job_id="backfill:AAPL:1")
        db = self._db_returning(latest)
        with patch("backend.database.SessionLocal", return_value=db):
            self.assertIsNone(backfill_queue._in_flight_job_id("AAPL"))

    def test_returns_job_id_when_rq_confirms_active(self):
        latest = MagicMock(status="started", job_id="backfill:AAPL:1")
        db = self._db_returning(latest)
        fake_redis = MagicMock()
        fake_rq_job = MagicMock()
        fake_rq_job.get_status.return_value = "started"
        with (
            patch("backend.database.SessionLocal", return_value=db),
            patch("backend.ai.background.get_redis", return_value=fake_redis),
            patch("rq.job.Job.fetch", return_value=fake_rq_job),
        ):
            self.assertEqual(backfill_queue._in_flight_job_id("AAPL"), "backfill:AAPL:1")

    def test_none_when_rq_has_no_record_of_stale_row(self):
        """A worker crash mid-job never writes 'failed' — the DB row is
        stale, and RQ no longer knowing about the job is how that's
        detected (rather than wedging the symbol forever)."""
        latest = MagicMock(status="started", job_id="backfill:AAPL:1")
        db = self._db_returning(latest)
        fake_redis = MagicMock()
        with (
            patch("backend.database.SessionLocal", return_value=db),
            patch("backend.ai.background.get_redis", return_value=fake_redis),
            patch("rq.job.Job.fetch", side_effect=Exception("no such job")),
        ):
            self.assertIsNone(backfill_queue._in_flight_job_id("AAPL"))

    def test_trusts_db_row_when_redis_unavailable(self):
        """Can't confirm with RQ — err toward not double-running a
        backfill rather than risking a duplicate concurrent one."""
        latest = MagicMock(status="queued", job_id="backfill:AAPL:1")
        db = self._db_returning(latest)
        with (
            patch("backend.database.SessionLocal", return_value=db),
            patch("backend.ai.background.get_redis", return_value=None),
        ):
            self.assertEqual(backfill_queue._in_flight_job_id("AAPL"), "backfill:AAPL:1")


class TestGetBackfillJobStatus(unittest.TestCase):
    """Previously untested — the HTTP-level backfill-status endpoint test
    mocks this function itself, so nothing had exercised its own logic."""

    def _db_returning(self, record):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = record
        return mock_db

    def test_returns_none_when_no_job_exists(self):
        db = self._db_returning(None)
        with patch("backend.database.SessionLocal", return_value=db):
            self.assertIsNone(backfill_queue.get_backfill_job_status("AAPL"))

    def test_returns_dict_shape_for_terminal_status(self):
        record = MagicMock(
            symbol="AAPL", job_id="backfill-abc", status="completed",
            tier1_written=10, tier2_written=5, tier3_written=2,
            gaps_found=0, gaps_filled=0, result=None, error=None,
            created_at=None, started_at=None, completed_at=None,
        )
        db = self._db_returning(record)
        with patch("backend.database.SessionLocal", return_value=db):
            status = backfill_queue.get_backfill_job_status("AAPL")
        assert status is not None
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["tier1_written"], 10)

    def test_parses_result_json(self):
        record = MagicMock(
            symbol="AAPL", job_id="backfill-abc", status="partial",
            tier1_written=1, tier2_written=1, tier3_written=1,
            gaps_found=2, gaps_filled=1, result='{"1d": {"gaps_found": 2}}',
            error=None, created_at=None, started_at=None, completed_at=None,
        )
        db = self._db_returning(record)
        with patch("backend.database.SessionLocal", return_value=db):
            status = backfill_queue.get_backfill_job_status("AAPL")
        assert status is not None
        self.assertEqual(status["result"], {"1d": {"gaps_found": 2}})

    def test_promotes_queued_to_started_when_rq_confirms(self):
        record = MagicMock(status="queued", job_id="backfill-abc", started_at=None)
        db = self._db_returning(record)
        fake_rq_job = MagicMock()
        fake_rq_job.get_status.return_value = "started"
        with (
            patch("backend.database.SessionLocal", return_value=db),
            patch("backend.ai.background.get_redis", return_value=MagicMock()),
            patch("rq.job.Job.fetch", return_value=fake_rq_job),
        ):
            backfill_queue.get_backfill_job_status("AAPL")
        self.assertEqual(record.status, "started")
        db.commit.assert_called_once()

    def test_reconciles_to_failed_when_rq_has_lost_the_job(self):
        """The scenario a 2026-09-08 completeness audit found live: a
        Redis flush (or a worker crash before it wrote "started") can
        leave a DB row saying "queued"/"started" forever with nothing in
        Redis to back it up. A status poller must not report that
        indefinitely — see this function's own comment for the incident."""
        record = MagicMock(status="started", job_id="backfill-abc", error=None)
        db = self._db_returning(record)
        with (
            patch("backend.database.SessionLocal", return_value=db),
            patch("backend.ai.background.get_redis", return_value=MagicMock()),
            patch("rq.job.Job.fetch", side_effect=Exception("no such job")),
        ):
            status = backfill_queue.get_backfill_job_status("AAPL")
        self.assertEqual(record.status, "failed")
        self.assertIsNotNone(record.error)
        assert status is not None
        self.assertEqual(status["status"], "failed")
        db.commit.assert_called_once()

    def test_does_not_touch_rq_for_terminal_status(self):
        """A completed/partial/failed row is authoritative on its own —
        no need to consult RQ, which may have long since forgotten the
        job (result_ttl expiry) without that meaning anything is wrong."""
        record = MagicMock(status="completed", job_id="backfill-abc", error=None)
        db = self._db_returning(record)
        with (
            patch("backend.database.SessionLocal", return_value=db),
            patch("backend.ai.background.get_redis") as mock_get_redis,
        ):
            backfill_queue.get_backfill_job_status("AAPL")
        mock_get_redis.assert_not_called()


class TestCancelBackfill(unittest.TestCase):

    def test_returns_false_when_nothing_to_cancel(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        with patch("backend.database.SessionLocal", return_value=mock_db):
            self.assertFalse(backfill_queue.cancel_backfill("AAPL"))

    def test_marks_row_failed_when_in_flight_job_found(self):
        record = MagicMock(status="queued", job_id="backfill:AAPL:1")
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = record
        with (
            patch("backend.database.SessionLocal", return_value=mock_db),
            patch("backend.ai.background.get_redis", return_value=None),
        ):
            self.assertTrue(backfill_queue.cancel_backfill("AAPL"))
        self.assertEqual(record.status, "failed")
        self.assertIn("cancelled", record.error)
        mock_db.commit.assert_called_once()


class TestConcurrentEnqueueSingleFlight(unittest.TestCase):
    """Real cross-thread integration test for the exact race the redesign
    was meant to close: two near-simultaneous callers enqueuing a backfill
    for the SAME symbol must not both succeed.

    enqueue_backfill's check-then-act (check _in_flight_job_id, then
    enqueue + insert a BackfillJob row) is not atomic on its own — this
    exercises the real Redis SET-NX-EX lock added around it (2026-09-08,
    found via a post-redesign completeness audit: without that lock, this
    specific race survived even though the class of bug it belongs to —
    two uncoordinated backfill_symbol_history callers — was the whole
    reason for the RQ-based redesign).

    Uses the REAL RQ queue and REAL Redis, NOT a mocked queue — deliberately.
    An earlier version of this test stubbed ``queue.enqueue()`` with a
    MagicMock, which let the lock test pass or fail seemingly at random
    (~1 in 10 runs showed both callers "succeeding"). Root cause turned out
    to be in the test double, not the lock: verified via `redis-cli monitor`
    that the SET-NX-EX lock itself always serialized correctly (acquire,
    then release, THEN the second acquire — never overlapping) — but
    ``_in_flight_job_id``'s RQ-confirmation step (``Job.fetch(...)``, a
    deliberate anti-staleness check for a crashed-worker's stuck DB row)
    found nothing for a job the mock never actually created in Redis, so it
    reported the first (genuinely in-flight) job as stale/gone and let the
    second caller straight through — regardless of whether the lock had
    already fixed the real race. Using the real queue means Job.fetch has
    something genuine to confirm, so this test now actually exercises both
    mechanisms together. No worker consumes ``marketlens-backfill`` in this
    test process, so the enqueued job is never actually executed."""

    def setUp(self):
        import uuid
        backfill_queue.reset_for_tests()
        self.symbol = f"ZZRACE{uuid.uuid4().hex[:6].upper()}"
        self._rq_job_ids: list[str] = []

    def tearDown(self):
        from backend.database import SessionLocal
        from backend.models import BackfillJob
        db = SessionLocal()
        try:
            db.query(BackfillJob).filter(BackfillJob.symbol == self.symbol).delete()
            db.commit()
        finally:
            db.close()
        # Clean up the real RQ job(s) so they don't linger in Redis.
        try:
            from backend.ai.background import get_redis
            client = get_redis()
            if client is not None:
                from rq.job import Job
                for job_id in self._rq_job_ids:
                    try:
                        Job.fetch(job_id, connection=client).delete()
                    except Exception:
                        pass
        except Exception:
            pass
        backfill_queue.reset_for_tests()

    def test_two_concurrent_enqueues_for_same_symbol_only_create_one_job(self):
        results: list = []
        barrier = threading.Barrier(2)

        def call():
            barrier.wait(timeout=5)
            results.append(backfill_queue.enqueue_backfill(self.symbol))

        t1 = threading.Thread(target=call)
        t2 = threading.Thread(target=call)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        successes = [r for r in results if r is not None]
        self._rq_job_ids.extend(successes)
        self.assertEqual(
            len(successes), 1,
            f"expected exactly one enqueue to win the race, got {results}",
        )

        from backend.database import SessionLocal
        from backend.models import BackfillJob
        db = SessionLocal()
        try:
            rows = db.query(BackfillJob).filter(BackfillJob.symbol == self.symbol.upper()).all()
            self.assertEqual(
                len(rows), 1,
                "exactly one BackfillJob row should exist after the race",
            )
        finally:
            db.close()

    def test_lock_is_released_so_a_later_call_can_still_enqueue(self):
        """The lock must not outlive the call — a second, non-concurrent
        call for the same symbol after the first completes should still
        be blocked only by the (correct) in-flight check, not by a
        leaked lock."""
        first = backfill_queue.enqueue_backfill(self.symbol)
        self.assertIsNotNone(first)
        assert first is not None
        self._rq_job_ids.append(first)

        # Immediately after, the lock key itself must be gone — verify
        # directly against Redis rather than inferring it from
        # enqueue_backfill's own return value (which would also be None
        # here for the unrelated reason that a job is now legitimately
        # in flight).
        from backend.ai.background import get_redis
        client = get_redis()
        if client is not None:
            self.assertIsNone(client.get(f"backfill:enqueue-lock:{self.symbol.upper()}"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
