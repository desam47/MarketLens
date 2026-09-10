"""Tests for backend.api.tape.registry."""
import unittest
from unittest.mock import patch

from backend.api.tape import registry
from backend.market_data.services.engine_seeder import engine_registry
from backend.tape.tape_engine import TapeEngine


class TestTapeRegistry(unittest.TestCase):
    def setUp(self):
        registry._engines.clear()

    def tearDown(self):
        registry._engines.clear()

    @patch("backend.api.tape.registry._seed_from_webull_ticks", return_value=0)
    def test_get_tape_engine_is_lazy_and_shared(self, _seed):
        e1 = registry.get_tape_engine("aapl")
        e2 = registry.get_tape_engine("AAPL")
        self.assertIs(e1, e2)
        self.assertIsInstance(e1, TapeEngine)
        self.assertEqual(e1.symbol, "AAPL")

    @patch("backend.api.tape.registry._seed_from_webull_ticks", return_value=0)
    def test_get_tape_engine_registers_for_trade_dispatch(self, _seed):
        e = registry.get_tape_engine("MSFT")
        # a dispatch_trade for MSFT must now reach the engine
        engine_registry.dispatch_trade("MSFT", price=400.0, size=100,
                                       timestamp=__import__("time").time(), side="buy")
        self.assertEqual(e.get_snapshot()["trade_count"], 1)

    @patch("backend.api.tape.registry._seed_from_webull_ticks", return_value=0)
    def test_persist_once_drains_engines(self, _seed):
        import time as _t
        e = registry.get_tape_engine("NVDA")
        base = _t.time() - 5
        for i in range(20):
            e.update(price=800 + i * 0.1, size=100, timestamp=base + i * 0.2, side="buy")
        e.update(price=810, size=100, timestamp=base + 6, side="buy")  # roll last bucket
        with patch("backend.repositories.tape_repository.upsert_tape_bars") as up:
            up.return_value = 5
            n = registry._persist_once()
        self.assertEqual(n, 5)
        self.assertTrue(up.called)
        self.assertGreater(len(up.call_args[0][1]), 0)
        # drained — a second persist has nothing
        with patch("backend.repositories.tape_repository.upsert_tape_bars") as up2:
            registry._persist_once()
            up2.assert_not_called()

    def test_warmup_noop_when_disabled(self):
        from backend.config.settings import settings

        with patch.object(settings.tape, "enabled", False):
            self.assertEqual(registry.warmup_tape_engines(), [])


if __name__ == "__main__":
    unittest.main()
