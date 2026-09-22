from datetime import datetime

from backend.ai.market_tools import (
    FundamentalsRequest,
    NewsRequest,
    get_fundamentals_tool,
    get_news_tool,
)
from backend.models.aux_data import FundamentalsItem, FundamentalsResponse, NewsItem, NewsResponse


class _FakeAux:
    def get_news(self, symbol, limit):
        return NewsResponse(
            symbol=symbol,
            items=[
                NewsItem(
                    headline="Test headline",
                    source="test",
                    timestamp=datetime(2026, 9, 22, 12),
                    symbol=symbol,
                    relevance=1.0,
                )
            ][:limit],
            provider="finnhub_news",
            timestamp=datetime(2026, 9, 22, 12),
        )

    def get_fundamentals(self, symbol):
        return FundamentalsResponse(
            symbol=symbol,
            data=FundamentalsItem(symbol=symbol, pe_ratio=20),
            provider="yfinance_fundamentals",
            timestamp=datetime(2026, 9, 22, 12),
        )


def test_aux_tools_preserve_provider_and_payload(monkeypatch) -> None:
    monkeypatch.setattr("backend.ai.market_tools._aux_manager", lambda: _FakeAux())

    news = get_news_tool(NewsRequest(symbol="AAPL", limit=5))
    fundamentals = get_fundamentals_tool(FundamentalsRequest(symbol="AAPL"))

    assert news.provider == "finnhub_news"
    assert news.items[0]["headline"] == "Test headline"
    assert fundamentals.provider == "yfinance_fundamentals"
    assert fundamentals.data["pe_ratio"] == 20
