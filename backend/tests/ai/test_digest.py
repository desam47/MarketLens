"""
Tests for backend.ai.digest — the daily/session AI digest's
aggregation logic (build_digest_payload / narrate_digest /
generate_and_store_digest).
"""
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from backend.ai.digest import build_digest_payload, narrate_digest
from backend.ai.prompt import DigestNarrative
from backend.ai.provider import AIResponse
from backend.scanner.scanner import ScanResult


def _fake_result(symbol: str, score: float, signals: list[str] | None = None, rsi: float | None = None) -> ScanResult:
    r = ScanResult(symbol, datetime.now(UTC))
    r.quote = MagicMock()
    r.quote.price = 100.0
    r.indicator_values = {"rsi": rsi} if rsi is not None else {}
    r.signals = signals or []
    r._score = score
    r.calculate_signed_total_score = MagicMock(return_value=score)
    return r


class TestBuildDigestPayload(unittest.TestCase):

    @patch("backend.ai.digest.analyze_symbol")
    @patch("backend.nl_search.executor._resolve_watchlist_symbols")
    @patch("backend.scanner.scanner.market_scanner")
    @patch("backend.api.market_context.router.get_engine")
    def test_picks_top_n_movers_by_signed_score(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        mock_get_engine.return_value = MagicMock(
            get_current_context=MagicMock(return_value=None)
        )
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
    def test_empty_watchlist_degrades_without_exception(
        self, mock_get_engine, mock_scanner, mock_resolve, mock_analyze
    ):
        mock_get_engine.return_value = MagicMock(
            get_current_context=MagicMock(return_value=None)
        )
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
        mock_get_engine.return_value = MagicMock(
            get_current_context=MagicMock(return_value=None)
        )
        mock_resolve.return_value = ["A", "B", "C"]
        mock_scanner.scan_results = {
            "A": _fake_result("A", 10.0, signals=["RSI_OVERSOLD", "MULTI_TIMEFRAME_BULLISH"], rsi=25.0),
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
        mock_get_engine.return_value = MagicMock(
            get_current_context=MagicMock(return_value=None)
        )
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
        mock_get_engine.return_value = MagicMock(
            get_current_context=MagicMock(return_value=None)
        )
        mock_resolve.return_value = ["A"]
        mock_scanner.scan_results = {"A": _fake_result("A", 90.0)}
        mock_analyze.return_value = MagicMock(is_uncertain=True, summary="")

        payload = build_digest_payload()

        self.assertNotIn("blurb", payload["movers"]["top_bullish"][0])


class TestNarrateDigest(unittest.TestCase):

    @patch("backend.ai.digest.ai_manager")
    def test_ai_off_falls_back_to_plain_narrative(self, mock_ai):
        mock_ai.is_available.return_value = False
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
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = AIResponse(
            text='```json\n{"narrative": "Markets are calm.", "headline_movers": ["A", "B"]}\n```',
            provider="ollama", model="llama3.2",
        )
        payload = {"market_regime": {}, "movers": {"top_bullish": [], "top_bearish": []}}
        result = narrate_digest(payload)
        self.assertEqual(result.narrative, "Markets are calm.")
        self.assertEqual(result.headline_movers, ["A", "B"])

    @patch("backend.ai.digest.ai_manager")
    def test_malformed_ai_reply_falls_back_gracefully(self, mock_ai):
        mock_ai.is_available.return_value = True
        mock_ai.complete.return_value = AIResponse(
            text="not json", provider="ollama", model="llama3.2",
        )
        payload = {
            "market_regime": {"regime": "NEUTRAL"},
            "movers": {"top_bullish": [], "top_bearish": []},
        }
        result = narrate_digest(payload)
        self.assertIsInstance(result, DigestNarrative)
        self.assertIn("NEUTRAL", result.narrative)


if __name__ == "__main__":
    unittest.main()
