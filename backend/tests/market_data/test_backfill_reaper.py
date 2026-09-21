"""
``reap_orphaned_jobs``: a worker killed mid-job (RQ ``AbandonedJobError``) never reaches the code
that writes a terminal status, so its ``BackfillJob`` row stayed ``started`` forever -- 17 such
rows had piled up. The reaper reconciles them against RQ, conservatively.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from rq.exceptions import NoSuchJobError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.market_data.services.backfill_queue import reap_orphaned_jobs
from backend.models import BackfillJob


def _now():
    return datetime.utcnow()


class TestReapOrphanedJobs(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        BackfillJob.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        self.rq = {}  # job_id -> RQ job mock, or an exception
        for target, obj in (
            ("backend.database.SessionLocal", self.Session),
            ("backend.ai.background.get_redis", MagicMock(return_value=object())),
            ("rq.job.Job.fetch", self._fetch),
        ):
            p = patch(target, obj)
            p.start()
            self.addCleanup(p.stop)

    def _fetch(self, job_id, connection=None):
        outcome = self.rq[job_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def _rq_job(self, status, exc_info=None):
        from rq.job import JobStatus

        job = MagicMock(exc_info=exc_info)
        job.get_status.return_value = JobStatus(status)  # RQ 2.x returns the enum, not a str
        return job

    def _row(self, job_id, status, age=timedelta(hours=2)):
        with self.Session() as db:
            db.add(
                BackfillJob(job_id=job_id, symbol="AAPL", status=status, created_at=_now() - age)
            )
            db.commit()

    def _get(self, job_id):
        with self.Session() as db:
            return db.query(BackfillJob).filter_by(job_id=job_id).one()

    def test_a_started_row_whose_worker_died_is_marked_failed(self):
        self._row("j1", "started")
        self.rq["j1"] = self._rq_job(
            "failed", "Moved to FailedJobRegistry, due to AbandonedJobError, at ..."
        )
        self.assertEqual(reap_orphaned_jobs(), 1)
        row = self._get("j1")
        self.assertEqual(row.status, "failed")
        self.assertIn("AbandonedJobError", row.error)
        self.assertIn("reports the job as failed", row.error)  # not "JobStatus.FAILED"
        self.assertIsNotNone(row.completed_at)

    def test_a_row_rq_has_no_record_of_is_marked_failed(self):
        self._row("j1", "queued")
        self.rq["j1"] = NoSuchJobError("gone")
        self.assertEqual(reap_orphaned_jobs(), 1)
        self.assertIn("lost from the queue", self._get("j1").error)

    def test_jobs_rq_still_considers_active_are_left_alone(self):
        for i, status in enumerate(("queued", "started", "deferred", "scheduled")):
            self._row(f"j{i}", "started")
            self.rq[f"j{i}"] = self._rq_job(status)
        self.assertEqual(reap_orphaned_jobs(), 0)
        for i in range(4):
            self.assertEqual(self._get(f"j{i}").status, "started")

    def test_recent_rows_are_never_touched(self):
        self._row("young", "started", age=timedelta(minutes=5))
        self.rq["young"] = NoSuchJobError("gone")
        self.assertEqual(reap_orphaned_jobs(), 0)
        self.assertEqual(self._get("young").status, "started")

    def test_terminal_rows_are_never_touched(self):
        for i, status in enumerate(("completed", "partial", "failed")):
            self._row(f"t{i}", status)
            self.rq[f"t{i}"] = NoSuchJobError("gone")
        self.assertEqual(reap_orphaned_jobs(), 0)

    def test_an_unreachable_redis_reaps_nothing(self):
        self._row("j1", "started")
        self.rq["j1"] = ConnectionError("redis down")  # not proof the job is gone
        self.assertEqual(reap_orphaned_jobs(), 0)
        self.assertEqual(self._get("j1").status, "started")

    def test_no_redis_client_reaps_nothing(self):
        self._row("j1", "started")
        with patch("backend.ai.background.get_redis", return_value=None):
            self.assertEqual(reap_orphaned_jobs(), 0)
        self.assertEqual(self._get("j1").status, "started")

    def test_only_the_dead_rows_of_a_mixed_batch_are_reaped(self):
        self._row("dead", "started")
        self._row("alive", "started")
        self.rq["dead"] = self._rq_job("failed")
        self.rq["alive"] = self._rq_job("started")
        self.assertEqual(reap_orphaned_jobs(), 1)
        self.assertEqual(
            (self._get("dead").status, self._get("alive").status), ("failed", "started")
        )


class TestReaperWiring(unittest.IsolatedAsyncioTestCase):
    async def test_the_hourly_loop_runs_the_reaper_and_survives_its_failure(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        svc = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])
        svc.manager = MagicMock()

        async def stop_after_one(*_a, **_k):
            svc.is_running = False

        svc._jittered_sleep = stop_after_one
        svc.is_running = True
        reaper = MagicMock(side_effect=RuntimeError("redis down"))
        with (
            patch("backend.market_data.services.ingestion_service.SessionLocal"),
            patch("backend.repositories.bar_repository.prune_bars_by_retention", return_value={}),
            patch("backend.repositories.status_retention.prune_status_tables", return_value={}),
            patch("backend.market_data.services.backfill_queue.reap_orphaned_jobs", reaper),
            patch(
                "backend.repositories.signal_repository.prune_signals_by_retention", return_value={}
            ),
            self.assertLogs("backend.market_data.services.ingestion_service", "ERROR") as cm,
        ):
            await svc._retention_prune_loop()
        reaper.assert_called_once()
        self.assertTrue(any("orphaned backfill" in r.getMessage() for r in cm.records))


if __name__ == "__main__":
    unittest.main()
