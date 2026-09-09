"""
Tests for backfill_service.py's gap-check-and-fill pipeline.

Regression coverage note: this file used to test cross-event-loop
coordination for a module-level asyncio.Lock/Semaphore (the 2026-09-09
incident: two independent, uncoordinated callers of backfill_symbol_history
running on two different event loops in the same process). That root
cause — two racing callers — no longer exists: backfill_symbol_history now
has exactly ONE call site (backfill_symbol_task, run inside an RQ worker
process via backend/market_data/services/backfill_queue.py), so this
module no longer has, and no longer needs, any lock/semaphore of its own.
See test_backfill_queue.py for the single-flight coverage that replaced
this file's old cross-loop tests (now enforced via a DB+RQ check upstream
of enqueue, not an in-process primitive).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.market_data.services import backfill_service


class TestNoLeftoverLockMachinery(unittest.TestCase):
    """The per-event-loop lock/semaphore this module used to carry is gone
    — assert it stays gone rather than silently reappearing in a future
    edit (it was a stopgap for a design flaw that no longer exists; see
    the module docstring)."""

    def test_no_lock_helpers(self):
        for name in ("_get_lock", "_get_backfill_semaphore", "_backfill_locks",
                     "_lock_guards_by_loop", "_backfill_semaphores_by_loop"):
            self.assertFalse(
                hasattr(backfill_service, name),
                f"backfill_service.{name} should not exist anymore",
            )

    def test_backfill_symbol_task_exists(self):
        """The single call site the module docstring promises."""
        self.assertTrue(callable(backfill_service.backfill_symbol_task))


class TestCountContiguousSpans(unittest.TestCase):
    """_count_contiguous_spans: reporting-only helper so a single multi-day
    outage doesn't get logged/reported as if it were N separate gaps (the
    plan had specified find_gaps itself return coalesced ranges; kept the
    flat-list return there since the patch loop benefits from it, fixed the
    misleading count downstream here instead — see the function's own
    docstring)."""

    def test_empty(self):
        self.assertEqual(backfill_service._count_contiguous_spans([], "1d"), 0)

    def test_single_gap(self):
        self.assertEqual(
            backfill_service._count_contiguous_spans([datetime(2026, 1, 2)], "1d"), 1,
        )

    def test_one_contiguous_run_across_a_weekend(self):
        # Fri, Mon, Tue missing 1d bars — one 3-trading-day outage that
        # happens to span a weekend in the calendar.
        gaps = [datetime(2026, 1, 2), datetime(2026, 1, 5), datetime(2026, 1, 6)]
        self.assertEqual(backfill_service._count_contiguous_spans(gaps, "1d"), 1)

    def test_two_separate_gaps(self):
        gaps = [datetime(2026, 1, 2), datetime(2026, 3, 2)]
        self.assertEqual(backfill_service._count_contiguous_spans(gaps, "1d"), 2)

    def test_1m_uses_a_tight_threshold(self):
        # Two 1m gaps 3 minutes apart are separate outages, not one —
        # unlike 1d, where that gap would be well within the same
        # weekend-spanning threshold.
        base = datetime(2026, 1, 2, 10, 0)
        gaps = [base, base + timedelta(minutes=3)]
        self.assertEqual(backfill_service._count_contiguous_spans(gaps, "1m"), 2)


class TestCheckAndFillGaps(unittest.IsolatedAsyncioTestCase):
    """_check_and_fill_gaps: the new step inserted between a tier's
    fetch-and-write and the resample that depends on it."""

    async def test_no_gaps_when_db_empty(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.one.return_value = (None, None)
        result = await backfill_service._check_and_fill_gaps(
            db, "AAPL", "1d", [], "5y"
        )
        self.assertEqual(
            result,
            {"gaps_found": 0, "gaps_filled": 0, "remaining_gap_count": 0, "gap_span_count": 0},
        )

    async def test_no_gaps_when_find_gaps_returns_empty(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.one.return_value = (
            datetime(2026, 1, 2), datetime(2026, 1, 5),
        )
        with patch(
            "backend.repositories.bar_repository.find_gaps", return_value=[]
        ):
            result = await backfill_service._check_and_fill_gaps(
                db, "AAPL", "1d", ["yahoo_finance"], "5y"
            )
        self.assertEqual(result["gaps_found"], 0)
        self.assertEqual(result["gaps_filled"], 0)

    async def test_patches_gap_from_fallback_provider(self):
        """A gap timestamp present in a fallback provider's response gets
        written; timestamps outside the gap set are ignored."""
        db = MagicMock()
        db.query.return_value.filter.return_value.one.return_value = (
            datetime(2026, 1, 2), datetime(2026, 1, 5),
        )
        gap_ts = datetime(2026, 1, 3)
        other_ts = datetime(2026, 1, 4)

        from backend.models import Bar, DataStatus
        gap_bar = Bar(
            symbol="AAPL", timestamp=gap_ts, open=1, high=1, low=1, close=1,
            volume=100, timeframe="1d", provider="yahoo_finance",
            data_status=DataStatus.HISTORICAL,
        )
        other_bar = Bar(
            symbol="AAPL", timestamp=other_ts, open=1, high=1, low=1, close=1,
            volume=100, timeframe="1d", provider="yahoo_finance",
            data_status=DataStatus.HISTORICAL,
        )
        mock_provider = MagicMock()
        mock_provider.get_historical_bars.return_value = [gap_bar, other_bar]

        with (
            patch(
                "backend.repositories.bar_repository.find_gaps",
                return_value=[gap_ts],
            ),
            patch.object(backfill_service, "_instantiate_provider", return_value=mock_provider),
            patch.object(backfill_service, "_write_bars_in_chunks", new=AsyncMock(return_value=1)) as mock_write,
        ):
            result = await backfill_service._check_and_fill_gaps(
                db, "AAPL", "1d", ["yahoo_finance"], "5y"
            )

        self.assertEqual(
            result,
            {"gaps_found": 1, "gaps_filled": 1, "remaining_gap_count": 0, "gap_span_count": 1},
        )
        written_bars = mock_write.call_args.args[1]
        self.assertEqual(len(written_bars), 1)
        self.assertEqual(written_bars[0].timestamp, gap_ts)

    async def test_records_remaining_gap_when_no_provider_has_it(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.one.return_value = (
            datetime(2026, 1, 2), datetime(2026, 1, 5),
        )
        gap_ts = datetime(2026, 1, 3)
        mock_provider = MagicMock()
        mock_provider.get_historical_bars.return_value = []  # provider has nothing

        with (
            patch(
                "backend.repositories.bar_repository.find_gaps",
                return_value=[gap_ts],
            ),
            patch.object(backfill_service, "_instantiate_provider", return_value=mock_provider),
        ):
            result = await backfill_service._check_and_fill_gaps(
                db, "AAPL", "1d", ["yahoo_finance"], "5y"
            )

        self.assertEqual(
            result,
            {"gaps_found": 1, "gaps_filled": 0, "remaining_gap_count": 1, "gap_span_count": 1},
        )

    async def test_provider_exception_does_not_raise(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.one.return_value = (
            datetime(2026, 1, 2), datetime(2026, 1, 5),
        )
        with (
            patch(
                "backend.repositories.bar_repository.find_gaps",
                return_value=[datetime(2026, 1, 3)],
            ),
            patch.object(backfill_service, "_instantiate_provider", side_effect=RuntimeError("boom")),
        ):
            result = await backfill_service._check_and_fill_gaps(
                db, "AAPL", "1d", ["yahoo_finance"], "5y"
            )
        self.assertEqual(result["remaining_gap_count"], 1)


class TestBackfillSymbolTask(unittest.TestCase):
    """backfill_symbol_task: the RQ job body — status-row bookkeeping."""

    def test_task_writes_completed_status_on_success(self):
        from backend.database import SessionLocal
        from backend.models import BackfillJob

        db = SessionLocal()
        try:
            job_id = "backfill:TESTSYM:unit1"
            db.add(BackfillJob(job_id=job_id, symbol="TESTSYM", status="queued"))
            db.commit()
        finally:
            db.close()

        fake_result = {
            "symbol": "TESTSYM", "tier1_written": 5, "tier2_written": 3,
            "tier3_written": 2, "gaps_found": 1, "gaps_filled": 1,
            "gap_detail": {"1d": {"gaps_found": 1, "gaps_filled": 1, "remaining_gap_count": 0}},
            "duration_s": 0.1,
        }
        with patch.object(
            backfill_service, "backfill_symbol_history",
            new=AsyncMock(return_value=fake_result),
        ), patch(
            "backend.services.signal_recorder.signal_recorder.backfill_signals_for_symbol",
            return_value=0,
        ):
            backfill_service.backfill_symbol_task("TESTSYM", job_id)

        db = SessionLocal()
        try:
            row = db.query(BackfillJob).filter(BackfillJob.job_id == job_id).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.status, "completed")
            self.assertEqual(row.tier1_written, 5)
            self.assertEqual(row.gaps_found, 1)
            self.assertEqual(row.gaps_filled, 1)
        finally:
            if row is not None:
                db.delete(row)
                db.commit()
            db.close()

    def test_task_writes_partial_status_when_gaps_remain(self):
        from backend.database import SessionLocal
        from backend.models import BackfillJob

        db = SessionLocal()
        try:
            job_id = "backfill:TESTSYM2:unit2"
            db.add(BackfillJob(job_id=job_id, symbol="TESTSYM2", status="queued"))
            db.commit()
        finally:
            db.close()

        fake_result = {
            "symbol": "TESTSYM2", "tier1_written": 5, "tier2_written": 3,
            "tier3_written": 2, "gaps_found": 3, "gaps_filled": 1,
            "gap_detail": {}, "duration_s": 0.1,
        }
        with patch.object(
            backfill_service, "backfill_symbol_history",
            new=AsyncMock(return_value=fake_result),
        ), patch(
            "backend.services.signal_recorder.signal_recorder.backfill_signals_for_symbol",
            return_value=0,
        ):
            backfill_service.backfill_symbol_task("TESTSYM2", job_id)

        db = SessionLocal()
        try:
            row = db.query(BackfillJob).filter(BackfillJob.job_id == job_id).first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.status, "partial")
        finally:
            if row is not None:
                db.delete(row)
                db.commit()
            db.close()

    def test_task_writes_failed_status_on_exception(self):
        from backend.database import SessionLocal
        from backend.models import BackfillJob

        db = SessionLocal()
        try:
            job_id = "backfill:TESTSYM3:unit3"
            db.add(BackfillJob(job_id=job_id, symbol="TESTSYM3", status="queued"))
            db.commit()
        finally:
            db.close()

        with patch.object(
            backfill_service, "backfill_symbol_history",
            new=AsyncMock(side_effect=RuntimeError("provider exploded")),
        ):
            backfill_service.backfill_symbol_task("TESTSYM3", job_id)

        db = SessionLocal()
        try:
            row = db.query(BackfillJob).filter(BackfillJob.job_id == job_id).first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.status, "failed")
            self.assertIn("provider exploded", row.error or "")
        finally:
            if row is not None:
                db.delete(row)
                db.commit()
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
