"""Regression tests for feature tables that must come from Alembic.

``Base.metadata.create_all()`` is convenient for local bootstrap, but it must
not be the only path that creates application tables. These checks start with
an empty SQLite database, apply only Alembic migrations, and inspect the
resulting schema directly.
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

_REQUIRED_TABLE_INDEXES = {
    "ai_analysis_jobs": {
        "ix_ai_analysis_jobs_job_id",
        "ix_ai_analysis_jobs_status",
        "ix_ai_analysis_jobs_symbol_created",
    },
    "ai_templates": {"ix_ai_templates_id"},
    "custom_indicators": {"ix_custom_indicators_id", "ix_custom_indicators_slug"},
    "drawing_tools": {"ix_drawing_tools_id", "ix_drawing_tools_symbol"},
}

_REQUIRED_EXISTING_TABLE_INDEXES = {
    "ai_digests": {"ix_ai_digests_session"},
    "alert_deliveries": {"ix_alert_deliveries_id"},
    "chat_sessions": {"ix_chat_sessions_scope"},
}


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


class TestSchemaCompletenessMigration(unittest.TestCase):
    def setUp(self) -> None:
        # The database guard permits temporary SQLite files inside the project
        # only, and every related SQLite sidecar is removed in tearDown.
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix=".schema_test_", dir=_ROOT)
        os.close(fd)
        os.unlink(self.db_path)
        self.db_url = f"sqlite:///{self.db_path}"

    def tearDown(self) -> None:
        for path in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            if os.path.exists(path):
                os.unlink(path)

    def test_fresh_migrations_create_feature_tables_and_indexes(self) -> None:
        upgrade = _alembic("upgrade", "head", db_url=self.db_url)
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)

        check = _alembic("check", db_url=self.db_url)
        self.assertEqual(check.returncode, 0, check.stderr)

        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            expected_indexes = {
                **_REQUIRED_TABLE_INDEXES,
                **_REQUIRED_EXISTING_TABLE_INDEXES,
            }
            for table_name, indexes in expected_indexes.items():
                self.assertIn(table_name, tables)
                actual_indexes = {
                    row[1] for row in connection.execute(f"PRAGMA index_list('{table_name}')")
                }
                self.assertTrue(
                    indexes.issubset(actual_indexes),
                    f"{table_name} is missing indexes: {indexes - actual_indexes}",
                )

            outcome_indexes = {
                row[1] for row in connection.execute("PRAGMA index_list('ai_trade_plan_outcomes')")
            }
            self.assertNotIn("ix_ai_trade_plan_outcomes_id", outcome_indexes)


if __name__ == "__main__":
    unittest.main()
