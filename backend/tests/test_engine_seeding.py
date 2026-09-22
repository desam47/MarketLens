"""
Regression tests for engine seeding and live-tick dispatch.

These tests verify that:
  1. Analysis engines seed from persisted DB data on first creation, so the
     API returns real signals immediately after a backend restart — instead of
     returning "unknown" until enough new ticks arrive.
  2. The EngineRegistry correctly routes fresh ticks dispatched by the ingestion
     service to registered engines, keeping in-memory state current between
     restart cycles.

Bug this prevents: "no data showing up" after backend restart because the
regime/trend/confluence engines were in-memory only and lost their state.
"""

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api.regime.router import get_engine as get_regime_engine_from_router
from backend.database import Base
from backend.market_data.services.engine_seeder import (
    EngineRegistry,
    seed_engine_from_quotes,
)
from backend.models.market_data_sql import BarModel, QuoteModel
from backend.regime.market_regime_engine import MarketRegimeEngine


class _InMemoryDBMixin:
    """Route the seeding code's DB reads at an ephemeral in-memory SQLite
    so these tests never touch the real ``marketlens.db``.

    They used to (found 2026-09-10): the fixtures below wrote 60
    synthetic ``provider="test"`` AAPL rows straight to production via
    the real ``SessionLocal`` — after deleting the real AAPL rows —
    so the running backend's ingestion picked the fakes up, resampled
    them into a bogus live 1d bar (~$155 while AAPL traded ~$318), and
    poisoned every downstream price/level shown in the UI. One fixture
    also wrote ``data_status="historical"`` (lowercase — not a valid
    enum value), which later made ``load_bars`` raise.
    """

    def setUp(self):
        super().setUp()
        self._mem_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self._mem_engine)
        self.MemSession = sessionmaker(
            bind=self._mem_engine,
            autoflush=False,
            expire_on_commit=False,
        )
        # Every DB read in the seeding path goes through
        # engine_seeder.SessionLocal (seed_engine_from_quotes /
        # seed_engine_from_bars) — patch it there.
        self._db_patch = patch(
            "backend.market_data.services.engine_seeder.SessionLocal",
            self.MemSession,
        )
        self._db_patch.start()
        # The shared TrendEngine registry imports its own SessionLocal for
        # BarModel warmup; route that read to the same isolated fixture DB.
        self._trend_db_patch = patch(
            "backend.api.trend.registry.SessionLocal",
            self.MemSession,
        )
        self._trend_db_patch.start()
        # A prior test may have cached a regime engine keyed "AAPL";
        # clear so seeding actually runs against the patched DB.
        from backend.api.regime.router import _engines as _regime_engines

        _regime_engines.pop("AAPL", None)
        # The regime engine shares the trend registry. Clear that cache too so
        # this fixture always seeds the fresh in-memory database below rather
        # than reusing state from an earlier test.
        from backend.api.trend.registry import _engines as _trend_engines

        _trend_engines.pop("AAPL", None)

    def tearDown(self):
        self._trend_db_patch.stop()
        self._db_patch.stop()
        self._mem_engine.dispose()
        super().tearDown()


class _SeededDBMixin(_InMemoryDBMixin):
    """Insert synthetic QuoteModel rows for AAPL into the in-memory DB."""

    def setUp(self):
        super().setUp()
        base_time = datetime.now(UTC) - timedelta(hours=10)
        with self.MemSession() as db:
            # 60 synthetic quotes with a clear uptrend — enough for regime classification.
            for i in range(60):
                db.add(
                    QuoteModel(
                        symbol="AAPL",
                        price=150.0 + i * 0.10,  # clear uptrend
                        bid=150.0 + i * 0.10 - 0.01,
                        ask=150.0 + i * 0.10 + 0.01,
                        volume=1_000_000,
                        timestamp=base_time + timedelta(minutes=i * 10),
                        provider="test",
                        data_status="ok",
                    )
                )
            db.commit()


class TestSeedEngineFromQuotes(_SeededDBMixin, unittest.TestCase):
    """Tests for the seed_engine_from_quotes helper."""

    def test_seed_produces_non_unknown_regime(self):
        """After seeding from the DB, the regime engine must produce a non-unknown regime.

        This is the core regression test for the restart-bug: before the seeder was
        written, a fresh engine would return RegimeSignal with regime=MarketRegime.UNKNOWN
        until at least ~50 ticks arrived. After seeding, it should return a real
        classification immediately.
        """
        # AAPL has ~56 persisted quotes in the test DB from the ingestion runs.
        engine = MarketRegimeEngine("AAPL")
        self.assertIsNone(
            engine.get_current_regime(),
            "Fresh engine should have no regime signal before seeding",
        )

        count = seed_engine_from_quotes("AAPL", engine.update, max_points=200)
        self.assertGreater(
            count,
            0,
            "seed_engine_from_quotes should have found and replayed some quotes. "
            "If this fails, ensure the test DB (marketlens.db) has QuoteModel rows "
            "for AAPL from a prior ingestion run.",
        )

        signal = engine.get_current_regime()
        self.assertIsNotNone(
            signal,
            "Regime engine should have a signal after seeding",
        )
        # RegimeSignal.regime is an Enum — compare via .value to avoid enum mismatch
        self.assertNotEqual(
            signal.regime.value,
            "unknown",
            "Seeded regime should not be 'unknown' — the engine should have "
            "enough history from the DB to classify the market.",
        )

    def test_seed_replays_in_chronological_order(self):
        """The seeder must replay quotes oldest→newest so indicators compute correctly."""
        engine = MarketRegimeEngine("AAPL")
        # Track the timestamps we see during seeding by hooking into update().
        seen_timestamps = []
        original_update = engine.update

        def tracking_update(*args, **kwargs):
            # The update signature is update(price, volume, timestamp, ...)
            # timestamp is the 3rd positional arg.
            ts = args[2] if len(args) >= 3 else kwargs.get("timestamp")
            seen_timestamps.append(ts)
            return original_update(*args, **kwargs)

        seed_engine_from_quotes("AAPL", tracking_update, max_points=50)
        self.assertGreater(len(seen_timestamps), 1, "Should have replayed multiple quotes")

        # Verify strictly increasing timestamps (oldest first)
        for i in range(1, len(seen_timestamps)):
            self.assertGreaterEqual(
                seen_timestamps[i],
                seen_timestamps[i - 1],
                f"Quote timestamps must be replayed oldest→newest, but "
                f"ts[{i - 1}]={seen_timestamps[i - 1]} > ts[{i}]={seen_timestamps[i]}",
            )


class TestEngineRegistry(unittest.TestCase):
    """Tests for the EngineRegistry that routes ticks to registered engines."""

    def setUp(self):
        self.registry = EngineRegistry()

    def test_register_and_dispatch_quote(self):
        """dispatch_quote must invoke every callback registered for that symbol."""
        received = []

        def callback_a(price, volume, timestamp, **kw):
            received.append(("a", price))

        def callback_b(price, volume, timestamp, **kw):
            received.append(("b", price))

        self.registry.register("quote", "AAPL", callback_a)
        self.registry.register("quote", "AAPL", callback_b)
        self.registry.register(
            "quote", "NVDA", lambda **kw: received.append(("n", 1))
        )  # different symbol

        ts = datetime.now(UTC)
        notified = self.registry.dispatch_quote(
            symbol="AAPL",
            price=123.45,
            volume=1000,
            timestamp=ts,
            high=124.0,
            low=122.0,
            open_price=123.0,
        )

        self.assertEqual(notified, 2, "Should have notified both AAPL callbacks")
        self.assertEqual(len(received), 2)
        self.assertIn(("a", 123.45), received)
        self.assertIn(("b", 123.45), received)

    def test_dispatch_bar_routes_by_timeframe(self):
        """dispatch_bar must only notify engines registered for the specific timeframe."""
        received = []

        self.registry.register("bar:1h", "AAPL", lambda **kw: received.append("1h"))
        self.registry.register("bar:1d", "AAPL", lambda **kw: received.append("1d"))

        ts = datetime.now(UTC)
        n_1h = self.registry.dispatch_bar("AAPL", "1h", price=100, volume=1, timestamp=ts)
        n_1d = self.registry.dispatch_bar("AAPL", "1d", price=100, volume=1, timestamp=ts)

        self.assertEqual(n_1h, 1)
        self.assertEqual(n_1d, 1)
        self.assertEqual(received, ["1h", "1d"])

    def test_dispatch_is_safe_when_no_callbacks_registered(self):
        """dispatch_* must return 0 and not raise when no engine is registered."""
        n = self.registry.dispatch_quote("MISSING", price=0, volume=0, timestamp=datetime.now())
        self.assertEqual(n, 0)

        n = self.registry.dispatch_bar("MISSING", "1d", price=0, volume=0, timestamp=datetime.now())
        self.assertEqual(n, 0)

    def test_exception_in_callback_does_not_break_dispatch(self):
        """If one engine's update() raises, dispatch must still notify remaining engines."""
        received = []

        def bad(**kw):
            raise RuntimeError("engine bug")

        def good(**kw):
            received.append("ok")

        self.registry.register("quote", "AAPL", bad)
        self.registry.register("quote", "AAPL", good)

        n = self.registry.dispatch_quote("AAPL", price=1, volume=1, timestamp=datetime.now())
        self.assertEqual(n, 1, "Should have notified the good callback")
        self.assertEqual(received, ["ok"])

    def test_callback_receives_high_low_open(self):
        """dispatch_quote must pass OHLCV kwargs to registered callbacks."""
        captured = {}

        def capture(**kw):
            captured.update(kw)

        self.registry.register("quote", "AAPL", capture)
        ts = datetime.now(UTC)
        self.registry.dispatch_quote(
            symbol="AAPL",
            price=100,
            volume=50,
            timestamp=ts,
            high=101,
            low=99,
            open_price=99.5,
        )

        self.assertEqual(captured["price"], 100)
        self.assertEqual(captured["high"], 101)
        self.assertEqual(captured["low"], 99)
        self.assertEqual(captured["open_price"], 99.5)

    def test_callback_receives_symbol(self):
        """Regression for a live bug (2026-09-09): dispatch_quote never
        passed `symbol` to callbacks — dispatch_bar always has (see
        test_dispatch_bar_routes_by_timeframe's sibling assertions on
        the real AlertsEngine._on_quote, which requires it) — so a
        callback that needs to know which symbol it's being called for
        (e.g. AlertsEngine._on_quote(self, symbol, price, ...)) crashed
        on every single quote, silently swallowed by dispatch_quote's
        own except block. Every callback here uses **kw already, so
        this only needed the dispatch side fixed, not the tests."""
        captured = {}

        def capture(**kw):
            captured.update(kw)

        self.registry.register("quote", "AAPL", capture)
        self.registry.dispatch_quote(
            symbol="AAPL",
            price=100,
            volume=50,
            timestamp=datetime.now(UTC),
        )

        self.assertEqual(captured.get("symbol"), "AAPL")

    def test_dispatch_trade_fans_out_with_side(self):
        """dispatch_trade must notify every 'trade' callback with
        symbol/price/size/timestamp/side, and only for that symbol."""
        received = []
        self.registry.register("trade", "AAPL", lambda **kw: received.append(kw))
        self.registry.register("trade", "NVDA", lambda **kw: received.append({"other": True}))

        ts = datetime.now(UTC)
        n = self.registry.dispatch_trade("AAPL", price=191.5, size=300, timestamp=ts, side="sell")

        self.assertEqual(n, 1)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["symbol"], "AAPL")
        self.assertEqual(received[0]["price"], 191.5)
        self.assertEqual(received[0]["size"], 300)
        self.assertEqual(received[0]["side"], "sell")

    def test_dispatch_trade_swallows_callback_errors(self):
        received = []
        self.registry.register(
            "trade", "AAPL", lambda **kw: (_ for _ in ()).throw(RuntimeError("x"))
        )
        self.registry.register("trade", "AAPL", lambda **kw: received.append("ok"))
        n = self.registry.dispatch_trade("AAPL", price=1, size=1, timestamp=datetime.now(UTC))
        self.assertEqual(n, 1)
        self.assertEqual(received, ["ok"])


class TestRouterSeedingIntegration(_InMemoryDBMixin, unittest.TestCase):
    """End-to-end test: the router's get_regime_engine must seed from the DB.

    This is the highest-value regression test — it exercises the exact code path
    that was broken before engine_seeder.py was introduced.

    Note: the router's ``get_engine()`` seeds from BarModel ("1m") rows via
    ``seed_engine_from_bars``, not from QuoteModel rows — quote-frequency
    updates are intentionally NOT fed to the regime engine (see the comment
    in ``backend/api/regime/router.py::get_engine`` re: stale Alpaca
    free-tier quotes after 16:00 ET). This fixture seeds bars accordingly
    instead of reusing ``_SeededDBMixin`` (which only inserts quotes).
    """

    def setUp(self):
        super().setUp()
        # Seed each configured confluence timeframe with a clear uptrend.
        # Regime classification requires an overall trend plus a confluence
        # snapshot; a 1m-only fixture leaves the other timeframe signals
        # absent and correctly produces ``unknown``.
        base_time = datetime.now(UTC) - timedelta(minutes=60)
        with self.MemSession() as db:
            for timeframe in ("1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk"):
                for i in range(60):
                    price = 150.0 + i * 0.10
                    db.add(
                        BarModel(
                            symbol="AAPL",
                            timeframe=timeframe,
                            open=price - 0.05,
                            high=price + 0.05,
                            low=price - 0.10,
                            close=price,
                            volume=1_000_000,
                            timestamp=base_time + timedelta(minutes=i),
                            provider="test",
                            data_status="HISTORICAL",
                            source="raw",
                        )
                    )
            db.commit()

    def test_regime_engine_returns_non_unknown_on_first_request(self):
        """Simulate the restart scenario: fresh in-process engine, first API request.

        The router's get_regime_engine() should create a fresh engine, seed it from
        the DB, register it for live-tick updates, and return a non-unknown regime.
        """
        # Reset the router's module-level engine cache so a prior test (e.g.
        # test_seed_produces_non_unknown_regime, which seeds an engine into
        # the registry) doesn't return a stale engine here.
        from backend.api.regime.router import _engines

        _engines.pop("AAPL", None)

        signal = get_regime_engine_from_router("AAPL").get_current_regime()

        self.assertIsNotNone(
            signal,
            "Regime engine should have a signal after seeding from DB. "
            "If this fails, either the DB has no BarModel '1m' rows for AAPL, "
            "or the router's get_regime_engine() is not calling seed_engine_from_bars.",
        )
        self.assertNotEqual(
            signal.regime.value,
            "unknown",
            f"Seeded regime for AAPL should not be 'unknown', got: {signal.regime.value}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
