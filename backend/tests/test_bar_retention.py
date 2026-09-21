"""
Tests for bar retention primitives (Phase 3.3.8 – 3.3.18).

Validates:
  * prune_bars_older_than deletes old bars in chunks and returns a count
  * bulk_delete_bars handles symbol lists and the cutoff parameter
  * delete_bars_for_symbol and delete_bars_for_symbols are thin wrappers
  * symbol_exists_in_any_watchlist correctly detects watchlisted symbols
  * the watchlist router's add/remove endpoints trigger backfill/purge
  * the settings field backfill_on_add is readable (bar_retention_days
    was removed 2026-09-09 — see TestSettingsFields)
  * _safe_bar_counts returns retention fields
"""
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.models.market_data_sql import BarModel, Base


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
        ts = datetime.now(UTC) - timedelta(minutes=minutes_ago)
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
            cutoff = datetime.now(UTC) - timedelta(days=1)
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
            cutoff = datetime.now(UTC) - timedelta(days=1)
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
            now = datetime.now(UTC)
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
                    datetime.now(UTC),
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
            cutoff = datetime.now(UTC) - timedelta(days=1)
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
        from backend.database import SessionLocal
        from backend.repositories.bar_repository import delete_bars_for_symbol

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
        from backend.database import SessionLocal
        from backend.repositories.bar_repository import delete_bars_for_symbols

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
        from backend.database.db import SessionLocal
        from backend.repositories.watchlist_repository import WatchlistRepository

        db = SessionLocal()
        try:
            repo = WatchlistRepository(db)
            self.assertFalse(repo.symbol_exists_in_any_watchlist("DEFINITELYNOTAWATCHED"))
        finally:
            db.close()

    def test_returns_true_for_watched_symbol(self):
        from backend.database.db import SessionLocal
        from backend.repositories.watchlist_repository import WatchlistRepository

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


class TestPruneBarsOlderThanTimeframeFilter(_TestMixin, unittest.TestCase):
    """prune_bars_older_than's timeframe param scopes deletion to one
    timeframe, leaving others (even if also old) untouched.

    Regression coverage for per-timeframe retention (2026-09-09).
    """

    def test_timeframe_filter_only_deletes_matching_rows(self):
        from backend.repositories.bar_repository import prune_bars_older_than

        db = self._session()
        try:
            self._add_bar(db, "AAPL", minutes_ago=60 * 60 * 48, timeframe="1m")  # 48h
            self._add_bar(db, "AAPL", minutes_ago=60 * 60 * 48, timeframe="1h")  # 48h
            cutoff = datetime.now(UTC) - timedelta(days=1)
            deleted = prune_bars_older_than(db, cutoff, timeframe="1m")
            self.assertEqual(deleted, 1)
            # The 1h row is old too, but wasn't targeted — must remain.
            remaining = db.query(BarModel).filter(BarModel.timeframe == "1h").count()
            self.assertEqual(remaining, 1)
        finally:
            db.close()

    def test_no_timeframe_filter_deletes_across_all(self):
        """Backward compat: timeframe=None (default) behaves exactly as
        before per-timeframe retention existed — one cutoff, every
        timeframe."""
        from backend.repositories.bar_repository import prune_bars_older_than

        db = self._session()
        try:
            self._add_bar(db, "AAPL", minutes_ago=60 * 60 * 48, timeframe="1m")
            self._add_bar(db, "AAPL", minutes_ago=60 * 60 * 48, timeframe="1h")
            cutoff = datetime.now(UTC) - timedelta(days=1)
            deleted = prune_bars_older_than(db, cutoff)
            self.assertEqual(deleted, 2)
        finally:
            db.close()


class TestPruneBarsByRetention(_TestMixin, unittest.TestCase):
    """prune_bars_by_retention applies each timeframe's own configured
    window — a 1m bar just past its (short) window is pruned while a 1d
    bar at the exact same age is not."""

    def test_short_window_timeframe_pruned_long_window_timeframe_kept(self):
        from backend.repositories.bar_repository import prune_bars_by_retention

        db = self._session()
        try:
            # 20 days old: past the 16-day default for 1m, well within
            # the 1096-day default for 1d.
            self._add_bar(db, "AAPL", minutes_ago=60 * 24 * 20, timeframe="1m")
            self._add_bar(db, "AAPL", minutes_ago=60 * 24 * 20, timeframe="1d")
            deleted_by_tf = prune_bars_by_retention(db)
            self.assertEqual(deleted_by_tf.get("1m"), 1)
            self.assertNotIn("1d", deleted_by_tf)  # nothing pruned -> omitted
            remaining_1d = db.query(BarModel).filter(
                BarModel.symbol == "AAPL", BarModel.timeframe == "1d",
            ).count()
            self.assertEqual(remaining_1d, 1)
        finally:
            db.close()

    def test_returns_empty_dict_when_nothing_to_prune(self):
        from backend.repositories.bar_repository import prune_bars_by_retention

        db = self._session()
        try:
            self._add_bar(db, "AAPL", minutes_ago=5, timeframe="1m")
            deleted_by_tf = prune_bars_by_retention(db)
            self.assertEqual(deleted_by_tf, {})
        finally:
            db.close()

    def test_covers_all_ten_stored_timeframes(self):
        """Every timeframe the app actually stores rows for gets its own
        prune pass — not just 1m/1h, the two this mechanism historically
        covered before per-timeframe retention existed."""
        from backend.repositories.bar_repository import prune_bars_by_retention

        db = self._session()
        try:
            all_tfs = ["1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk"]
            # 2000 days old: past every timeframe's default window,
            # including the longest (1096 days for 1d/1wk).
            for tf in all_tfs:
                self._add_bar(db, "AAPL", minutes_ago=60 * 24 * 2000, timeframe=tf)
            deleted_by_tf = prune_bars_by_retention(db)
            self.assertEqual(set(deleted_by_tf.keys()), set(all_tfs))
            self.assertEqual(db.query(BarModel).count(), 0)
        finally:
            db.close()


class TestRetentionSettings(unittest.TestCase):
    """Per-timeframe retention defaults — set 2026-09-09 to mirror
    BACKFILL_1M/1H/1D_DAYS (15/365/1095) at +1 day, so nothing is pruned
    right after backfill just fetched it."""

    def test_sub_hour_defaults_are_16_days(self):
        from backend.config.settings import RetentionSettings

        cfg = RetentionSettings(_env_file=None)
        for tf in ("1m", "2m", "3m", "5m", "15m", "30m"):
            self.assertEqual(cfg.days_for(tf), 16, msg=f"{tf} should default to 16")

    def test_hourly_defaults_are_366_days(self):
        from backend.config.settings import RetentionSettings

        cfg = RetentionSettings(_env_file=None)
        for tf in ("1h", "4h"):
            self.assertEqual(cfg.days_for(tf), 366, msg=f"{tf} should default to 366")

    def test_daily_and_weekly_defaults_are_1096_days(self):
        from backend.config.settings import RetentionSettings

        cfg = RetentionSettings(_env_file=None)
        for tf in ("1d", "1wk"):
            self.assertEqual(cfg.days_for(tf), 1096, msg=f"{tf} should default to 1096")

    def test_unknown_timeframe_falls_back_to_longest_window(self):
        """Safer to under-prune an unrecognized timeframe than to
        silently delete it fast on a coding mistake elsewhere."""
        from backend.config.settings import RetentionSettings

        cfg = RetentionSettings(_env_file=None)
        self.assertEqual(cfg.days_for("bogus"), cfg.tf_1wk_days)

    def test_env_var_override(self):
        import os
        from unittest.mock import patch

        from backend.config.settings import RetentionSettings

        with patch.dict(os.environ, {"RETENTION_TF_1M_DAYS": "5"}):
            cfg = RetentionSettings()
            self.assertEqual(cfg.tf_1m_days, 5)

    def test_top_level_settings_exposes_retention(self):
        from backend.config.settings import settings

        self.assertEqual(settings.retention.tf_1m_days, 16)
        self.assertEqual(settings.retention.tf_1h_days, 366)
        self.assertEqual(settings.retention.tf_1d_days, 1096)


class TestSettingsFields(unittest.TestCase):
    """Verify the Phase 3.3.8 settings are accessible.

    ``bar_retention_days`` was removed 2026-09-09 — it never actually
    clamped anything in any real call path (see its removal note in
    MarketDataSettings) and was superseded by RetentionSettings
    (TestRetentionSettings above covers that).
    """

    def test_bar_retention_days_no_longer_exists(self):
        from backend.config.settings import settings

        self.assertFalse(hasattr(settings.market_data, "bar_retention_days"))

    def test_backfill_on_add_defaults_to_true(self):
        from backend.config.settings import settings

        self.assertEqual(settings.market_data.backfill_on_add, True)


class TestSafeBarCountsRetentionFields(unittest.TestCase):
    """_safe_bar_counts returns per-timeframe retention fields."""

    def test_returns_retention_fields(self):
        from backend.api.system.router import _safe_bar_counts

        result = _safe_bar_counts()
        self.assertIsNotNone(result)
        self.assertIn("oldest_bar", result)
        self.assertIn("newest_bar", result)
        self.assertIn("distinct_symbols", result)
        self.assertIn("retention_days_by_timeframe", result)
        by_tf = result["retention_days_by_timeframe"]
        self.assertEqual(by_tf["1m"], 16)
        self.assertEqual(by_tf["1h"], 366)
        self.assertEqual(by_tf["1d"], 1096)
        self.assertIsInstance(result["distinct_symbols"], int)


if __name__ == "__main__":
    unittest.main()
