"""
Tests for the RankingEngine (backend.scanner.ranking).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../'))

from backend.scanner.filters import DailyBullish
from backend.scanner.ranking import (
    RankedEntry,
    RankingEngine,
    default_ranking_engine,
)
from backend.scanner.scanner import ScanResult


def _result(
    symbol: str = "AAPL",
    *,
    score: float = 50.0,
    trend_strength: float = 0.0,
    momentum: float = 0.0,
    rsi: float | None = None,
    macd: float | None = None,
    adx: float | None = None,
    trend_signals: dict | None = None,
    change_pct: float | None = None,
) -> ScanResult:
    """Build a ScanResult with convenient kwargs.

    ``score`` sets ``_total`` in the scores dict (used by non-directional
    ranking categories that read it directly). ``strongest_bullish`` /
    ``strongest_bearish`` rank by live price change (``change_pct``) —
    set that explicitly for those. ``biggest_improvement`` /
    ``biggest_deterioration`` still rank by the momentum/macd/rsi-based
    directional score — set ``momentum``/``macd`` for those.

    Note: rsi, macd, adx are added to BOTH ``indicator_values`` (so
    category builders that read indicators work) and ``scores`` (so the
    directional ranking has them). In production, ``ScanResult`` carries
    them in both places too.
    """
    r = ScanResult(symbol, __import__("datetime").datetime.now())
    if trend_strength != 0.0:
        r.add_score("trend_strength", trend_strength)
    if momentum != 0.0:
        r.add_score("momentum", momentum)
    if rsi is not None:
        r.add_indicator("rsi", rsi)
        r.add_score("rsi", rsi)
    if macd is not None:
        r.add_indicator("macd", macd)
        r.add_score("macd", macd)
    if adx is not None:
        r.add_indicator("adx", adx)
        r.add_score("adx", adx)
    if trend_signals:
        r.trend_signals.update(trend_signals)
    if change_pct is not None:
        r.change_pct = change_pct
    # Non-directional categories use _total directly.
    r.scores["_total"] = score
    return r


class TestRankedEntry(unittest.TestCase):

    def test_ranked_entry(self):
        e = RankedEntry(symbol="AAPL", score=80.5, rank=1, metrics={"foo": 1.0})
        self.assertEqual(e.symbol, "AAPL")
        self.assertEqual(e.score, 80.5)
        self.assertEqual(e.rank, 1)
        self.assertEqual(e.metrics["foo"], 1.0)


class TestRankingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RankingEngine()

    def test_categories_defined(self):
        names = {c["name"] for c in self.engine.CATEGORIES}
        expected = {
            "strongest_bullish",
            "strongest_bearish",
            "strongest_momentum",
            "biggest_improvement",
            "biggest_deterioration",
            "best_mtf_alignment",
            "strongest_relative_strength",
        }
        self.assertEqual(names, expected)

    def test_rank_produces_all_categories(self):
        results = [_result("AAPL")]
        out = self.engine.rank(results)
        names = set(out.keys())
        self.assertEqual(names, {c["name"] for c in self.engine.CATEGORIES})

    def test_strongest_bullish_top_n(self):
        results = [
            # strongest_bullish/bearish rank by live price change_pct now,
            # not the momentum/macd directional score — see
            # _build_strongest_bullish's docstring.
            _result("LOW", change_pct=1.0),
            _result("MID", change_pct=5.0),
            _result("HIGH", change_pct=12.0),
        ]
        out = self.engine.rank(results, top_n=2)
        bullish = out["strongest_bullish"]
        self.assertEqual(len(bullish.entries), 2)
        self.assertEqual(bullish.entries[0].symbol, "HIGH")
        self.assertEqual(bullish.entries[0].rank, 1)
        self.assertEqual(bullish.entries[1].symbol, "MID")
        self.assertEqual(bullish.entries[1].rank, 2)

    def test_strongest_bearish_lowest(self):
        results = [
            _result("BULL", change_pct=9.0),
            _result("NEUT", change_pct=-0.5),
            _result("BEAR", change_pct=-11.0),
        ]
        out = self.engine.rank(results, top_n=2)
        bearish = out["strongest_bearish"]
        self.assertEqual(bearish.entries[0].symbol, "BEAR")
        self.assertEqual(bearish.entries[1].symbol, "NEUT")

    def test_strongest_bullish_bearish_exclude_missing_change_pct(self):
        """A symbol with no live quote (change_pct=None) can't be placed
        on either side of a price-direction ranking."""
        results = [_result("NOQUOTE", change_pct=None)]
        out = self.engine.rank(results)
        self.assertEqual(out["strongest_bullish"].entries, [])
        self.assertEqual(out["strongest_bearish"].entries, [])

    def test_strongest_bullish_excludes_symbols_actually_down(self):
        """Regression for a live bug (2026-09-16): with top_n larger than
        the number of symbols genuinely up, strongest_bullish had no
        change_pct <= 0 guard (unlike strongest_bearish, which already
        excludes change_pct >= 0), so it padded out the requested top_n
        with negative-change symbols — "Top Bullish" showing tickers
        that were actually down. A symbol that's flat or down must never
        appear on the bullish side, regardless of top_n."""
        results = [
            _result("UP", change_pct=3.0),
            _result("FLAT", change_pct=0.0),
            _result("DOWN", change_pct=-2.0),
        ]
        out = self.engine.rank(results, top_n=10)
        bullish_symbols = {e.symbol for e in out["strongest_bullish"].entries}
        self.assertEqual(bullish_symbols, {"UP"})
        self.assertEqual(out["strongest_bullish"].total_eligible, 1)

    def test_strongest_bullish_can_disagree_with_directional_score(self):
        """Regression for a live case (CTNT, 2026-09-16): price crashing
        (change_pct very negative) while the momentum/RSI directional
        score reads bullish (deeply oversold RSI + a lagging-positive,
        clamped MACD). strongest_bullish must rank by actual price
        direction, not the contrarian directional score — so a genuinely
        rising symbol must outrank the crashing one on the bullish side,
        and the crashing one must top the bearish side."""
        crashing_but_directionally_bullish = _result(
            "CTNT", change_pct=-31.0, momentum=50.0, rsi=38.4,
        )
        actually_rising = _result("RISING", change_pct=5.0)
        out = self.engine.rank([crashing_but_directionally_bullish, actually_rising], top_n=1)
        self.assertEqual(out["strongest_bullish"].entries[0].symbol, "RISING")
        self.assertEqual(out["strongest_bearish"].entries[0].symbol, "CTNT")

    def test_directional_ranking_ignores_magnitude_only_scores(self):
        # biggest_improvement/biggest_deterioration are the categories
        # that still rank by the momentum/macd/rsi directional score
        # (strongest_bullish/bearish now rank by live change_pct — see
        # above). Magnitude-only scores (trend_strength, adx, volatility,
        # volume) must not wash out the directional signal: BULL has a
        # strong trend_strength AND positive momentum; BEAR has the same
        # strong trend_strength but negative momentum.
        results = [
            _result("BULL", score=80.0, momentum=40.0, macd=1.0,
                    trend_strength=80.0, adx=50.0),
            _result("BEAR", score=80.0, momentum=-40.0, macd=-1.0,
                    trend_strength=80.0, adx=50.0),
        ]
        out = self.engine.rank(results)
        improvement = {e.symbol: e.score for e in out["biggest_improvement"].entries}
        deterioration = {e.symbol: e.score for e in out["biggest_deterioration"].entries}
        self.assertIn("BULL", improvement)
        self.assertIn("BEAR", deterioration)

    def test_strongest_momentum(self):
        results = [
            _result("WEAK", momentum=20.0, macd=0.5),
            _result("STRONG", momentum=90.0, macd=5.0),
        ]
        out = self.engine.rank(results)
        mom = out["strongest_momentum"]
        self.assertEqual(mom.entries[0].symbol, "STRONG")

    def test_biggest_improvement_adx_weighted(self):
        results = [
            _result("LOW", trend_strength=20.0, adx=15.0, momentum=1.0, macd=0.1),
            _result("HIGH", trend_strength=85.0, adx=45.0, momentum=10.0, macd=1.0),
        ]
        out = self.engine.rank(results)
        imp = out["biggest_improvement"]
        self.assertEqual(imp.entries[0].symbol, "HIGH")

    def test_best_mtf_alignment(self):
        results = [
            _result("ALL_UP", trend_signals={
                "ONE_HOUR": {"direction": "uptrend", "confidence": 0.8},
                "FOUR_HOUR": {"direction": "uptrend", "confidence": 0.8},
                "ONE_DAY": {"direction": "uptrend", "confidence": 0.8},
            }),
            _result("MIXED", trend_signals={
                "ONE_HOUR": {"direction": "uptrend", "confidence": 0.8},
                "FOUR_HOUR": {"direction": "downtrend", "confidence": 0.8},
            }),
        ]
        out = self.engine.rank(results)
        align = out["best_mtf_alignment"]
        self.assertEqual(align.entries[0].symbol, "ALL_UP")

    def test_filter_restricts_candidates(self):
        results = [
            _result("BULL", score=90.0, change_pct=8.0, trend_signals={
                "ONE_DAY": {"direction": "uptrend", "confidence": 0.8}
            }),
            _result("BEAR", score=10.0, change_pct=-8.0),
        ]
        bull_filter = DailyBullish()
        out = self.engine.rank(results, filter=bull_filter)
        # After filter: only BULL qualifies → strongest_bullish has 1 entry
        self.assertEqual(out["strongest_bullish"].total_eligible, 1)
        self.assertEqual(out["strongest_bullish"].entries[0].symbol, "BULL")

    def test_rank_one(self):
        results = [
            _result("AAPL", score=90.0, change_pct=6.0),
            _result("MSFT", score=10.0, change_pct=1.0),
        ]
        r = self.engine.rank_one("strongest_bullish", results)
        self.assertIsNotNone(r)
        self.assertEqual(r.entries[0].symbol, "AAPL")

    def test_rank_one_unknown_category_returns_none(self):
        r = self.engine.rank_one("does_not_exist", [_result("AAPL")])
        self.assertIsNone(r)

    def test_empty_results(self):
        out = self.engine.rank([])
        for ranking in out.values():
            self.assertEqual(ranking.total_eligible, 0)
            self.assertEqual(ranking.entries, [])

    def test_top_n_zero_returns_empty(self):
        results = [_result("AAPL", score=80.0)]
        out = self.engine.rank(results, top_n=0)
        for ranking in out.values():
            self.assertEqual(ranking.entries, [])

    def test_named_ranking_to_dict(self):
        from backend.scanner.ranking import NamedRanking
        ranking = NamedRanking(
            name="strongest_bullish",
            label="Strongest Bullish",
            description="Top by score",
            entries=[RankedEntry(symbol="AAPL", score=88.8, rank=1, metrics={})],
            total_eligible=1,
        )
        d = ranking.to_dict()
        self.assertEqual(d["name"], "strongest_bullish")
        self.assertEqual(d["entries"][0]["symbol"], "AAPL")
        # float rounding
        self.assertEqual(d["entries"][0]["score"], 88.8)


class TestDefaultRankingEngine(unittest.TestCase):

    def test_default_instance_exists(self):
        self.assertIsInstance(default_ranking_engine, RankingEngine)


if __name__ == "__main__":
    unittest.main()
