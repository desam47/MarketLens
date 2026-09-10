"""
Tests for build_context()'s news/fundamentals sections (Phase 18
aux-data — real, working News/Fundamentals providers that never
reached the AI analysis prompt before this change).
"""
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from backend.ai.context import build_context
from backend.models.aux_data import FundamentalsItem, FundamentalsResponse, NewsItem, NewsResponse
from backend.scanner.scanner import ScanResult


def _fake_scan_result() -> ScanResult:
    result = ScanResult("AAPL", datetime.now(UTC))
    result.quote = MagicMock()
    result.quote.price = 185.0
    result.quote.timestamp = datetime.now(UTC)
    result.trend_signals = {
        "ONE_DAY": {"direction": "strong_bullish", "strength": "strong", "confidence": 0.85},
    }
    result.indicator_values = {"rsi": 62.0, "macd": 1.5, "volume": 50_000_000}
    result.scores = {"total_score": 72.5}
    result.signals = ["bullish_trend", "high_volume"]
    return result


class TestBuildContextNews(unittest.TestCase):

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.aux_data.services.manager.aux_data_manager")
    @patch("backend.ai.context.market_scanner")
    def test_news_items_populate_compact_shape(
        self, mock_scanner, mock_aux, mock_get_engine
    ):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})
        now = datetime.now(UTC)
        mock_aux.get_news.return_value = NewsResponse(
            symbol="AAPL",
            items=[
                NewsItem(
                    headline="Apple beats earnings estimates",
                    source="Reuters",
                    timestamp=now - timedelta(hours=2),
                    symbol="AAPL",
                    relevance=0.9,
                ),
            ],
            provider="yfinance",
            timestamp=now,
        )

        ctx = build_context("AAPL", "1d")

        self.assertEqual(len(ctx.news), 1)
        item = ctx.news[0]
        self.assertEqual(item["headline"], "Apple beats earnings estimates")
        self.assertEqual(item["source"], "Reuters")
        self.assertEqual(item["relevance"], 0.9)
        self.assertAlmostEqual(item["age_hours"], 2.0, delta=0.1)

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.aux_data.services.manager.aux_data_manager")
    @patch("backend.ai.context.market_scanner")
    def test_provider_exception_degrades_to_empty_list(
        self, mock_scanner, mock_aux, mock_get_engine
    ):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})
        mock_aux.get_news.side_effect = RuntimeError("provider down")

        ctx = build_context("AAPL", "1d")

        self.assertEqual(ctx.news, [])
        # data_status must be unaffected by an aux-data failure.
        self.assertEqual(ctx.data_status, "live")

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.ai.context.market_scanner")
    def test_include_news_false_skips_the_call_entirely(
        self, mock_scanner, mock_get_engine
    ):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})

        with patch("backend.aux_data.services.manager.aux_data_manager") as mock_aux:
            ctx = build_context("AAPL", "1d", include_news=False)
            mock_aux.get_news.assert_not_called()
        self.assertEqual(ctx.news, [])


class TestBuildContextFundamentals(unittest.TestCase):

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.aux_data.services.manager.aux_data_manager")
    @patch("backend.ai.context.market_scanner")
    def test_fundamentals_curated_subset_populates(
        self, mock_scanner, mock_aux, mock_get_engine
    ):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})
        mock_aux.get_fundamentals.return_value = FundamentalsResponse(
            symbol="AAPL",
            data=FundamentalsItem(
                symbol="AAPL",
                sector="Technology",
                industry="Consumer Electronics",
                market_cap=3_000_000_000_000.0,
                pe_ratio=28.5,
                recommendation="buy",
                # company_name, eps, etc. deliberately left None —
                # must not appear in the curated dict.
            ),
            provider="yfinance",
            timestamp=datetime.now(UTC),
        )

        ctx = build_context("AAPL", "1d")

        self.assertEqual(ctx.fundamentals["sector"], "Technology")
        self.assertEqual(ctx.fundamentals["pe_ratio"], 28.5)
        self.assertEqual(ctx.fundamentals["recommendation"], "buy")
        # None-valued fields are dropped, not sent as null noise.
        self.assertNotIn("eps", ctx.fundamentals)
        self.assertNotIn("company_name", ctx.fundamentals)

    @patch("backend.api.trend.registry.get_engine")
    @patch("backend.aux_data.services.manager.aux_data_manager")
    @patch("backend.ai.context.market_scanner")
    def test_provider_exception_degrades_to_empty_dict(
        self, mock_scanner, mock_aux, mock_get_engine
    ):
        mock_scanner.scan_symbol.return_value = _fake_scan_result()
        mock_get_engine.return_value = MagicMock(trend_history={})
        mock_aux.get_fundamentals.side_effect = RuntimeError("provider down")

        ctx = build_context("AAPL", "1d")

        self.assertEqual(ctx.fundamentals, {})
        self.assertEqual(ctx.data_status, "live")


if __name__ == "__main__":
    unittest.main()
