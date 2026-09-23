"""
Tests for backend.ai.digest — the daily/session AI digest's
aggregation logic (build_digest_payload / narrate_digest /
generate_and_store_digest).
"""

import threading
import time
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from backend.ai.digest import build_digest_payload, narrate_digest, summary_metadata
from backend.ai.prompt import DigestNarrative
from backend.ai.provider import AIResponse
from backend.scanner.scanner import ScanResult


def _fake_result(
    symbol: str, score: float, signals: list[str] | None = None, rsi: float | None = None
) -> ScanResult:
    """``score`` doubles as both the (legacy) directional score and the
    live change_pct — build_digest_payload's movers now rank by
    change_pct, so every existing caller's sign/magnitude still drives
    bullish/bearish placement the same way it always did."""
    r = ScanResult(symbol, datetime.now(UTC))
    r.quote = MagicMock()
    r.quote.price = 100.0
    r.change_pct = score
    r.indicator_values = {"rsi": rsi} if rsi is not None else {}
    r.signals = signals or []
    r._score = score
    r.calculate_signed_total_score = MagicMock(return_value=score)
    return r


class TestBuildDigestPayload(unittest.TestCase):
    def test_summary_metadata_has_explicit_window_and_dedupe_key(self):
        metadata = summary_metadata("weekly", now=datetime(2026, 9, 25, 16, 30))

        self.assertEqual(metadata["kind"], "weekly_review")
        self.assertEqual(metadata["period_start"], "2026-09-21T00:00:00-04:00")
        self.assertEqual(metadata["period_end"], "2026-09-25T16:30:00-04:00")
        self.assertEqual(metadata["dedupe_key"], "weekly:2026-09-21")

    @patch("backend.ai.digest.analyze_symbol")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_picks_top_n_movers_by_signed_score(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        symbols = ["A", "B", "C", "D"]
        mock_resolve.return_value = symbols
        results = {
            "A": _fake_result("A", 80.0),
            "B": _fake_result("B", -60.0),
            "C": _fake_result("C", 30.0),
            "D": _fake_result("D", -10.0),
        }
        mock_scanner.scan_results = results
        mock_analyze.return_value = MagicMock(is_uncertain=True)

        payload = build_digest_payload()

        bullish_symbols = [m["symbol"] for m in payload["movers"]["top_bullish"]]
        bearish_symbols = [m["symbol"] for m in payload["movers"]["top_bearish"]]
        self.assertEqual(bullish_symbols[0], "A")
        self.assertIn("B", bearish_symbols)
        self.assertEqual(payload["watchlist_size"], 4)

    @patch("backend.ai.digest.analyze_symbol")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_movers_rank_by_change_pct_not_directional_score(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        """Regression for a live bug (2026-09-16, CTNT): movers used to
        rank by calculate_signed_total_score (a momentum/RSI contrarian
        composite) instead of live price change — a crashing stock could
        show up as a "leading bullish mover" in the AI narrative. A
        symbol with a bullish-looking directional score but a genuinely
        negative change_pct must land on the bearish side, and its
        reported change_pct (not the old score) is what's exposed."""
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        symbols = ["CRASHING", "RISING"]
        mock_resolve.return_value = symbols
        crashing = _fake_result("CRASHING", score=44.0)  # bullish-looking score...
        crashing.change_pct = -35.0  # ...but price is actually crashing.
        rising = _fake_result("RISING", score=-10.0)  # bearish-looking score...
        rising.change_pct = 5.0  # ...but price is actually up.
        mock_scanner.scan_results = {"CRASHING": crashing, "RISING": rising}
        mock_analyze.return_value = MagicMock(is_uncertain=True)

        payload = build_digest_payload()

        bullish_symbols = [m["symbol"] for m in payload["movers"]["top_bullish"]]
        bearish_symbols = [m["symbol"] for m in payload["movers"]["top_bearish"]]
        self.assertEqual(bullish_symbols, ["RISING"])
        self.assertEqual(bearish_symbols, ["CRASHING"])
        crashing_entry = payload["movers"]["top_bearish"][0]
        self.assertEqual(crashing_entry["change_pct"], -35.0)

    @patch("backend.ai.digest.analyze_symbol")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_empty_watchlist_degrades_without_exception(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        mock_resolve.return_value = []
        mock_scanner.scan_results = {}

        payload = build_digest_payload()

        self.assertEqual(payload["watchlist_size"], 0)
        self.assertEqual(payload["movers"]["top_bullish"], [])
        self.assertEqual(payload["movers"]["top_bearish"], [])
        mock_analyze.assert_not_called()

    @patch("backend.ai.digest.analyze_symbol")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_regime_engine_failure_degrades_to_empty_dict(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        mock_get_engine.side_effect = RuntimeError("regime engine down")
        mock_resolve.return_value = ["A"]
        mock_scanner.scan_results = {"A": _fake_result("A", 50.0)}
        mock_analyze.return_value = MagicMock(is_uncertain=True)

        payload = build_digest_payload()

        self.assertEqual(payload["market_regime"], {})
        # The rest of the payload must still be built.
        self.assertEqual(payload["watchlist_size"], 1)

    @patch("backend.ai.digest.analyze_symbol")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_rsi_extremes_and_mtf_counts(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        mock_resolve.return_value = ["A", "B", "C"]
        mock_scanner.scan_results = {
            "A": _fake_result(
                "A", 10.0, signals=["RSI_OVERSOLD", "MULTI_TIMEFRAME_BULLISH"], rsi=25.0
            ),
            "B": _fake_result("B", -10.0, signals=["MULTI_TIMEFRAME_BEARISH"], rsi=55.0),
            "C": _fake_result("C", 5.0, rsi=50.0),
        }
        mock_analyze.return_value = MagicMock(is_uncertain=True)

        payload = build_digest_payload()

        self.assertEqual(len(payload["rsi_extremes"]), 1)
        self.assertEqual(payload["rsi_extremes"][0]["symbol"], "A")
        self.assertEqual(payload["mtf_alignment_counts"], {"bullish": 1, "bearish": 1})

    @patch("backend.ai.digest.analyze_symbol")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_mover_blurb_only_added_when_analysis_is_confident(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        mock_resolve.return_value = ["A"]
        mock_scanner.scan_results = {"A": _fake_result("A", 90.0)}
        mock_analyze.return_value = MagicMock(is_uncertain=False, summary="Strong uptrend.")

        payload = build_digest_payload()

        self.assertEqual(payload["movers"]["top_bullish"][0]["blurb"], "Strong uptrend.")

    @patch("backend.ai.digest.analyze_symbol")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_mover_blurb_omitted_when_analysis_uncertain(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        mock_resolve.return_value = ["A"]
        mock_scanner.scan_results = {"A": _fake_result("A", 90.0)}
        mock_analyze.return_value = MagicMock(is_uncertain=True, summary="")

        payload = build_digest_payload()

        self.assertNotIn("blurb", payload["movers"]["top_bullish"][0])


class TestNarrateDigest(unittest.TestCase):
    @patch("backend.ai.digest.ai_manager")
    def test_ai_off_falls_back_to_plain_narrative(self, mock_ai):
        mock_ai.is_available = AsyncMock(return_value=False)
        payload = {
            "market_regime": {"regime": "RISK_ON"},
            "movers": {"top_bullish": [{"symbol": "A"}], "top_bearish": []},
        }
        result = narrate_digest(payload)
        self.assertIsInstance(result, DigestNarrative)
        self.assertIn("RISK_ON", result.narrative)
        self.assertIn("A", result.headline_movers)

    @patch("backend.ai.digest.ai_manager")
    def test_successful_ai_reply_parsed(self, mock_ai):
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.complete = AsyncMock(
            return_value=AIResponse(
                text='```json\n{"narrative": "Markets are calm.", "headline_movers": ["A", "B"]}\n```',
                provider="ollama",
                model="llama3.2",
            )
        )
        payload = {"market_regime": {}, "movers": {"top_bullish": [], "top_bearish": []}}
        result = narrate_digest(payload)
        self.assertEqual(result.narrative, "Markets are calm.")
        self.assertEqual(result.headline_movers, ["A", "B"])

    @patch("backend.ai.digest.ai_manager")
    def test_malformed_ai_reply_falls_back_gracefully(self, mock_ai):
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.complete = AsyncMock(
            return_value=AIResponse(
                text="not json",
                provider="ollama",
                model="llama3.2",
            )
        )
        payload = {
            "market_regime": {"regime": "NEUTRAL"},
            "movers": {"top_bullish": [], "top_bearish": []},
        }
        result = narrate_digest(payload)
        self.assertIsInstance(result, DigestNarrative)
        self.assertIn("NEUTRAL", result.narrative)


# --- O5: parallel mover analysis ------------------------------------


class TestDigestParallelMoverAnalysis(unittest.TestCase):
    """O5: verify parallelized mover analysis with a bounded thread pool."""

    @patch("backend.ai.digest.analyze_symbol", new_callable=MagicMock)
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_bounded_concurrency_max_four_workers(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        # 10 symbols → 5 bullish + 5 bearish = 10 movers;
        # max_workers = min(4, 10) = 4 → at most 4 concurrent analyses.
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        symbols = [f"S{i}" for i in range(10)]
        mock_resolve.return_value = symbols
        results = {
            s: _fake_result(s, 90.0 - i * 10) if i < 5 else _fake_result(s, -90.0 + (i - 5) * 10)
            for i, s in enumerate(symbols)
        }
        mock_scanner.scan_results = results
        mock_scanner.scan_symbols_async = AsyncMock()

        active = 0
        max_active = 0
        lock = threading.Lock()

        def counting_analyze(symbol, **kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.1)
            with lock:
                active -= 1
            return MagicMock(is_uncertain=False, summary=f"Analysis for {symbol}")

        mock_analyze.side_effect = counting_analyze

        build_digest_payload()

        self.assertLessEqual(max_active, 4)

    @patch("backend.ai.digest.analyze_symbol", new_callable=MagicMock)
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_ordered_output_preserved_despite_different_completion_times(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        # A=90 (slowest), B=80, C=70 (fastest) — ranked order must
        # survive even though C finishes before A.
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        mock_resolve.return_value = ["A", "B", "C"]
        results = {
            "A": _fake_result("A", 90.0),
            "B": _fake_result("B", 80.0),
            "C": _fake_result("C", 70.0),
        }
        mock_scanner.scan_results = results
        mock_scanner.scan_symbols_async = AsyncMock()

        delays = {"A": 0.15, "B": 0.05, "C": 0.01}

        def delayed_analyze(symbol, **kwargs):
            time.sleep(delays[symbol])
            return MagicMock(is_uncertain=False, summary=f"Analysis for {symbol}")

        mock_analyze.side_effect = delayed_analyze

        payload = build_digest_payload()

        self.assertEqual(
            [m["symbol"] for m in payload["movers"]["top_bullish"]],
            ["A", "B", "C"],
        )

    @patch("backend.ai.digest.analyze_symbol", new_callable=MagicMock)
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_per_mover_failure_isolation(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        # One symbol's analysis crashes; the other two must still
        # produce blurbs. _safe_call swallows the analyze_symbol error
        # inside _mover_dict, so future.result() returns normally.
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        mock_resolve.return_value = ["A", "B", "C"]
        results = {
            "A": _fake_result("A", 90.0),
            "B": _fake_result("B", 80.0),
            "C": _fake_result("C", 70.0),
        }
        mock_scanner.scan_results = results
        mock_scanner.scan_symbols_async = AsyncMock()

        def failing_analyze(symbol, **kwargs):
            if symbol == "B":
                raise RuntimeError("analysis crashed")
            return MagicMock(is_uncertain=False, summary=f"Analysis for {symbol}")

        mock_analyze.side_effect = failing_analyze

        payload = build_digest_payload()

        bullish = payload["movers"]["top_bullish"]
        self.assertEqual([m["symbol"] for m in bullish], ["A", "B", "C"])
        self.assertIn("blurb", bullish[0])  # A
        self.assertNotIn("blurb", bullish[1])  # B — degraded, no blurb
        self.assertIn("blurb", bullish[2])  # C

    @patch("backend.ai.digest.analyze_symbol", new_callable=MagicMock)
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_empty_mover_lists_no_executor_or_analysis(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        mock_resolve.return_value = []
        mock_scanner.scan_results = {}
        mock_scanner.scan_symbols_async = AsyncMock()

        build_digest_payload()

        mock_analyze.assert_not_called()

    @patch("backend.ai.digest.analyze_symbol", new_callable=MagicMock)
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_parallelism_proves_faster_than_sequential(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        # 8 movers × 0.1s each: sequential ≈ 0.8s, 4-worker parallel ≈ 0.2s.
        mock_get_engine.return_value = MagicMock(get_current_context=MagicMock(return_value=None))
        symbols = [f"S{i}" for i in range(8)]
        mock_resolve.return_value = symbols
        results = {
            s: _fake_result(s, 90.0 - i * 10) if i < 4 else _fake_result(s, -90.0 + (i - 4) * 10)
            for i, s in enumerate(symbols)
        }
        mock_scanner.scan_results = results
        mock_scanner.scan_symbols_async = AsyncMock()

        def slow_analyze(symbol, **kwargs):
            time.sleep(0.1)
            return MagicMock(is_uncertain=False, summary=f"Analysis for {symbol}")

        mock_analyze.side_effect = slow_analyze

        start = time.monotonic()
        build_digest_payload()
        elapsed = time.monotonic() - start

        # 2 batches × 0.1s ≈ 0.2s; sequential would be 0.8s.
        self.assertLess(elapsed, 0.6)


if __name__ == "__main__":
    unittest.main()
