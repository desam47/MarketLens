"""
Tests for SectorEngine — Phase 8 spec.
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from backend.regime.sector_engine import (
    SECTOR_ETFS,
    SECTOR_MAP,
    AlignmentLevel,
    SectorEngine,
    SectorSignal,
)


class TestSectorEngine(unittest.TestCase):
    def setUp(self):
        self.symbol = "AAPL"

    # ------------------------------------------------------------------
    # Mapping coverage
    # ------------------------------------------------------------------

    def test_sector_map_known_symbol(self):
        """AAPL maps to Technology."""
        self.assertEqual(SECTOR_MAP["AAPL"], "Technology")

    def test_sector_map_unknown_symbol(self):
        """An unknown symbol maps to Unknown."""
        self.assertEqual(SECTOR_MAP.get("XYZ123", "Unknown"), "Unknown")

    def test_sector_etf_for_technology(self):
        """Technology → XLK."""
        self.assertEqual(SECTOR_ETFS["Technology"], "XLK")

    def test_sector_etf_for_unknown_sector(self):
        """Unknown sector has no ETF."""
        self.assertIsNone(SECTOR_ETFS.get("Unknown"))

    # ------------------------------------------------------------------
    # Engine init
    # ------------------------------------------------------------------

    def test_engine_init_known_symbol(self):
        """AAPL → sector=Technology, sector_etf=XLK."""
        engine = SectorEngine("AAPL")
        self.assertEqual(engine.sector, "Technology")
        self.assertEqual(engine.sector_etf, "XLK")

    def test_engine_init_unknown_symbol(self):
        """Unknown symbol falls through to sector=Unknown, sector_etf=None."""
        engine = SectorEngine("XYZ123")
        self.assertEqual(engine.sector, "Unknown")
        self.assertIsNone(engine.sector_etf)

    def test_engine_has_3_trend_engines(self):
        """Known symbol: 3 TrendEngines (stock, sector ETF, SPY)."""
        engine = SectorEngine("AAPL")
        self.assertIsNotNone(engine._stock_eng)
        self.assertIsNotNone(engine._sector_eng)
        self.assertIsNotNone(engine._market_eng)

    def test_engine_unknown_sector_no_sector_engine(self):
        """Unknown symbol → no sector_eng (None)."""
        engine = SectorEngine("XYZ123")
        self.assertIsNone(engine._sector_eng)

    # ------------------------------------------------------------------
    # Alignment computation
    # ------------------------------------------------------------------

    def test_compute_alignment_all_uptrend(self):
        """All 3 up → perfect, score 1.0."""
        engine = SectorEngine("AAPL")
        score, level, factors = engine._compute_alignment("uptrend", "uptrend", "uptrend")
        self.assertEqual(score, 1.0)
        self.assertEqual(level, AlignmentLevel.PERFECT.value)

    def test_compute_alignment_all_downtrend(self):
        """All 3 down → perfect, score 1.0."""
        engine = SectorEngine("AAPL")
        score, level, factors = engine._compute_alignment("downtrend", "downtrend", "downtrend")
        self.assertEqual(score, 1.0)
        self.assertEqual(level, AlignmentLevel.PERFECT.value)

    def test_compute_alignment_2_up_1_down(self):
        """2/3 up → majority, score ~0.67."""
        engine = SectorEngine("AAPL")
        score, level, factors = engine._compute_alignment("uptrend", "uptrend", "downtrend")
        self.assertAlmostEqual(score, 0.67, places=2)
        self.assertEqual(level, AlignmentLevel.MAJORITY.value)

    def test_compute_alignment_split(self):
        """1/3 → split, score 0.0."""
        engine = SectorEngine("AAPL")
        score, level, factors = engine._compute_alignment("uptrend", "sideways", "downtrend")
        self.assertEqual(score, 0.0)
        self.assertEqual(level, AlignmentLevel.CONFLICTING.value)

    def test_compute_alignment_insufficient_data(self):
        """2+ unknown → 0.0."""
        engine = SectorEngine("AAPL")
        score, level, factors = engine._compute_alignment("unknown", "unknown", "uptrend")
        self.assertEqual(score, 0.0)

    def test_compute_alignment_perfect_all_strong_uptrend(self):
        """strong_uptrend counts as uptrend."""
        engine = SectorEngine("AAPL")
        score, level, factors = engine._compute_alignment(
            "strong_uptrend", "strong_uptrend", "strong_uptrend"
        )
        self.assertEqual(score, 1.0)

    # ------------------------------------------------------------------
    # Signal
    # ------------------------------------------------------------------

    def test_signal_to_dict(self):
        """Signal serialises to a clean dict."""
        sig = SectorSignal(
            symbol="AAPL",
            sector="Technology",
            sector_etf="XLK",
            stock_trend="uptrend",
            sector_trend="uptrend",
            market_trend="uptrend",
            alignment_score=1.0,
            alignment_level="perfect",
            contributing_factors={"agreement": "all_uptrend"},
            timestamp=datetime(2025, 1, 1),
        )
        d = sig.to_dict()
        self.assertEqual(d["symbol"], "AAPL")
        self.assertEqual(d["alignment_score"], 1.0)
        self.assertIn("timestamp", d)

    def test_unknown_sector_returns_unknown_sector_trend(self):
        """Unknown sector → sector_trend is 'unknown' (no sector engine to track)."""
        engine = SectorEngine("XYZ123")
        # Feed price data — sector_trend stays unknown, stock/market may align
        now = datetime.now()
        for _ in range(3):
            engine.update(price=100.0, volume=1000, timestamp=now)
        sig = engine.get_current_signal()
        # The key invariant: unknown sector → no sector ETF
        self.assertIsNone(sig.sector_etf)
        self.assertEqual(sig.sector, "Unknown")
        self.assertEqual(sig.sector_trend, "unknown")

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def test_signal_history_limit(self):
        """get_signal_history respects limit."""
        engine = SectorEngine("AAPL")
        for _ in range(5):
            engine.get_current_signal()
        history = engine.get_signal_history(limit=3)
        self.assertEqual(len(history), 3)


if __name__ == "__main__":
    unittest.main()
