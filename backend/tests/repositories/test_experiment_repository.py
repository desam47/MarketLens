"""
Tests for ``backend.repositories.experiment_repository``.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.database import Base
from backend.models import Experiment
from backend.repositories.experiment_repository import ExperimentRepository


def _make_engine():
    """Create a fresh temp SQLite engine for isolated testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    eng = create_engine(f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    return eng, tmp.name


class TestExperimentRepository(unittest.TestCase):
    def setUp(self):
        self._eng, self._path = _make_engine()
        self._Session = sessionmaker(bind=self._eng, expire_on_commit=False)
        self._session = self._Session()
        self.repo = ExperimentRepository(db=self._session)
        self._exp_counter = 0

    def tearDown(self):
        self.repo.close()
        self._session.close()
        self._eng.dispose()
        import os as _os

        for p in [self._path, self._path + "-wal", self._path + "-shm"]:
            if _os.path.exists(p):
                _os.unlink(p)

    def _seed(self, **overrides) -> Experiment:
        self._exp_counter += 1
        defaults = dict(
            name=f"Exp {self._exp_counter}",
            strategy_version="v1.0",
            parameters_json="{}",
            symbols="AAPL",
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 6, 1),
            is_start=datetime(2025, 1, 1),
            is_end=datetime(2025, 3, 1),
            val_start=datetime(2025, 3, 1),
            val_end=datetime(2025, 4, 1),
            oos_start=datetime(2025, 4, 1),
            oos_end=datetime(2025, 6, 1),
            signals_requested="RSI_OVERSOLD",
            n_splits=3,
            val_pct=0.2,
            oos_pct=0.2,
            status="completed",
        )
        defaults.update(overrides)
        return self.repo.create(**defaults)

    # --- create / get / list --------------------------------------------

    def test_create_returns_experiment(self):
        exp = self._seed()
        self.assertIsInstance(exp, Experiment)
        self.assertGreater(exp.id, 0)

    def test_get_returns_experiment(self):
        created = self._seed()
        fetched = self.repo.get(created.id)
        self.assertEqual(fetched.id, created.id)
        self.assertEqual(fetched.name, created.name)

    def test_get_unknown_id_returns_none(self):
        self.assertIsNone(self.repo.get(99999))

    def test_list_experiments_returns_all(self):
        self._seed(name="Exp A")
        self._seed(name="Exp B")
        self._seed(name="Exp C")
        results = self.repo.list_experiments(limit=10)
        self.assertEqual(len(results), 3)

    def test_list_experiments_respects_limit(self):
        for i in range(5):
            self._seed(name=f"Exp {i}")
        results = self.repo.list_experiments(limit=3)
        self.assertEqual(len(results), 3)

    def test_list_experiments_filters_by_status(self):
        self._seed(name="Running", status="running")
        self._seed(name="Completed", status="completed")
        self._seed(name="Completed 2", status="completed")
        results = self.repo.list_experiments(status="completed")
        self.assertEqual(len(results), 2)
        for exp in results:
            self.assertEqual(exp.status, "completed")

    # --- update_status ---------------------------------------------------

    def test_update_status_changes_status(self):
        exp = self._seed(status="running")
        self.repo.update_status(exp.id, status="failed", error="boom")
        updated = self.repo.get(exp.id)
        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.error, "boom")

    def test_update_status_unknown_id_returns_none(self):
        result = self.repo.update_status(99999, status="failed")
        self.assertIsNone(result)

    # --- update_metrics -------------------------------------------------

    def test_update_metrics_sets_slice_columns(self):
        exp = self._seed()
        self.repo.update_metrics(
            exp.id,
            "is_",
            {
                "win_rate_1d": 0.62,
                "avg_return_1d": 0.55,
                "total_signals": 120,
            },
        )
        updated = self.repo.get(exp.id)
        self.assertAlmostEqual(updated.is_win_rate, 0.62)
        self.assertAlmostEqual(updated.is_avg_return_1d, 0.55)
        self.assertEqual(updated.is_total_signals, 120)

    def test_update_metrics_unknown_id_returns_none(self):
        result = self.repo.update_metrics(99999, "is_", {"win_rate_1d": 0.5})
        self.assertIsNone(result)

    # --- update_overfit -------------------------------------------------

    def test_update_overfit_sets_overfit_fields(self):
        exp = self._seed()
        self.repo.update_overfit(exp.id, overfit_score=0.65, overfitting_warning="High overfit")
        updated = self.repo.get(exp.id)
        self.assertEqual(updated.overfit_score, 0.65)
        self.assertEqual(updated.overfitting_warning, "High overfit")

    # --- update_run_ids -------------------------------------------------

    def test_update_run_ids_stores_json(self):
        exp = self._seed()
        run_ids = [10, 20, 30]
        self.repo.update_run_ids(exp.id, run_ids)
        updated = self.repo.get(exp.id)
        self.assertEqual(json.loads(updated.run_ids_json), [10, 20, 30])

    # --- delete --------------------------------------------------------

    def test_delete_removes_experiment(self):
        exp = self._seed()
        eid = exp.id
        self.assertTrue(self.repo.delete(eid))
        self.assertIsNone(self.repo.get(eid))

    def test_delete_unknown_id_returns_false(self):
        self.assertFalse(self.repo.delete(99999))

    # --- get_runs_for_experiment -----------------------------------------

    def test_get_runs_for_experiment_returns_empty_when_no_runs(self):
        exp = self._seed()
        runs = self.repo.get_runs_for_experiment(exp.id)
        self.assertEqual(runs, [])

    def test_get_runs_for_experiment_returns_runs(self):
        from backend.models import BacktestRun

        session = self._Session()
        try:
            run = BacktestRun(
                symbol="AAPL",
                timeframe="1d",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 3, 1),
                signals_requested="RSI_OVERSOLD",
                status="completed",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id
        finally:
            session.close()

        exp = self._seed()
        self.repo.update_run_ids(exp.id, [run_id])
        runs = self.repo.get_runs_for_experiment(exp.id)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].id, run_id)

    # --- get_regime_breakdown -------------------------------------------

    def test_get_regime_breakdown_empty(self):
        from backend.models import BacktestRun

        session = self._Session()
        try:
            run = BacktestRun(
                symbol="AAPL",
                timeframe="1d",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 3, 1),
                signals_requested="RSI_OVERSOLD",
                status="completed",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id

            exp = self.repo.create(
                name="Regime test",
                strategy_version="v1.0",
                parameters_json="{}",
                symbols="AAPL",
                start_date=datetime(2025, 1, 1),
                end_date=datetime(2025, 6, 1),
                is_start=datetime(2025, 1, 1),
                is_end=datetime(2025, 3, 1),
                val_start=datetime(2025, 3, 1),
                val_end=datetime(2025, 4, 1),
                oos_start=datetime(2025, 4, 1),
                oos_end=datetime(2025, 6, 1),
                signals_requested="RSI_OVERSOLD",
                n_splits=3,
                val_pct=0.2,
                oos_pct=0.2,
                status="completed",
            )
            self.repo.update_run_ids(exp.id, [run_id])
        finally:
            session.close()

        breakdown = self.repo.get_regime_breakdown(exp.id)
        self.assertIn("risk_on", breakdown)
        self.assertIn("risk_off", breakdown)
        self.assertIn("neutral", breakdown)
        self.assertIn("unknown", breakdown)


if __name__ == "__main__":
    unittest.main()
