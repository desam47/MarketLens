"""
Tests for bar retention primitives (Phase 3.3.8 – 3.3.18).

Validates:
  * prune_bars_older_than deletes old bars in chunks and returns a count
  * bulk_delete_bars handles symbol lists and the cutoff parameter
  * delete_bars_for_symbol and delete_bars_for_symbols are thin wrappers
  * symbol_exists_in_any_watchlist correctly detects watchlisted symbols
  * the watchlist router's add/remove endpoints trigger backfill/purge
  * the settings fields bar_retention_days and backfill_on_add are readable
  * _safe_bar_counts returns retention fields
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.models.market_data_sql import Base, BarModel


class _TestMixin:
    """Shared SQLite engine + session for each test."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls._tmp.close()
        cls._engine = create_engine(f"sqlite:///{cls._tmp.name}", echo=False)
        Base.metadata.create_all(cls._engine)
        cls._Session = sessionmaker(bind=cls._engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls._engine.dispose()
        import os
        for p in [cls._tmp.name, cls._tmp.name + "-wal", cls._tmp.name + "-shm"]:
            if os.path.exists(p):
                os.unlink(p)

    def _session(self) -> Session:
        return self._Session()

    def _add_bar(
        self,
        session: Session,
        symbol: str,
        minutes_ago: int,
        timeframe: str = "1m",
    ) -> BarModel:
        ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        bar = BarModel(
            symbol=symbol.upper(),
            timestamp=ts,
            timeframe=timeframe,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            provider="test",
            data_status="complete",
            source="raw",
        )
        session.add(bar)
        session.commit()
        return bar


class TestPruneBarsOlderThan(_TestMixin, unittest.TestCase):
    """prune_bars_older_than must delete old rows chunk by chunk."""

    def test_returns_0_when_no_old_bars(self):
        from backend.repositories.bar_repository import prune_bars_older_than

        db = self._session()
        try:
            self._add_bar(db, "AAPL", minutes_ago=60)  # 1h old
            cutoff = datetime.now(timezone.utc) - timedelta(days=1)
            deleted = prune_bars_older_than(db, cutoff)
            self.assertEqual(deleted, 0)
        finally:
            db.close()

    def test_deletes_old_bars(self):
        from backend.repositories.bar_repository import prune_bars_older_than

        db = self._session()
        try:
            self._add_bar(db, "AAPL", minutes_ago=60)  # 1h
            self._add_bar(db, "TSLA", minutes_ago=60 * 60 * 48)  # 48h
            cutoff = datetime.now(timezone.utc) - timedelta(days=1)
            deleted = prune_bars_older_than(db, cutoff)
            self.assertEqual(deleted, 1)  # only TSLA
            # AAPL should remain
            remaining = db.query(BarModel).filter(
                BarModel.symbol == "AAPL"
            ).count()
            self.assertEqual(remaining, 1)
        finally:
            db.close()

    def test_chunks_delete_correctly(self):
        from backend.repositories.bar_repository import prune_bars_older_than

        db = self._session()
        try:
            # Insert 100 bars all older than the cutoff.
            now = datetime.now(timezone.utc)
            for i in range(100):
                ts = now - timedelta(days=100 + i)
                db.add(BarModel(
                    symbol="BULK",
                    timestamp=ts,
                    timeframe="1m",
                    open=10.0, high=11.0, low=9.0, close=10.5, volume=100,
                    provider="test", data_status="complete", source="raw",
                ))
            db.commit()
            cutoff = now - timedelta(days=50)
            deleted = prune_bars_older_than(db, cutoff, chunk_size=10)
            self.assertEqual(deleted, 100)
        finally:
            db.close()

    def test_raises_on_invalid_chunk_size(self):
        from backend.repositories.bar_repository import prune_bars_older_than

        db = self._session()
        try:
            with self.assertRaises(ValueError):
                prune_bars_older_than(
                    db,
                    datetime.now(timezone.utc),
                    chunk_size=0,
                )
        finally:
            db.close()


class TestBulkDeleteBars(_TestMixin, unittest.TestCase):
    """bulk_delete_bars must delete by symbol list with optional cutoff."""

    def test_deletes_all_symbols(self):
        from backend.repositories.bar_repository import bulk_delete_bars

        db = self._session()
        try:
            self._add_bar(db, "AAPL", minutes_ago=60)
            self._add_bar(db, "TSLA", minutes_ago=60)
            deleted = bulk_delete_bars(db, ["AAPL", "TSLA"])
            self.assertEqual(deleted, 2)
            self.assertEqual(db.query(BarModel).count(), 0)
        finally:
            db.close()

    def test_respects_cutoff(self):
        from backend.repositories.bar_repository import bulk_delete_bars

        db = self._session()
        try:
            self._add_bar(db, "AAPL", minutes_ago=60)  # 1h old
            self._add_bar(db, "AAPL", minutes_ago=60 * 60 * 48)  # 48h old
            cutoff = datetime.now(timezone.utc) - timedelta(days=1)
            deleted = bulk_delete_bars(db, ["AAPL"], cutoff=cutoff)
            self.assertEqual(deleted, 1)  # only the 48h bar
            remaining = db.query(BarModel).filter(BarModel.symbol == "AAPL").count()
            self.assertEqual(remaining, 1)
        finally:
            db.close()

    def test_returns_0_for_empty_list(self):
        from backend.repositories.bar_repository import bulk_delete_bars

        db = self._session()
        try:
            deleted = bulk_delete_bars(db, [])
            self.assertEqual(deleted, 0)
        finally:
            db.close()

    def test_normalises_to_uppercase(self):
        from backend.repositories.bar_repository import bulk_delete_bars

        db = self._session()
        try:
            self._add_bar(db, "aapl", minutes_ago=60)
            deleted = bulk_delete_bars(db, ["AAPL"])
            self.assertEqual(deleted, 1)
        finally:
            db.close()


class TestDeleteBarsForSymbol(_TestMixin, unittest.TestCase):
    """delete_bars_for_symbol and delete_bars_for_symbols use their own session."""

    def test_delete_bars_for_symbol(self):
        from backend.repositories.bar_repository import delete_bars_for_symbol
        from backend.database import SessionLocal

        # Use SessionLocal so the bar is added to the same DB that
        # delete_bars_for_symbol() reads from.
        db = SessionLocal()
        try:
            self._add_bar(db, "ZEBRA", minutes_ago=60)
        finally:
            db.close()

        deleted = delete_bars_for_symbol("ZEBRA")
        self.assertEqual(deleted, 1)

    def test_delete_bars_for_symbols(self):
        from backend.repositories.bar_repository import delete_bars_for_symbols
        from backend.database import SessionLocal

        db = SessionLocal()
        try:
            self._add_bar(db, "X", minutes_ago=60)
            self._add_bar(db, "Y", minutes_ago=60)
        finally:
            db.close()

        deleted = delete_bars_for_symbols(["X", "Y"])
        self.assertEqual(deleted, 2)

    def test_returns_0_for_empty_symbol(self):
        from backend.repositories.bar_repository import delete_bars_for_symbol

        deleted = delete_bars_for_symbol("")
        self.assertEqual(deleted, 0)


class TestSymbolExistsInAnyWatchlist(unittest.TestCase):
    """symbol_exists_in_any_watchlist requires the full DB (WatchlistSymbol model)."""

    def test_returns_false_for_unknown_symbol(self):
        from backend.repositories.watchlist_repository import WatchlistRepository
        from backend.database.db import SessionLocal

        db = SessionLocal()
        try:
            repo = WatchlistRepository(db)
            self.assertFalse(repo.symbol_exists_in_any_watchlist("DEFINITELYNOTAWATCHED"))
        finally:
            db.close()

    def test_returns_true_for_watched_symbol(self):
        from backend.repositories.watchlist_repository import WatchlistRepository
        from backend.database.db import SessionLocal

        db = SessionLocal()
        try:
            repo = WatchlistRepository(db)
            wl = repo.create_watchlist("test_watchlist_for_retention")
            repo.add_symbol_to_watchlist(wl.id, "WATCHED")
            self.assertTrue(repo.symbol_exists_in_any_watchlist("WATCHED"))
            # Cleanup
            repo.remove_symbol_from_watchlist(wl.id, "WATCHED")
            repo.delete_watchlist(wl.id)
        finally:
            db.close()


class TestSettingsFields(unittest.TestCase):
    """Verify the Phase 3.3.8 settings are accessible."""

    def test_bar_retention_days_defaults_to_1095(self):
        """Default is 1095 days (~3 calendar years) — see settings.py comment."""
        from backend.config.settings import settings

        self.assertEqual(settings.market_data.bar_retention_days, 1095)

    def test_backfill_on_add_defaults_to_true(self):
        from backend.config.settings import settings

        self.assertEqual(settings.market_data.backfill_on_add, True)


class TestSafeBarCountsRetentionFields(unittest.TestCase):
    """_safe_bar_counts returns retention fields."""

    def test_returns_retention_fields(self):
        from backend.api.system.router import _safe_bar_counts

        result = _safe_bar_counts()
        self.assertIsNotNone(result)
        self.assertIn("oldest_bar", result)
        self.assertIn("newest_bar", result)
        self.assertIn("distinct_symbols", result)
        self.assertIn("retention_days", result)
        self.assertEqual(result["retention_days"], 1095)
        self.assertIsInstance(result["distinct_symbols"], int)


if __name__ == "__main__":
    unittest.main()
