"""
Failures on data paths used to be swallowed by a bare ``except Exception: pass``, so a
broken section, dropped tape bars or a rejected warmup bar left no trace anywhere.

These tests pin two things for every site that was changed:

* it still degrades exactly as before (no exception escapes, same fallback value), and
* the failure is now logged, at a level that fits how often it can happen.
"""
import ast
import pathlib
import unittest
from unittest.mock import MagicMock, patch

_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Modules that no longer contain ANY silent broad handler; a new one is a regression.
_MUST_NOT_SWALLOW_SILENTLY = (
    "backend/ai/context.py",
    "backend/indicators/roc.py",
    "backend/indicators/relative_volume.py",
    "backend/api/tape/registry.py",
    "backend/api/trend/registry.py",
)


def _silent_broad_handlers(path: pathlib.Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.ExceptHandler):
            continue
        broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
        )
        if broad and all(isinstance(stmt, ast.Pass) for stmt in node.body):
            lines.append(node.lineno)
    return lines


class TestNoSilentBroadHandlers(unittest.TestCase):
    def test_cleaned_modules_stay_clean(self):
        for rel in _MUST_NOT_SWALLOW_SILENTLY:
            with self.subTest(module=rel):
                self.assertEqual(_silent_broad_handlers(_ROOT / rel), [],
                                 f"{rel}: `except Exception: pass` is back (line numbers above)")


class TestTrendWarmup(unittest.TestCase):
    def _bars(self, n):
        return [MagicMock(close=100.0 + i, volume=10, timestamp=i) for i in range(n)]

    def test_rejected_bars_are_counted_and_reported_once(self):
        from backend.api.trend.registry import _feed_warmup_bars
        from backend.engines.timeframe import Timeframe

        engine = MagicMock()
        engine.update.side_effect = [None, ValueError("bad row"), None, ValueError("bad row 2")]
        with self.assertLogs("backend.api.trend.registry", "WARNING") as cm:
            seeded = _feed_warmup_bars(engine, "AAPL", Timeframe("1m"), self._bars(4))

        self.assertEqual(seeded, 2, "good bars after a bad one must still be fed")
        self.assertEqual(engine.update.call_count, 4, "one bad bar must not abort warmup")
        self.assertEqual(len(cm.records), 1, "one report per (symbol, timeframe), not per bar")
        msg = cm.records[0].getMessage()
        self.assertIn("AAPL/1m", msg)
        self.assertIn("2 of 4", msg)
        self.assertIn("bad row 2", msg, "the last error is included")

    def test_clean_warmup_is_silent(self):
        from backend.api.trend.registry import _feed_warmup_bars
        from backend.engines.timeframe import Timeframe

        with self.assertNoLogs("backend.api.trend.registry", "WARNING"):
            self.assertEqual(_feed_warmup_bars(MagicMock(), "AAPL", Timeframe("1m"), self._bars(3)), 3)


class TestTapePersist(unittest.TestCase):
    def test_failing_drain_is_logged_and_others_still_persist(self):
        from backend.api.tape import registry

        broken, healthy = MagicMock(), MagicMock()
        broken.drain_pending.side_effect = RuntimeError("engine wedged")
        healthy.drain_pending.return_value = [{"symbol": "MSFT"}]
        with patch.dict(registry._engines, {"AAPL": broken, "MSFT": healthy}, clear=True), \
             patch("backend.database.SessionLocal", MagicMock()), \
             patch("backend.repositories.tape_repository.upsert_tape_bars", return_value=1) as up, \
             self.assertLogs("backend.api.tape.registry", "WARNING") as cm:
            persisted = registry._persist_once()

        self.assertEqual(persisted, 1)
        self.assertEqual(up.call_args.args[1], [{"symbol": "MSFT"}])
        self.assertTrue(any("AAPL" in r.getMessage() for r in cm.records), "names the failing symbol")
        self.assertTrue(any(r.exc_info for r in cm.records), "carries the traceback")


class TestIndicators(unittest.TestCase):
    def _check(self, indicator, logger_name, feed):
        with patch.object(type(indicator), "calculate", side_effect=RuntimeError("boom")), \
             self.assertLogs(logger_name, "DEBUG") as cm:
            results = [indicator.update(point) for point in feed]
        self.assertTrue(all(r is None for r in results), "a failing update still returns None")
        self.assertTrue(any(r.exc_info for r in cm.records))

    def test_roc_update_failure_is_logged(self):
        from backend.indicators.roc import ROCIndicator

        ind = ROCIndicator(period=2)
        self._check(ind, "backend.indicators.roc", [{"close": float(i + 1)} for i in range(4)])

    def test_relative_volume_update_failure_is_logged(self):
        from backend.indicators.relative_volume import RelativeVolumeIndicator

        self._check(RelativeVolumeIndicator(period=2), "backend.indicators.relative_volume",
                    [{"volume": 100.0}] * 3)


class TestAiContextSections(unittest.TestCase):
    def test_failing_section_is_logged_and_returns_its_empty_shape(self):
        from backend.ai import context

        with patch("backend.regime.market_regime_engine.MarketRegimeEngine",
                   side_effect=RuntimeError("no regime engine")), \
             self.assertLogs("backend.ai.context", "DEBUG") as cm:
            result = context._regime_context("AAPL")

        self.assertIsInstance(result, dict)
        self.assertTrue(any("regime_context" in r.getMessage() and r.exc_info for r in cm.records))


class TestSignalRecorderHelpers(unittest.TestCase):
    def setUp(self):
        from backend.services.signal_recorder import SignalRecorder
        self.recorder = SignalRecorder.__new__(SignalRecorder)  # helpers use no instance state

    def test_market_regime_unavailable_is_logged_and_none(self):
        with patch("backend.api.market_context.router.get_engine", side_effect=RuntimeError("cold")), \
             self.assertLogs("backend.services.signal_recorder", "DEBUG") as cm:
            self.assertIsNone(self.recorder._get_market_regime())
        self.assertTrue(any("market regime" in r.getMessage() for r in cm.records))

    def test_trend_unavailable_is_logged_and_none(self):
        with patch("backend.api.trend.registry.get_engine", side_effect=RuntimeError("cold")), \
             self.assertLogs("backend.services.signal_recorder", "DEBUG") as cm:
            self.assertIsNone(self.recorder._get_trend_signal("AAPL", "1m"))
        self.assertTrue(any("AAPL/1m" in r.getMessage() for r in cm.records))


class TestIngestionStreamSubscription(unittest.TestCase):
    def test_register_symbol_survives_and_logs_a_stream_failure(self):
        from backend.market_data.services.ingestion_service import MarketDataIngestionService

        svc = MarketDataIngestionService(symbols=["AAPL"], timeframes=["1m"])
        with patch("backend.market_data.streaming.webull_stream.get_webull_stream_client",
                   side_effect=RuntimeError("stream down")), \
             self.assertLogs("backend.market_data.services.ingestion_service", "DEBUG") as cm:
            svc.register_symbol("msft")  # must not raise
        self.assertTrue(any("register_symbol" in r.getMessage() and r.exc_info for r in cm.records))


if __name__ == "__main__":
    unittest.main()
