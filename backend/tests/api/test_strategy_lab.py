"""
Integration tests for ``/api/strategy-lab``.

Uses FastAPI's ``TestClient`` to hit the live router without
requiring a running server. Database operations are performed against
a real (but isolated) in-memory SQLite database so the repository
queries are exercised end-to-end.
"""
import contextlib
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

os.environ.setdefault("MARKETLENS_DB_OVERRIDE", "sqlite:////tmp/test_strategy_lab.db")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastapi.testclient import TestClient

from backend.api.main import app
import backend.database as _database_pkg
import backend.database.db as _database_impl
from backend.database import Base
from backend.models import Experiment

# ---------------------------------------------------------------------------
# Isolate this module onto its own temporary database.
# ---------------------------------------------------------------------------
# The MARKETLENS_DB_OVERRIDE above only takes effect if this module is the
# first thing to import backend.config.settings. During a full-suite run it
# is not: other test modules import it first, the settings singleton is built
# with the real DATABASE_URL, and backend.database.engine ends up bound to the
# developer's live marketlens.db. A drop_all() in setUp then wiped every
# table, which is exactly what happened on 2026-09-02 (all bars plus the
# user's watchlist).
#
# So do not depend on import order. Build an explicit temp engine and rebind
# the module attributes. The strategy-lab router resolves SessionLocal lazily
# (``from backend.database import SessionLocal`` inside the handler), so
# rebinding the attribute is picked up on every request.
#
# CRITICAL: the actual rebinding (and the ingestion-service patch below) must
# happen in ``setUpModule()``, NOT at bare module level. pytest collects
# (imports) every test module for the whole run up front, before running ANY
# test — so module-level code here would execute during collection and stay
# in effect for the rest of collection too. Plenty of other test files do
# ``from backend.database import SessionLocal`` at THEIR OWN module level
# (e.g. test_purge_service.py, test_engine_seeding.py) — if their import
# happens during that collection window while this module's rebind was
# active, they'd permanently capture the temp SessionLocal (later deleted by
# tearDownModule) with no way for tearDownModule to fix it, since it only
# restores the *module attribute*, not names other files already imported
# from it. ``setUpModule()``/``tearDownModule()`` are real unittest run-phase
# hooks — they fire immediately before/after this module's tests actually
# execute, well after collection has finished — so confining the rebind to
# that window means no other file's collection-time import can observe it.
# (2026-09-08: this was corrupting purge_service, bar_retention, wal_pragmas,
# vacuum_into, backup_status, signals_api, engine_seeding, scanner_api, and
# ai_router across full-suite runs.)
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()

engine = create_engine(
    f"sqlite:///{_TMP_DB.name}",
    connect_args={"check_same_thread": False},
)
_TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

import backend.repositories.experiment_repository as _exp_repo

# This module is the only one in the suite that triggers FastAPI's lifespan
# startup (``with TestClient(app)``; a bare ``TestClient(app)`` does not).
# The lifespan is expensive — alembic subprocess, Redis SCAN+DELETE,
# provider warmups that make real HTTP calls — so it now runs ONCE for the
# whole module via the shared client below, not once per test. It still
# calls ``ingestion_service.start()``, which spawns a REAL daemon thread
# that hits real network providers and writes to the real ``marketlens.db``
# on a 30-60s cadence — and, being a process-wide singleton with its own
# captured SessionLocal, keeps doing so for the rest of the pytest process
# once started, regardless of this module's DB isolation. Patch
# ``.start()`` to a no-op for the same setUpModule/tearDownModule-scoped
# reason as above.
from unittest.mock import patch as _patch
from backend.market_data.services.ingestion_service import ingestion_service as _ingestion_service

_orig_engine = None
_orig_session_local = None
_orig_exp_repo_session_local = None
_ingestion_start_patch = None


def setUpModule():
    global _orig_engine, _orig_session_local, _orig_exp_repo_session_local
    global _ingestion_start_patch

    _orig_engine = _database_pkg.engine
    _orig_session_local = _database_pkg.SessionLocal
    for _mod in (_database_pkg, _database_impl):
        _mod.engine = engine
        _mod.SessionLocal = _TestSessionLocal

    # ExperimentRepository captures SessionLocal at import time (line 11 of
    # experiment_repository.py: ``from backend.database import
    # SessionLocal``). Rebind there too so any repository instances the
    # router spawns use the isolated test session rather than the
    # production one.
    _orig_exp_repo_session_local = _exp_repo.SessionLocal
    _exp_repo.SessionLocal = _TestSessionLocal

    _ingestion_start_patch = _patch.object(_ingestion_service, "start", lambda: None)
    _ingestion_start_patch.start()

    # Build a fresh schema on the temp DB before any tests run.
    Base.metadata.create_all(bind=engine)

    # Start the lifespan exactly once for this module (see the note above
    # ``_client()``). Do it after the DB rebinds so anything the startup
    # touches goes through the isolated temp DB, just like the old
    # per-test lifespan did.
    global _shared_client, _client_stack
    _client_stack = contextlib.ExitStack()
    _shared_client = _client_stack.enter_context(TestClient(app))


def tearDownModule():
    """Stop the shared client, drop the temp DB, restore the real engine."""
    # Shut the lifespan down BEFORE restoring the DB rebinds / stopping the
    # ingestion patch — mirrors the old ordering where shutdown happened
    # inside each test with the isolation still in effect.
    if _client_stack is not None:
        _client_stack.close()
    engine.dispose()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    for _mod in (_database_pkg, _database_impl):
        _mod.engine = _orig_engine
        _mod.SessionLocal = _orig_session_local
    _exp_repo.SessionLocal = _orig_exp_repo_session_local
    _ingestion_start_patch.stop()


# One lifespan startup for the whole module instead of one per test.
#
# Each ``with _client() as client:`` re-ran the full FastAPI lifespan
# — alembic subprocess, Redis key flush, alerts/digest/tracing startup,
# trend and market-context warmups that make real provider HTTP calls —
# measured at ~12-18s per entry (11 entries ≈ 135s of suite time, plus
# real-network leakage into the logs). Nothing in these tests depends on
# the lifespan re-running, so setUpModule enters it once and every test
# shares the same client.
_shared_client = None
_client_stack = None


@contextlib.contextmanager
def _client():
    """Yield the module-shared TestClient without re-running the lifespan."""
    assert _shared_client is not None, "setUpModule must create the shared client"
    yield _shared_client


class _DBSession:
    """Provide an isolated DB connection for direct assertions."""

    def __init__(self):
        from backend.database import SessionLocal
        self._session = SessionLocal()

    def close(self):
        self._session.close()

    def add_experiment(self, **kwargs) -> Experiment:
        exp = Experiment(**kwargs)
        self._session.add(exp)
        self._session.commit()
        self._session.refresh(exp)
        return exp

    def get_experiment(self, eid: int) -> Experiment | None:
        return self._session.query(Experiment).filter(Experiment.id == eid).first()

    def list_experiments(self) -> list[Experiment]:
        return self._session.query(Experiment).order_by(Experiment.created_at.desc()).all()


class TestStrategyLabEndpoints(unittest.TestCase):
    """Exercise each ``/api/strategy-lab`` endpoint."""

    def setUp(self):
        # Wipe and re-create tables so each test is isolated.
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.db = _DBSession()

    def tearDown(self):
        self.db.close()

    def test_list_experiments_empty(self):
        with _client() as client:
            resp = client.get("/api/strategy-lab/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_create_experiment_persists_row(self):
        with _client() as client:
            resp = client.post(
                "/api/strategy-lab/",
                json={
                    "name": "RSI(7) smoke test",
                    "symbols": ["AAPL"],
                    "start_date": "2025-01-01T00:00:00",
                    "end_date": "2025-03-01T00:00:00",
                    "signals": ["RSI_OVERSOLD"],
                    "n_splits": 3,
                    "val_pct": 0.2,
                    "oos_pct": 0.2,
                },
            )
        # If the engine fails (no data in test DB) the status will be
        # "failed" but the experiment row should still exist.
        self.assertIn(resp.status_code, (200, 202, 500))
        data = resp.json()
        self.assertIn("id", data)
        self.assertEqual(data["name"], "RSI(7) smoke test")
        self.assertEqual(data["symbols"], "AAPL")

    def test_get_experiment_not_found(self):
        with _client() as client:
            resp = client.get("/api/strategy-lab/9999")
        self.assertEqual(resp.status_code, 404)

    def test_get_experiment_returns_existing(self):
        # Seed an experiment directly in the DB.
        exp = self.db.add_experiment(
            name="Seed experiment",
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
        self.db._session.commit()

        with _client() as client:
            resp = client.get(f"/api/strategy-lab/{exp.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["name"], "Seed experiment")
        self.assertEqual(data["status"], "completed")

    def test_delete_experiment_not_found(self):
        with _client() as client:
            resp = client.delete("/api/strategy-lab/9999")
        self.assertEqual(resp.status_code, 404)

    def test_delete_experiment_removes_row(self):
        exp = self.db.add_experiment(
            name="To be deleted",
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
        self.db._session.commit()
        eid = exp.id

        with _client() as client:
            resp = client.delete(f"/api/strategy-lab/{eid}")
        self.assertEqual(resp.status_code, 204)
        self.assertIsNone(self.db.get_experiment(eid))

    def test_compare_experiments_missing_one_returns_404(self):
        with _client() as client:
            resp = client.post(
                "/api/strategy-lab/compare",
                json={"experiment_ids": [88888, 99999]},
            )
        self.assertEqual(resp.status_code, 404)
        # First non-existent ID is reported.
        self.assertIn("88888", resp.json()["detail"])

    def test_get_experiment_runs_not_found(self):
        with _client() as client:
            resp = client.get("/api/strategy-lab/9999/runs")
        self.assertEqual(resp.status_code, 404)


class TestExperimentParametersRequest(unittest.TestCase):
    """Validate ``ExperimentParametersRequest`` merging logic."""

    def test_merges_defaults_with_overrides(self):
        from backend.api.strategy_lab.router import ExperimentParametersRequest

        req = ExperimentParametersRequest(rsi_period=7, macd_fast=8)
        params = req.to_experiment_parameters()
        # Overridden fields
        self.assertEqual(params.rsi_period, 7)
        self.assertEqual(params.macd_fast, 8)
        # Unset fields use defaults
        self.assertEqual(params.rsi_overbought, 70.0)

    def test_empty_request_returns_full_defaults(self):
        from backend.api.strategy_lab.router import ExperimentParametersRequest

        req = ExperimentParametersRequest()
        params = req.to_experiment_parameters()
        self.assertEqual(params.rsi_period, 14)
        self.assertEqual(params.macd_slow, 26)


class TestExperimentCreateValidation(unittest.TestCase):
    """Validate request body constraints."""

    def test_end_date_before_start_date_rejected(self):
        with _client() as client:
            resp = client.post(
                "/api/strategy-lab/",
                json={
                    "name": "Bad dates",
                    "symbols": ["AAPL"],
                    "start_date": "2025-06-01T00:00:00",
                    "end_date": "2025-01-01T00:00:00",
                },
            )
        self.assertEqual(resp.status_code, 422)

    def test_empty_symbols_rejected(self):
        with _client() as client:
            resp = client.post(
                "/api/strategy-lab/",
                json={
                    "name": "No symbols",
                    "symbols": [],
                    "start_date": "2025-01-01T00:00:00",
                    "end_date": "2025-06-01T00:00:00",
                },
            )
        self.assertEqual(resp.status_code, 422)

    def test_symbols_uppercased(self):
        with _client() as client:
            resp = client.post(
                "/api/strategy-lab/",
                json={
                    "name": "Lower symbols",
                    "symbols": ["aapl", "  msft  "],
                    "start_date": "2025-01-01T00:00:00",
                    "end_date": "2025-03-01T00:00:00",
                },
            )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["symbols"], "AAPL,MSFT")


if __name__ == "__main__":
    unittest.main()
