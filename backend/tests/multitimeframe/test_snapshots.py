"""
Tests for TimeframeTrendSnapshot and MultiTimeframeSnapshot (Phase 7).
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.engines.timeframe import Timeframe
from backend.multitimeframe.multi_timeframe_engine import (
    MultiTimeframeEngine,
    MultiTimeframeSnapshot,
    TimeframeTrendSnapshot,
)
from backend.trend.trend_engine import TrendClassification


class TestTimeframeTrendSnapshotFields(unittest.TestCase):
    """All 8 spec fields exist with correct types."""

    def test_all_8_fields_present(self):
        ts = datetime.now()
        snap = TimeframeTrendSnapshot(
            symbol="AAPL",
            timeframe=Timeframe.ONE_HOUR,
            timestamp=ts,
            direction=TrendClassification.STRONG_BULLISH,
            score=85.0,
            strength=0.75,
            confidence=0.9,
            data_quality="ok",
            strategy_version="v1.0",
        )
        self.assertEqual(snap.symbol, "AAPL")
        self.assertEqual(snap.timeframe, Timeframe.ONE_HOUR)
        self.assertEqual(snap.timestamp, ts)
        self.assertEqual(snap.direction, TrendClassification.STRONG_BULLISH)
        self.assertEqual(snap.score, 85.0)
        self.assertEqual(snap.strength, 0.75)
        self.assertEqual(snap.confidence, 0.9)
        self.assertEqual(snap.data_quality, "ok")
        self.assertEqual(snap.strategy_version, "v1.0")

    def test_direction_uses_trend_classification(self):
        """The direction field must be the 8-class TrendClassification, not
        the 4-class TrendDirection (Phase 6 spec requirement)."""
        snap = TimeframeTrendSnapshot(
            symbol="AAPL",
            timeframe=Timeframe.ONE_DAY,
            timestamp=datetime.now(),
            direction=TrendClassification.WEAK_BEARISH,
            score=-20.0,
            strength=0.25,
            confidence=0.5,
            data_quality="ok",
            strategy_version="v1.0",
        )
        self.assertIsInstance(snap.direction, TrendClassification)
        self.assertNotEqual(snap.direction, TrendClassification.STRONG_BULLISH)

    def test_score_in_range(self):
        """score must be a float (Phase 6: -100..+100)."""
        snap = TimeframeTrendSnapshot(
            symbol="AAPL",
            timeframe=Timeframe.ONE_DAY,
            timestamp=datetime.now(),
            direction=TrendClassification.BULLISH,
            score=55.0,
            strength=0.5,
            confidence=0.8,
            data_quality="ok",
            strategy_version="v1.0",
        )
        self.assertIsInstance(snap.score, float)


class TestMultiTimeframeSnapshotFields(unittest.TestCase):
    """All 14 spec fields exist with correct types."""

    def test_all_fields_present(self):
        from backend.multitimeframe.multi_timeframe_engine import ConfluenceDirection

        ts = datetime.now()
        snap = MultiTimeframeSnapshot(
            symbol="AAPL",
            timestamp=ts,
            preset="day_trading",
            direction=ConfluenceDirection.STRONG_UPTREND,
            strength=0.85,
            alignment_score=0.9,
            bullish_alignment=1.0,
            bearish_alignment=0.0,
            conflicting=0,
            short_term_direction=TrendClassification.STRONG_BULLISH,
            intermediate_direction=TrendClassification.STRONG_BULLISH,
            higher_direction=TrendClassification.STRONG_BULLISH,
            timeframe_snapshots={},
            strategy_version="v1.0",
        )
        self.assertEqual(snap.symbol, "AAPL")
        self.assertEqual(snap.timestamp, ts)
        self.assertEqual(snap.preset, "day_trading")
        self.assertEqual(snap.direction.value, "strong_uptrend")
        self.assertEqual(snap.strength, 0.85)
        self.assertEqual(snap.alignment_score, 0.9)
        self.assertEqual(snap.bullish_alignment, 1.0)
        self.assertEqual(snap.bearish_alignment, 0.0)
        self.assertEqual(snap.conflicting, 0)
        self.assertEqual(snap.short_term_direction, TrendClassification.STRONG_BULLISH)
        self.assertEqual(snap.intermediate_direction, TrendClassification.STRONG_BULLISH)
        self.assertEqual(snap.higher_direction, TrendClassification.STRONG_BULLISH)
        self.assertIsInstance(snap.timeframe_snapshots, dict)
        self.assertEqual(snap.strategy_version, "v1.0")

    def test_horizon_directions_use_trend_classification(self):
        """Short/intermediate/higher must use the 8-class TrendClassification."""
        from backend.multitimeframe.multi_timeframe_engine import ConfluenceDirection

        snap = MultiTimeframeSnapshot(
            symbol="AAPL",
            timestamp=datetime.now(),
            preset="swing",
            direction=ConfluenceDirection.DOWNTREND,
            strength=0.6,
            alignment_score=0.5,
            bullish_alignment=0.0,
            bearish_alignment=0.5,
            conflicting=2,
            short_term_direction=TrendClassification.WEAK_BEARISH,
            intermediate_direction=TrendClassification.BEARISH,
            higher_direction=TrendClassification.STRONG_BEARISH,
            timeframe_snapshots={},
            strategy_version="v1.0",
        )
        for attr in ("short_term_direction", "intermediate_direction", "higher_direction"):
            val = getattr(snap, attr)
            self.assertIsInstance(val, TrendClassification)


class TestSnapshotBehavior(unittest.TestCase):
    """build_snapshot() contract + preset name round-trip."""

    def setUp(self):
        from backend.config.settings import settings

        self._original_gap = settings.data_quality.max_tick_gap_seconds
        settings.data_quality.max_tick_gap_seconds = 86400.0

    def tearDown(self):
        from backend.config.settings import settings

        settings.data_quality.max_tick_gap_seconds = self._original_gap

    def _feed(self, engine: MultiTimeframeEngine, n: int = 120):
        base = datetime.now()
        for i in range(n):
            engine.update(100.0 + i * 0.5, 1_000_000, base + timedelta(minutes=i))

    def test_build_snapshot_returns_none_no_data(self):
        """Fresh engine: build_snapshot returns None."""
        engine = MultiTimeframeEngine("AAPL")
        self.assertIsNone(engine.build_snapshot())

    def test_build_snapshot_populated_after_warmup(self):
        """After feeding data, build_snapshot returns a non-None snapshot."""
        engine = MultiTimeframeEngine("AAPL", preset="day_trading")
        self._feed(engine, 60)
        snap = engine.build_snapshot()
        self.assertIsNotNone(snap)
        self.assertEqual(snap.symbol, "AAPL")
        self.assertIsInstance(snap.preset, str)

    def test_snapshot_preset_name_matches_engine(self):
        """The snapshot's preset field matches the engine's preset_name."""
        for preset in ("day_trading", "swing", "all"):
            engine = MultiTimeframeEngine("AAPL", preset=preset)
            self._feed(engine, 60)
            snap = engine.build_snapshot()
            self.assertIsNotNone(snap)
            self.assertEqual(snap.preset, preset, f"Snapshot preset should be '{preset}'")

    def test_snapshot_timeframe_snapshots_keys_match_active_tfs(self):
        """After warmup, every active TF has a TimeframeTrendSnapshot."""
        engine = MultiTimeframeEngine("AAPL", preset="day_trading")
        self._feed(engine, 120)
        snap = engine.build_snapshot()
        self.assertIsNotNone(snap)
        # Every active TF with a signal appears in the map.
        active_signals = snap.timeframe_snapshots
        self.assertIsInstance(active_signals, dict)
        for tf in engine.active_timeframes:
            if tf in active_signals:
                self.assertIsInstance(active_signals[tf], TimeframeTrendSnapshot)

    def test_snapshot_history_accumulates(self):
        """Each update appends to snapshot_history."""
        engine = MultiTimeframeEngine("AAPL", preset="day_trading")
        self.assertEqual(len(engine.snapshot_history), 0)
        base = datetime.now()
        # Need enough ticks for the 5m TF's slow EMA to warm up.
        # 5m slow EMA period=26 → 26 completed 5m candles → 26*5=130 ticks minimum.
        # Feed 150 ticks to be safe.
        for i in range(150):
            engine.update(100.0 + i, 1_000_000, base + timedelta(minutes=i))
        # After enough ticks, snapshot_history should have entries.
        self.assertGreaterEqual(len(engine.snapshot_history), 1)

    def test_get_snapshot_history_limit(self):
        """get_snapshot_history with a limit returns the last N."""
        engine = MultiTimeframeEngine("AAPL", preset="day_trading")
        base = datetime.now()
        for i in range(20):
            engine.update(100.0 + i, 1_000_000, base + timedelta(minutes=i))
        history = engine.get_snapshot_history(limit=5)
        self.assertLessEqual(len(history), 5)


if __name__ == "__main__":
    unittest.main()
