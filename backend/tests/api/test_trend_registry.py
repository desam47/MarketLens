"""
Regression tests for backend.api.trend.registry's engine-seeding paths.

Found live 2026-09-18: ``_batch_seed_engines`` (the startup warmup path,
"Phase 3.9.3 — single batched query instead of N per-symbol queries")
called ``get_engine(symbol)`` to obtain each engine, but ``get_engine()``
itself already runs a full per-symbol ``_seed_from_bar_model`` query and
applies those bars the first time it sees a symbol — so every symbol was
seeded TWICE (once via that per-symbol query, once via the batch's own
shared-query rows), doubling every bar applied to the engine. That
redundant per-symbol query, run sequentially for every watchlist symbol,
was the dominant cost of server startup (~500-700ms/symbol) — the fix is
`_create_and_register_engine`, a bare (unseeded) engine constructor the
batch path uses instead of `get_engine()`.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.market_data_sql import BarModel


class TestBatchSeedEnginesNoDoubleSeed(unittest.TestCase):
    def setUp(self):
        from backend.api.trend import registry as trend_registry

        self.registry = trend_registry
        self.registry._engines.clear()

        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        BarModel.__table__.create(self.engine, checkfirst=True)
        self.Session = sessionmaker(bind=self.engine)
        self._session_patch = patch.object(trend_registry, "SessionLocal", self.Session)
        self._session_patch.start()

    def tearDown(self):
        self._session_patch.stop()
        self.registry._engines.clear()
        self.engine.dispose()

    def _insert_bars(self, symbol: str, timeframe: str, n: int):
        with self.Session() as db:
            for i in range(n):
                db.add(
                    BarModel(
                        symbol=symbol,
                        timeframe=timeframe,
                        open=100.0,
                        high=101.0,
                        low=99.0,
                        close=100.0 + i,
                        volume=1000,
                        timestamp=datetime(2025, 1, 1) + timedelta(minutes=i),
                        provider="test",
                        data_status="HISTORICAL",
                    )
                )
            db.commit()

    def test_new_symbol_seeded_exactly_once(self):
        """A brand-new symbol's bars must be applied to the engine exactly
        once — not once via get_engine()'s own query and again via the
        batch's shared-query rows (the double-seed this regression covers)."""
        self._insert_bars("AAPL", "1d", n=5)

        with patch.object(self.registry, "TrendEngine") as MockEngineCls:
            mock_engine = MagicMock()
            MockEngineCls.return_value = mock_engine

            results = self.registry._batch_seed_engines(("AAPL",))

        self.assertEqual(results["AAPL"], 5)
        self.assertEqual(
            mock_engine.update.call_count, 5, "bars must be applied exactly once, not doubled"
        )

    def test_already_created_engine_is_not_reseeded(self):
        """A symbol whose engine already exists (e.g. warmed by an earlier
        get_engine() call) must not be re-seeded a second time by the
        batch path — that would double-count its bars just as badly."""
        self._insert_bars("AAPL", "1d", n=5)

        existing_engine = MagicMock()
        self.registry._engines["AAPL"] = existing_engine

        with patch.object(self.registry, "TrendEngine") as MockEngineCls:
            results = self.registry._batch_seed_engines(("AAPL",))
            MockEngineCls.assert_not_called()

        self.assertEqual(results["AAPL"], 0)
        existing_engine.update.assert_not_called()

    def test_batch_seed_uses_a_single_query(self):
        """The whole point of Phase 3.9.3: one query for all symbols, not
        one per symbol. Count real SQLAlchemy executions via a counting
        wrapper around the session's query() method."""
        self._insert_bars("AAPL", "1d", n=3)
        self._insert_bars("MSFT", "1d", n=3)

        query_calls = {"n": 0}
        real_sessionmaker = self.Session

        def counting_session():
            db = real_sessionmaker()
            real_query = db.query

            def counting_query(*a, **kw):
                query_calls["n"] += 1
                return real_query(*a, **kw)

            db.query = counting_query
            return db

        with (
            patch.object(self.registry, "SessionLocal", counting_session),
            patch.object(self.registry, "TrendEngine") as MockEngineCls,
        ):
            MockEngineCls.side_effect = lambda symbol: MagicMock()
            self.registry._batch_seed_engines(("AAPL", "MSFT"))

        # _batch_seed_engines issues exactly 2 .query() calls (the
        # subquery + the outer aliased select) regardless of symbol count.
        self.assertEqual(query_calls["n"], 2, "must be O(1) queries, not O(symbols)")


if __name__ == "__main__":
    unittest.main()
