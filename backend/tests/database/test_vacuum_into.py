"""
Tests for vacuum_into() and analyze_db() (Phase 3.3.5).

Validates:
  * vacuum_into() writes a non-empty snapshot file that is a valid SQLite
    DB and contains the same row count as the source
  * vacuum_into() creates parent directories as needed
  * vacuum_into() rejects non-SQLite engines
  * analyze_db() runs without raising on a SQLite engine
  * analyze_db() is a no-op on a non-SQLite engine
"""
import sqlite3
import tempfile
import unittest
from datetime import UTC
from pathlib import Path


class TestVacuumInto(unittest.TestCase):
    """vacuum_into() must produce a valid, non-empty SQLite snapshot."""

    def setUp(self):
        # Use a temp dir so we can clean up after each test.
        self.tmpdir = tempfile.mkdtemp(prefix="marketlens_vacuum_")
        self.snapshot_path = Path(self.tmpdir) / "snap.db"
        # Seed the live DB with a known row so we can verify the snapshot.
        from backend.database.db import SessionLocal
        from backend.models.market_data_sql import BarModel
        self._session = SessionLocal()
        # Use a tiny dummy bar so we have at least one row.
        # We pick a unique symbol per test to avoid duplicates on
        # successive runs of the same test method.
        import time
        self.test_symbol = f"VACUUM_TEST_{int(time.time() * 1000)}"
        from datetime import datetime
        self._session.add(BarModel(
            symbol=self.test_symbol,
            timestamp=datetime.now(UTC),
            timeframe="1m",
            open=1.0, high=2.0, low=0.5, close=1.5, volume=100,
            source="raw",
            provider="test",
            data_status="complete",
        ))
        self._session.commit()

    def tearDown(self):
        # Remove the marker row we added to the LIVE db. This used to be
        # left behind — one VACUUM_TEST_<ms> junk symbol accumulated per
        # run (found 2026-09-10 after ~76 had piled up). The row has to
        # go in the real DB because vacuum_into() snapshots the real
        # file, but it must not outlive the test.
        try:
            from backend.models.market_data_sql import BarModel
            self._session.query(BarModel).filter(
                BarModel.symbol == self.test_symbol
            ).delete()
            self._session.commit()
        except Exception:
            self._session.rollback()
        self._session.close()
        # Best-effort cleanup; tolerate running files.
        try:
            for p in Path(self.tmpdir).glob("*"):
                p.unlink()
            Path(self.tmpdir).rmdir()
        except OSError:
            pass

    def test_snapshot_is_created_and_nonempty(self):
        from backend.database.db import vacuum_into
        result = vacuum_into(self.snapshot_path)
        self.assertEqual(result, self.snapshot_path.resolve())
        self.assertTrue(self.snapshot_path.exists())
        self.assertGreater(self.snapshot_path.stat().st_size, 0)

    def test_snapshot_is_a_valid_sqlite_db(self):
        from backend.database.db import vacuum_into
        vacuum_into(self.snapshot_path)
        # Open the snapshot with the standard library sqlite3 module to
        # confirm it's a real DB (not just any file).
        con = sqlite3.connect(str(self.snapshot_path))
        try:
            row_count = con.execute(
                "SELECT COUNT(*) FROM bars WHERE symbol = ?",
                (self.test_symbol,),
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(row_count, 1)

    def test_snapshot_creates_missing_parent_dirs(self):
        from backend.database.db import vacuum_into
        nested = Path(self.tmpdir) / "a" / "b" / "snap.db"
        self.assertFalse(nested.parent.exists())
        vacuum_into(nested)
        self.assertTrue(nested.exists())

    def test_returns_resolved_path(self):
        from backend.database.db import vacuum_into
        rel = Path(self.tmpdir) / "rel.db"
        result = vacuum_into(rel)
        self.assertTrue(result.is_absolute())
        self.assertEqual(result, rel.resolve())


class TestVacuumIntoRejectsNonSqlite(unittest.TestCase):
    """vacuum_into() must raise on non-SQLite engines."""

    def test_raises_on_postgres_url(self):
        from backend.database import db as dbmod
        from backend.database.db import vacuum_into
        # Temporarily point the module's engine at a Postgres URL by
        # monkey-patching ``engine.url``. We restore it after the test.
        original_engine = dbmod.engine
        fake_eng = type("E", (), {"url": type("U", (), {"__str__": lambda s: "postgresql://x/y"})()})()
        try:
            dbmod.engine = fake_eng
            with self.assertRaises(RuntimeError):
                vacuum_into("/tmp/should_never_exist.db")
        finally:
            dbmod.engine = original_engine


class TestAnalyzeDb(unittest.TestCase):
    """analyze_db() must succeed on SQLite and be a no-op on non-SQLite."""

    def test_runs_without_error_on_sqlite(self):
        from backend.database.db import analyze_db
        # Should not raise.
        analyze_db()

    def test_noop_on_non_sqlite(self):
        from backend.database import db as dbmod
        from backend.database.db import analyze_db
        original_engine = dbmod.engine
        fake_eng = type("E", (), {"url": type("U", (), {"__str__": lambda s: "postgresql://x/y"})()})()
        try:
            dbmod.engine = fake_eng
            # Should return without raising.
            analyze_db()
        finally:
            dbmod.engine = original_engine


if __name__ == "__main__":
    unittest.main()
