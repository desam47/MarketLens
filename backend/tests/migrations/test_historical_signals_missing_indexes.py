"""Regression test for MD-08: the live ``historical_signals`` table was missing
``ix_historical_signals_symbol`` and ``ix_historical_signals_timeframe`` — both in
the model and created by the initial-schema migration, but absent from the live
database, with no migration in the history that drops them. See
``alembic/versions/20261004_historical_signals_missing_indexes.py``.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_INDEXES = ("ix_historical_signals_symbol", "ix_historical_signals_timeframe")


def _alembic(*args: str, db_url: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DEBUG": "false",
        "REDIS_ENABLED": "false",
        "MARKETLENS_DB_OVERRIDE": db_url,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _index_names(db_path: str, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA index_list('{table}')")}


class TestHistoricalSignalsMissingIndexesMigration(unittest.TestCase):
    def setUp(self) -> None:
        # The database guard permits temporary SQLite files inside the project
        # only, and every related SQLite sidecar is removed in tearDown.
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix=".md08_test_", dir=_ROOT)
        os.close(fd)
        os.unlink(self.db_path)
        self.db_url = f"sqlite:///{self.db_path}"

    def tearDown(self) -> None:
        for path in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def test_fresh_build_has_both_indexes(self) -> None:
        """A from-scratch ``alembic upgrade head`` build already has them (the
        initial-schema migration creates them) — this migration must be a no-op
        there, not an error."""
        upgrade = _alembic("upgrade", "head", db_url=self.db_url)
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)

        present = _index_names(self.db_path, "historical_signals")
        self.assertTrue(set(_INDEXES).issubset(present))

    def test_a_database_missing_them_gets_them_added(self) -> None:
        """Simulates the live database's actual drifted state: migrated to the
        revision just before this one, then the two indexes dropped by hand (the
        same state the live database was found in), then upgraded the rest of
        the way."""
        step = _alembic("upgrade", "20261003_signal_identity_unique", db_url=self.db_url)
        self.assertEqual(step.returncode, 0, step.stderr)

        with sqlite3.connect(self.db_path) as conn:
            for name in _INDEXES:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
        self.assertFalse(set(_INDEXES) & _index_names(self.db_path, "historical_signals"))

        final = _alembic("upgrade", "head", db_url=self.db_url)
        self.assertEqual(final.returncode, 0, final.stderr)

        present = _index_names(self.db_path, "historical_signals")
        self.assertTrue(set(_INDEXES).issubset(present))

    def test_downgrade_removes_them_upgrade_restores_them(self) -> None:
        upgrade = _alembic("upgrade", "head", db_url=self.db_url)
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)

        down = _alembic("downgrade", "20261003_signal_identity_unique", db_url=self.db_url)
        self.assertEqual(down.returncode, 0, down.stderr)
        self.assertFalse(set(_INDEXES) & _index_names(self.db_path, "historical_signals"))

        up = _alembic("upgrade", "head", db_url=self.db_url)
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertTrue(set(_INDEXES).issubset(_index_names(self.db_path, "historical_signals")))

    def test_index_columns_are_correct(self) -> None:
        upgrade = _alembic("upgrade", "head", db_url=self.db_url)
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)

        with sqlite3.connect(self.db_path) as conn:
            for name, expected_column in zip(_INDEXES, ("symbol", "timeframe"), strict=True):
                columns = [row[2] for row in conn.execute(f"PRAGMA index_info('{name}')")]
                self.assertEqual(columns, [expected_column])


if __name__ == "__main__":
    unittest.main()
