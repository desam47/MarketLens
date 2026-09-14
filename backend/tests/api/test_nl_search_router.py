"""Tests for the NL search API endpoint."""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.models.market_data import DataStatus, Quote
from backend.scanner.scanner import ScanResult

client = TestClient(app)


def _make_quote(symbol: str = "AAPL", price: float = 150.0) -> Quote:
    return Quote(
        symbol=symbol,
        price=price,
        timestamp=datetime(2026, 1, 1),
        provider="test",
        data_status=DataStatus.DELAYED,
        volume=1_000_000,
        bid=price - 0.5,
        ask=price + 0.5,
    )


def _make_result(
    symbol: str = "AAPL",
    indicators: dict | None = None,
    scores: dict | None = None,
    signals: list[str] | None = None,
) -> ScanResult:
    r = ScanResult(symbol, datetime(2026, 1, 1))
    r.quote = _make_quote(symbol)
    r.indicator_values = indicators or {
        "price": 150.0, "volume": 1_000_000, "rsi": 55.0,
        "macd": 1.2, "adx": 22.0,
    }
    r.scores = scores or {"momentum": 60.0, "volume": 80.0, "trend_strength": 75.0}
    r.signals = list(signals or ["RSI_OVERSOLD"])
    r.trend_signals = {
        "ONE_DAY": {"direction": "uptrend", "confidence": 0.8},
        "ONE_HOUR": {"direction": "uptrend", "confidence": 0.6},
    }
    return r


class TestNLSearchEndpoint(unittest.TestCase):

    @patch("backend.api.nl_search.router.ai_manager")
    @patch("backend.api.nl_search.router.execute_query")
    @patch("backend.nl_search.parser.ai_manager")
    def test_rule_based_query_returns_200(self, mock_ai, mock_exec, mock_router_ai):
        # Two separate ai_manager references: the parser's (query
        # translation) and the router's own (result explanation, see
        # _maybe_explain — explain defaults to True). Both must be
        # mocked unavailable, or a real, now-enabled AI backend answers
        # for real and ai_explanation_used flips true underneath this
        # test (found live 2026-09-09, when AI_ENABLED became true).
        # is_available is async now (bridged via run_sync) — stub AsyncMock.
        mock_ai.is_available = AsyncMock(return_value=False)
        mock_router_ai.is_available = AsyncMock(return_value=False)
        from backend.nl_search.executor import ExecutionResult
        from backend.nl_search.schema import ScannedResultItem
        mock_exec.return_value = ExecutionResult(
            matched_all=[],
            top_n=[
                ScannedResultItem(
                    symbol="AAPL",
                    total_score=75.0,
                    rank=1,
                    signals=["RSI_OVERSOLD"],
                    trend_directions={"ONE_DAY": "uptrend"},
                    rsi=35.0,
                    macd=1.0,
                    adx=25.0,
                    price=150.0,
                ),
            ],
            filter_description="direction=bullish",
            universe_size=1,
            matched_count=1,
        )

        resp = client.post("/api/nl-search", json={"query": "strongest bullish stocks"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["query"], "strongest bullish stocks")
        self.assertEqual(data["results"][0]["symbol"], "AAPL")
        self.assertEqual(data["parser_used"], "rules")
        self.assertFalse(data["ai_translation_used"])
        self.assertFalse(data["ai_explanation_used"])

    @patch("backend.api.nl_search.router.execute_query")
    @patch("backend.nl_search.parser.ai_manager")
    def test_ai_translation_used_flag(self, mock_ai, mock_exec):
        # is_available/complete are async now — stub as AsyncMock so the
        # run_sync bridge in parser.py receives a coroutine.
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.complete = AsyncMock(
            return_value=MagicMock(text=None, provider="disabled", model="llama3.2")
        )
        from backend.nl_search.executor import ExecutionResult
        mock_exec.return_value = ExecutionResult(
            matched_all=[],
            top_n=[],
            filter_description="(match all)",
            universe_size=0,
            matched_count=0,
        )

        # Even with AI available, if rule-based fires first and produces valid
        # filters, AI path is not used.
        resp = client.post("/api/nl-search", json={"query": "strongest bullish"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["parser_used"], "rules")
        self.assertFalse(data["ai_translation_used"])

    @patch("backend.api.nl_search.router.execute_query")
    @patch("backend.nl_search.parser.ai_manager")
    def test_garbage_query_returns_empty_results(self, mock_ai, mock_exec):
        # Async now — must be an AsyncMock for the run_sync bridge.
        mock_ai.is_available = AsyncMock(return_value=False)
        from backend.nl_search.executor import ExecutionResult
        mock_exec.return_value = ExecutionResult(
            matched_all=[],
            top_n=[],
            filter_description="(match all)",
            universe_size=0,
            matched_count=0,
        )

        resp = client.post("/api/nl-search", json={"query": "asdfghjklqwert"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["parser_used"], "default")
        self.assertEqual(data["results"], [])
        self.assertIsNotNone(data["reason"])

    @patch("backend.api.nl_search.router.ai_manager")
    @patch("backend.api.nl_search.router.execute_query")
    @patch("backend.nl_search.parser.ai_manager")
    def test_explain_true_includes_explanation(self, mock_ai, mock_exec, mock_router_ai):
        # See test_rule_based_query_returns_200's comment: the router's
        # own ai_manager reference (used for the explanation step) is
        # separate from the parser's and must be mocked too.
        # is_available is async now — stub AsyncMock.
        mock_ai.is_available = AsyncMock(return_value=False)
        mock_router_ai.is_available = AsyncMock(return_value=False)
        from backend.nl_search.executor import ExecutionResult
        from backend.nl_search.schema import ScannedResultItem
        mock_exec.return_value = ExecutionResult(
            matched_all=[],
            top_n=[
                ScannedResultItem(
                    symbol="AAPL", total_score=75.0, rank=1, signals=[],
                    trend_directions={}, rsi=35.0,
                ),
            ],
            filter_description="direction=bullish",
            universe_size=1,
            matched_count=1,
        )

        resp = client.post("/api/nl-search", json={
            "query": "strongest bullish",
            "explain": True,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # With AI disabled, explanation is None
        self.assertIsNone(data["explanation"])
        self.assertFalse(data["ai_explanation_used"])

    @patch("backend.api.nl_search.router.execute_query")
    @patch("backend.nl_search.parser.ai_manager")
    def test_explain_false_skips_ai(self, mock_ai, mock_exec):
        # AI is on but explain=False — async is_available stubbed AsyncMock.
        mock_ai.is_available = AsyncMock(return_value=True)
        from backend.nl_search.executor import ExecutionResult
        from backend.nl_search.schema import ScannedResultItem
        mock_exec.return_value = ExecutionResult(
            matched_all=[],
            top_n=[
                ScannedResultItem(
                    symbol="AAPL", total_score=75.0, rank=1, signals=[],
                    trend_directions={},
                ),
            ],
            filter_description="direction=bullish",
            universe_size=1,
            matched_count=1,
        )

        resp = client.post("/api/nl-search", json={
            "query": "strongest bullish",
            "explain": False,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["explanation"])
        self.assertFalse(data["ai_explanation_used"])

    def test_rejects_query_too_short(self):
        resp = client.post("/api/nl-search", json={"query": "x"})
        self.assertEqual(resp.status_code, 422)

    def test_rejects_query_too_long(self):
        resp = client.post("/api/nl-search", json={"query": "x" * 600})
        self.assertEqual(resp.status_code, 422)

    def test_rejects_invalid_top_n(self):
        resp = client.post("/api/nl-search", json={
            "query": "strongest bullish",
            "top_n": 100,  # max is 50
        })
        self.assertEqual(resp.status_code, 422)


class TestNLSearchEndpointAIExplanation(unittest.TestCase):

    @patch("backend.api.nl_search.router.execute_query")
    @patch("backend.api.nl_search.router.ai_manager")
    def test_explanation_when_ai_on_and_explain_true(self, mock_ai, mock_exec):
        # is_available/complete are async now (bridged via run_sync in
        # _maybe_explain) — stub as AsyncMock.
        mock_ai.is_available = AsyncMock(return_value=True)
        mock_ai.complete = AsyncMock(
            return_value=MagicMock(
                text='```json\n{"explanation": "All symbols are in a strong uptrend with high momentum."}\n```',
                provider="ollama",
                model="llama3.2",
            )
        )
        from backend.nl_search.executor import ExecutionResult
        from backend.nl_search.schema import ScannedResultItem
        mock_exec.return_value = ExecutionResult(
            matched_all=[],
            top_n=[
                ScannedResultItem(
                    symbol="AAPL", total_score=75.0, rank=1, signals=[],
                    trend_directions={},
                ),
            ],
            filter_description="direction=bullish",
            universe_size=1,
            matched_count=1,
        )

        resp = client.post("/api/nl-search", json={
            "query": "strongest bullish",
            "explain": True,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ai_explanation_used"])
        self.assertIn("uptrend", data["explanation"].lower())


if __name__ == "__main__":
    unittest.main()
