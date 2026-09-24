"""
Migration test for 20260930_chat_id_autoincrement (BF-10).

Runs Alembic against a throwaway SQLite file under the project root (the
only place backend/database/db.py accepts), never the real marketlens.db.
Seeds the chat tables the way a cleared chat leaves them, with feedback,
a regression fixture and a notebook item that point at deleted message
ids, then checks the rebuild keeps every row, drops only orphan feedback,
and makes new ids start above every id still referenced.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PRE = "20260929_chat_notebooks_fixtures"
_POST = "20260930_chat_id_autoincrement"
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


class TestChatIdAutoincrementMigration(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix=".mig_test_", dir=str(_ROOT))
        os.close(fd)
        os.unlink(self.db_path)  # let Alembic/SQLite create it fresh
        self.db_url = f"sqlite:///{self.db_path}"

    def tearDown(self):
        for p in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            if os.path.exists(p):
                os.unlink(p)

    def _query(self, sql: str) -> list[tuple]:
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def _execute(self, script: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(script)
            conn.commit()
        finally:
            conn.close()

    def _autoincrement_tables(self) -> set[str]:
        rows = self._query(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('chat_sessions', 'chat_messages') AND sql LIKE '%AUTOINCREMENT%'"
        )
        return {name for (name,) in rows}

    def test_upgrade_keeps_rows_drops_orphan_feedback_and_never_reuses_ids(self):
        r = _alembic("upgrade", _PRE, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)
        # A cleared chat: messages above 41 were deleted, but feedback (55),
        # a fixture (60) and a notebook item (72) still hold their ids.
        self._execute(
            """
            INSERT INTO chat_sessions (id, symbol, scope, planner_state) VALUES (7, '*', 'universal', '{"k": 1}');
            INSERT INTO chat_messages (id, session_id, role, content) VALUES
                (40, 7, 'user', 'hi'), (41, 7, 'assistant', 'hello');
            INSERT INTO chat_feedback (message_id, rating) VALUES (41, 'correct'), (55, 'incorrect');
            INSERT INTO chat_regression_fixtures (message_id, prompt, response, rating)
                VALUES (60, 'p', 'r', 'incorrect');
            INSERT INTO research_notebooks (id, client_key, name) VALUES (1, 'client-123456', 'nb');
            INSERT INTO research_notebook_items (notebook_id, message_id, question, answer)
                VALUES (1, 72, 'q', 'a');
            """
        )

        r = _alembic("upgrade", _POST, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)

        self.assertEqual(self._autoincrement_tables(), {"chat_sessions", "chat_messages"})
        self.assertEqual(self._query("SELECT id, planner_state FROM chat_sessions"), [(7, '{"k": 1}')])
        self.assertEqual(
            self._query("SELECT id, session_id, role, content FROM chat_messages ORDER BY id"),
            [(40, 7, "user", "hi"), (41, 7, "assistant", "hello")],
        )
        self.assertEqual(self._query("SELECT message_id FROM chat_feedback"), [(41,)])
        self.assertEqual(self._query("SELECT message_id FROM chat_regression_fixtures"), [(60,)])
        self.assertEqual(self._query("SELECT message_id FROM research_notebook_items"), [(72,)])
        indexes = {
            name
            for (name,) in self._query(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name IN ('chat_sessions', 'chat_messages')"
            )
        }
        self.assertTrue(
            {"ix_chat_sessions_scope", "ix_chat_sessions_symbol", "ix_chat_messages_session_id", "ix_chat_messages_created_at"}
            <= indexes
        )

        self._execute(
            "INSERT INTO chat_sessions (symbol, scope) VALUES ('*', 'universal');"
            "INSERT INTO chat_messages (session_id, role, content) VALUES (7, 'user', 'next');"
        )
        self.assertEqual(self._query("SELECT MAX(id) FROM chat_sessions"), [(8,)])
        # Above the notebook item's 72, not just above the surviving 41.
        self.assertEqual(self._query("SELECT MAX(id) FROM chat_messages"), [(73,)])

    def test_downgrade_removes_autoincrement_and_keeps_rows(self):
        r = _alembic("upgrade", _POST, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)
        self._execute(
            "INSERT INTO chat_sessions (id, symbol, scope) VALUES (3, 'AAPL', 'symbol');"
            "INSERT INTO chat_messages (id, session_id, role, content) VALUES (9, 3, 'user', 'hi');"
        )

        r = _alembic("downgrade", _PRE, db_url=self.db_url)
        self.assertEqual(r.returncode, 0, r.stderr)

        self.assertEqual(self._autoincrement_tables(), set())
        self.assertEqual(self._query("SELECT id, session_id FROM chat_messages"), [(9, 3)])
        self.assertEqual(self._query("SELECT version_num FROM alembic_version"), [(_PRE,)])


if __name__ == "__main__":
    unittest.main()
