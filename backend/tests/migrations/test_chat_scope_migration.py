"""
Migration test for 20260910_chat_session_scope.

Runs Alembic against a throwaway SQLite file (via the supported
``MARKETLENS_DB_OVERRIDE`` env var — see backend/config/settings.py),
NOT the real marketlens.db. Verifies the ``scope`` column lands with
every row correctly classified, and that ``downgrade`` runs cleanly and
moves the revision pointer back.

Note: ``op.drop_column`` is a no-op on this repo's SQLite/Alembic setup
(no ``render_as_batch`` in alembic/env.py — ``bars.session`` survives its
own downgrade too), so the downgrade assertion checks the revision
pointer, not the physical column.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PRE = "20260909_chat_sessions"
_POST = "20260910_chat_session_scope"
_ROOT = Path(__file__).resolve().parents[3]


def _alembic(*args: str, db_url: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "MARKETLENS_DB_OVERRIDE": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _sqlite(db_path: str, sql: str) -> str:
    return subprocess.run(
        ["sqlite3", db_path, sql],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


class TestChatSessionScopeMigration(unittest.TestCase):
    def setUp(self):
        # Must live under the project root — backend/database/db.py rejects
        # any SQLite path outside it. ``*.db`` is gitignored.
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix=".mig_test_", dir=str(_ROOT))
        os.close(fd)
        os.unlink(self.db_path)  # let Alembic/SQLite create it fresh
        self.db_url = f"sqlite:///{self.db_path}"

    def tearDown(self):
        for p in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            if os.path.exists(p):
                os.unlink(p)

    def test_upgrade_adds_scope_and_classifies_rows(self):
        # Build the schema up to just before this migration.
        r = _alembic("upgrade", _PRE, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)

        # Seed: 2 plain sessions + 1 opened from an alert trigger.
        _sqlite(
            self.db_path,
            (
                "INSERT INTO chat_sessions (symbol, alert_trigger_id) VALUES "
                "('AAPL', NULL), ('SPY', NULL), ('NVDA', 42);"
            ),
        )

        # Apply the scope migration.
        r = _alembic("upgrade", _POST, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)

        cols = _sqlite(self.db_path, "SELECT name FROM pragma_table_info('chat_sessions');")
        self.assertIn("scope", cols.split("\n"))

        # No NULLs, correct classification.
        self.assertEqual(
            _sqlite(self.db_path, "SELECT count(*) FROM chat_sessions WHERE scope IS NULL;"), "0"
        )
        self.assertEqual(
            _sqlite(
                self.db_path, "SELECT scope FROM chat_sessions WHERE alert_trigger_id IS NOT NULL;"
            ),
            "alert",
        )
        self.assertEqual(
            _sqlite(
                self.db_path,
                "SELECT DISTINCT scope FROM chat_sessions WHERE alert_trigger_id IS NULL;",
            ),
            "symbol",
        )

    def test_downgrade_runs_and_moves_pointer_back(self):
        self.assertEqual(_alembic("upgrade", _POST, db_url=self.db_url).returncode, 0)

        r = _alembic("downgrade", _PRE, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)

        current = _alembic("current", db_url=self.db_url).stdout
        self.assertIn(_PRE, current)
        self.assertNotIn(_POST, current)


if __name__ == "__main__":
    unittest.main()
