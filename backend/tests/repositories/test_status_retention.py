"""
Retention for the append-only quotes / provider_status / market_status tables.

Nothing pruned them: the ingestion loops inserted on every tick and only ``bars`` had a
retention prune. ``prune_status_tables`` trims each to its window, in short transactions,
and the hourly ``_retention_prune_loop`` now calls it independently of the bar prune.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.market_data_sql import MarketStatusModel, ProviderStatusModel, QuoteModel
from backend.repositories.status_retention import prune_status_tables

NOW = datetime(2026, 9, 19, 12, 0)


def _quote(ts):
    return QuoteModel(symbol="AAPL", price=1.0, timestamp=ts, provider="webull", data_status="live")


def _provider(ts):
    return ProviderStatusModel(provider_name="webull", is_healthy=True, timestamp=ts)


def _market(ts):
    return MarketStatusModel(symbol="AAPL", is_open=False, timezone="America/New_York",
                             provider="webull", timestamp=ts)


class _Db(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        for model in (QuoteModel, ProviderStatusModel, MarketStatusModel):
            model.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)

    def _count(self, model):
        with self.Session() as db:
            return db.scalar(select(func.count()).select_from(model))

    def _seed(self, days_old, n=1):
        with self.Session() as db:
            for i in range(n):
                ts = NOW - timedelta(days=days_old, minutes=i)
                db.add_all([_quote(ts), _provider(ts), _market(ts)])
            db.commit()


class TestPruneStatusTables(_Db):
    def test_old_rows_go_and_recent_rows_stay(self):
        self._seed(days_old=60, n=3)
        self._seed(days_old=1, n=2)
        with self.Session() as db:
            deleted = prune_status_tables(db, now=NOW)
        self.assertEqual(deleted, {"quotes": 3, "provider_status": 3, "market_status": 3})
        for model in (QuoteModel, ProviderStatusModel, MarketStatusModel):
            self.assertEqual(self._count(model), 2, model.__tablename__)

    def test_a_no_op_returns_an_empty_dict(self):
        self._seed(days_old=1, n=4)
        with self.Session() as db:
            self.assertEqual(prune_status_tables(db, now=NOW), {})
        self.assertEqual(self._count(QuoteModel), 4)

    def test_rows_just_inside_the_window_are_kept(self):
        self._seed(days_old=29)
        self._seed(days_old=31)
        with self.Session() as db:
            prune_status_tables(db, now=NOW)
        self.assertEqual(self._count(QuoteModel), 1)

    def test_chunking_still_deletes_everything(self):
        self._seed(days_old=90, n=7)
        with self.Session() as db:
            deleted = prune_status_tables(db, chunk_size=2, now=NOW)
        self.assertEqual(deleted["quotes"], 7)
        self.assertEqual(self._count(QuoteModel), 0)

    def test_each_table_uses_its_own_window(self):
        self._seed(days_old=10, n=1)
        retention = MagicMock(quotes_days=7, provider_status_days=30, market_status_days=30)
        with patch("backend.config.settings.settings.retention", retention), self.Session() as db:
            deleted = prune_status_tables(db, now=NOW)
        self.assertEqual(deleted, {"quotes": 1})
        self.assertEqual(self._count(ProviderStatusModel), 1)
        self.assertEqual(self._count(MarketStatusModel), 1)

    def test_rejects_a_non_positive_chunk_size(self):
        with self.Session() as db, self.assertRaises(ValueError):
            prune_status_tables(db, chunk_size=0)

    def test_default_windows_are_a_month(self):
        from backend.config.settings import RetentionSettings

        r = RetentionSettings()
        self.assertEqual((r.quotes_days, r.provider_status_days, r.market_status_days), (30, 30, 30))


class TestRetentionLoopWiring(unittest.IsolatedAsyncioTestCase):
    async def _one_pass(self, bar_prune, status_prune):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        svc = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])
        svc.manager = MagicMock()

        async def stop_after_one(*_a, **_k):
            svc.is_running = False

        svc._jittered_sleep = stop_after_one
        svc.is_running = True
        with patch("backend.market_data.services.ingestion_service.SessionLocal"), \
             patch("backend.repositories.bar_repository.prune_bars_by_retention", bar_prune), \
             patch("backend.repositories.status_retention.prune_status_tables", status_prune):
            await svc._retention_prune_loop()

    async def test_both_prunes_run_each_pass(self):
        bars, status = MagicMock(return_value={}), MagicMock(return_value={"quotes": 2})
        with self.assertLogs("backend.market_data.services.ingestion_service", "INFO") as cm:
            await self._one_pass(bars, status)
        bars.assert_called_once()
        status.assert_called_once()
        self.assertTrue(any("Status-table retention" in r.getMessage() for r in cm.records))

    async def test_a_failing_bar_prune_does_not_skip_the_status_prune(self):
        bars, status = MagicMock(side_effect=RuntimeError("locked")), MagicMock(return_value={})
        with self.assertLogs("backend.market_data.services.ingestion_service", "ERROR"):
            await self._one_pass(bars, status)
        status.assert_called_once()

    async def test_a_failing_status_prune_is_logged_not_fatal(self):
        bars, status = MagicMock(return_value={}), MagicMock(side_effect=RuntimeError("locked"))
        with self.assertLogs("backend.market_data.services.ingestion_service", "ERROR") as cm:
            await self._one_pass(bars, status)
        bars.assert_called_once()
        self.assertTrue(any("status-table retention" in r.getMessage() for r in cm.records))


if __name__ == "__main__":
    unittest.main()
