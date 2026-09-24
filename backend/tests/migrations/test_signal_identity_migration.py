"""
Migration test for 20261003_signal_identity_unique (HS-12).

Runs Alembic against a throwaway SQLite file under the project root (the only
place backend/database/db.py accepts), never the real marketlens.db. Seeds
duplicate signals the way racing writers left them, then checks the upgrade
keeps the most complete row of each group, leaves unique rows alone, and makes
the database reject a new duplicate.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PRE = "20261002_signal_placeholder_fields"
_POST = "20261003_signal_identity_unique"
_ROOT = Path(__file__).resolve().parents[3]


def _alembic(*args: str, db_url: str) -> subprocess.CompletedProcess:
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
        timeout=180,
    )


class TestSignalIdentityMigration(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix=".mig_test_", dir=str(_ROOT))
        os.close(fd)
        os.unlink(self.db_path)  # let Alembic/SQLite create it fresh
        self.db_url = f"sqlite:///{self.db_path}"

    def tearDown(self):
        for p in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            if os.path.exists(p):
                os.unlink(p)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _seed(self) -> None:
        rows = [
            # id, symbol, timeframe, timestamp, return_5b, return_20b, mfe, market_regime
            (1, "AAPL", "5m", "2026-09-15 10:00:00.000000", 1.0, None, None, None),  # partial copy
            (2, "AAPL", "5m", "2026-09-15 10:00:00.000000", 1.0, 2.0, 3.0, None),  # complete copy: kept
            (3, "AAPL", "1m", "2026-09-15 10:00:00.000000", 1.0, 2.0, 3.0, None),
            (4, "AAPL", "1m", "2026-09-15 10:00:00.000000", 1.0, 2.0, 3.0, "risk_on"),  # has a regime: kept
            (5, "MSFT", "1m", "2026-09-15 10:00:00.000000", None, None, None, None),  # oldest id: kept
            (6, "MSFT", "1m", "2026-09-15 10:00:00.000000", None, None, None, None),
            (7, "MSFT", "1m", "2026-09-15 10:00:00.000000", None, None, None, None),
            (8, "MSFT", "1m", "2026-09-15 10:01:00.000000", None, None, None, None),  # unique
        ]
        conn = self._connect()
        try:
            conn.executemany(
                "INSERT INTO historical_signals (id, symbol, timeframe, timestamp, return_5b, return_10b, "
                "return_20b, mfe, mae, market_regime) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(i, s, tf, ts, r5, r5, r20, mfe, mfe, regime) for i, s, tf, ts, r5, r20, mfe, regime in rows],
            )
            # The non-unique composite some databases carry outside the migration history.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_historical_signals_symbol_timeframe_timestamp "
                "ON historical_signals (symbol, timeframe, timestamp)"
            )
            conn.commit()
        finally:
            conn.close()

    def _indexes(self) -> set[str]:
        conn = self._connect()
        try:
            return {
                name for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'historical_signals'"
                )
            }
        finally:
            conn.close()

    def test_upgrade_keeps_the_most_complete_row_and_enforces_uniqueness(self):
        r = _alembic("upgrade", _PRE, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)
        self._seed()

        r = _alembic("upgrade", _POST, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Removing 4 duplicate historical signals", r.stderr)

        conn = self._connect()
        try:
            kept = [row_id for (row_id,) in conn.execute("SELECT id FROM historical_signals ORDER BY id")]
            self.assertEqual(kept, [2, 4, 5, 8])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO historical_signals (symbol, timeframe, timestamp) "
                    "VALUES ('AAPL', '5m', '2026-09-15 10:00:00.000000')"
                )
        finally:
            conn.close()
        indexes = self._indexes()
        self.assertIn("uq_historical_signals_symbol_timeframe_timestamp", indexes)
        self.assertNotIn("ix_historical_signals_symbol_timeframe_timestamp", indexes)

        r = _alembic("downgrade", _PRE, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("uq_historical_signals_symbol_timeframe_timestamp", self._indexes())


if __name__ == "__main__":
    unittest.main()
