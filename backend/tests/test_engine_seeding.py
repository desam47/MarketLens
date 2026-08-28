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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../'))

from backend.api.regime.router import get_engine as get_regime_engine_from_router
from backend.database import SessionLocal
from backend.market_data.services.engine_seeder import (
    EngineRegistry,
    seed_engine_from_quotes,
)
from backend.regime.market_regime_engine import MarketRegimeEngine


class _SeededDBMixin:
    """Insert synthetic QuoteModel rows for AAPL before each test."""

    def setUp(self):
        super().setUp()
        self._seed_aapl_quotes()

    @staticmethod
    def _seed_aapl_quotes():
        # Import models normally so Base is the shared singleton.
        from backend.database import Base, engine
        from backend.models.market_data_sql import QuoteModel
        # Ensure tables exist.
        Base.metadata.create_all(bind=engine)
        # Remove any existing AAPL rows to keep tests deterministic.
        with SessionLocal() as db:
            db.query(QuoteModel).filter(QuoteModel.symbol == "AAPL").delete()
            # 60 synthetic quotes with a clear uptrend — enough for regime classification.
            base_time = datetime.now(UTC) - timedelta(hours=10)
            for i in range(60):
                row = QuoteModel(
                    symbol="AAPL",
                    price=150.0 + i * 0.10,  # clear uptrend
                    bid=150.0 + i * 0.10 - 0.01,
                    ask=150.0 + i * 0.10 + 0.01,
                    volume=1_000_000,
                    timestamp=base_time + timedelta(minutes=i * 10),
                    provider="test",
                    data_status="ok",
                )
                db.add(row)
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
            count, 0,
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
            signal.regime.value, "unknown",
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
                seen_timestamps[i], seen_timestamps[i - 1],
                f"Quote timestamps must be replayed oldest→newest, but "
                f"ts[{i-1}]={seen_timestamps[i-1]} > ts[{i}]={seen_timestamps[i]}",
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
        self.registry.register("quote", "NVDA", lambda **kw: received.append(("n", 1)))  # different symbol

        ts = datetime.now(UTC)
        notified = self.registry.dispatch_quote(
            symbol="AAPL", price=123.45, volume=1000,
            timestamp=ts, high=124.0, low=122.0, open_price=123.0,
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
            symbol="AAPL", price=100, volume=50, timestamp=ts,
            high=101, low=99, open_price=99.5,
        )

        self.assertEqual(captured["price"], 100)
        self.assertEqual(captured["high"], 101)
        self.assertEqual(captured["low"], 99)
        self.assertEqual(captured["open_price"], 99.5)


class TestRouterSeedingIntegration(_SeededDBMixin, unittest.TestCase):
    """End-to-end test: the router's get_regime_engine must seed from the DB.

    This is the highest-value regression test — it exercises the exact code path
    that was broken before engine_seeder.py was introduced.
    """

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
            "If this fails, either the DB has no QuoteModel rows for AAPL, "
            "or the router's get_regime_engine() is not calling seed_engine_from_quotes.",
        )
        self.assertNotEqual(
            signal.regime.value, "unknown",
            f"Seeded regime for AAPL should not be 'unknown', got: {signal.regime.value}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
