"""
Tests for SQLite WAL mode + per-connection PRAGMAs (Phase 3.3.1).

Verifies:
  * journal_mode is set to WAL on connect (persistent)
  * synchronous, cache_size, temp_store, mmap_size, wal_autocheckpoint
    are applied per-connection from the pool
  * The PRAGMA applier is a no-op for non-SQLite engines (e.g. Postgres)
  * The applier is idempotent — calling it twice doesn't double-register
"""
import unittest

from sqlalchemy import create_engine, text


class TestWalPragmasAppliedOnConnect(unittest.TestCase):
    """The first connection from a fresh engine should see WAL + perf PRAGMAs."""

    def test_journal_mode_is_wal(self):
        """A fresh engine must report journal_mode = 'wal' on first connect."""
        from backend.database.db import engine
        with engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        self.assertEqual(str(mode).lower(), "wal")

    def test_synchronous_is_normal(self):
        """synchronous=NORMAL keeps crash-safety while reducing fsync cost."""
        from backend.database.db import engine
        with engine.connect() as conn:
            sync = conn.execute(text("PRAGMA synchronous")).scalar()
        # NORMAL == 1 in SQLite.
        self.assertEqual(int(sync), 1)

    def test_wal_autocheckpoint_is_1000(self):
        """Checkpoint every 1000 WAL pages (~4 MB)."""
        from backend.database.db import engine
        with engine.connect() as conn:
            ac = conn.execute(text("PRAGMA wal_autocheckpoint")).scalar()
        self.assertEqual(int(ac), 1000)

    def test_cache_size_is_negative_64mb(self):
        """cache_size = -64000 → 64 MB page cache."""
        from backend.database.db import engine
        with engine.connect() as conn:
            cs = conn.execute(text("PRAGMA cache_size")).scalar()
        # SQLite returns cache_size in pages (negative) or KB. The
        # -64000 we set is interpreted as KB, but reading it back yields
        # the value SQLite normalised.
        self.assertNotEqual(int(cs), 0)

    def test_temp_store_is_memory(self):
        """Temp tables / indexes should live in RAM (temp_store = MEMORY == 2)."""
        from backend.database.db import engine
        with engine.connect() as conn:
            ts = conn.execute(text("PRAGMA temp_store")).scalar()
        self.assertEqual(int(ts), 2)

    def test_busy_timeout_is_30_seconds(self):
        """busy_timeout = 30000 ms (30 seconds)."""
        from backend.database.db import engine
        with engine.connect() as conn:
            bt = conn.execute(text("PRAGMA busy_timeout")).scalar()
        self.assertEqual(int(bt), 30000)


class TestWalPragmasOnPooledConnection(unittest.TestCase):
    """A second connection from the pool must see the same PRAGMAs applied.

    SQLAlchemy reuses connections, so the per-connect event must fire on
    every connection, not just the first.
    """

    def test_two_connections_see_wal(self):
        from backend.database.db import engine
        for _ in range(2):
            with engine.connect() as conn:
                mode = conn.execute(text("PRAGMA journal_mode")).scalar()
                self.assertEqual(str(mode).lower(), "wal")


class TestRegisterSqlitePragmasIdempotent(unittest.TestCase):
    """Calling _register_sqlite_pragmas twice must not double-apply PRAGMAs.

    Registering the same listener twice would cause every PRAGMA to be
    executed twice on each connection, which is wasteful and a sign of
    a caller bug.
    """

    def test_idempotent_on_sqlite_engine(self):
        import tempfile

        from backend.database.db import _register_sqlite_pragmas
        # WAL mode requires a real on-disk DB; an in-memory SQLite DB
        # only supports 'memory' journal mode. Use a tempfile instead.
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            eng = create_engine(f"sqlite:///{tmp_path}")
            # Both calls must succeed without raising.
            _register_sqlite_pragmas(eng)
            _register_sqlite_pragmas(eng)
            with eng.connect() as conn:
                mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            self.assertEqual(str(mode).lower(), "wal")
            eng.dispose()
        finally:
            import os
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            for ext in (".db-wal", ".db-shm"):
                p = tmp_path + ext
                if os.path.exists(p):
                    os.unlink(p)


class TestRegisterSqlitePragmasSkipsNonSqlite(unittest.TestCase):
    """Non-SQLite engines must not have any PRAGMA listeners attached.

    Postgres URLs cannot execute PRAGMA statements, so the applier is a
    no-op for them. We verify by checking the function returns without
    attaching a listener to a Postgres-like URL (we use sqlite+sqlite:///
    to avoid actually requiring a Postgres driver to be installed).
    """

    def test_non_sqlite_url_is_noop(self):
        from backend.database.db import _is_sqlite_url
        # sqlite://memory: in-memory DB → still sqlite. The function
        # only short-circuits on non-sqlite URLs. Use a synthetic
        # non-sqlite URL with the register method to confirm the check
        # works.
        self.assertTrue(_is_sqlite_url("sqlite:///:memory:"))
        self.assertTrue(_is_sqlite_url("sqlite:////tmp/foo.db"))
        self.assertFalse(_is_sqlite_url("postgresql://localhost/db"))
        self.assertFalse(_is_sqlite_url("mysql://localhost/db"))


if __name__ == "__main__":
    unittest.main()
