from datetime import datetime

from backend.ai.market_tools import (
    CalendarRequest,
    FundamentalsRequest,
    NewsRequest,
    OptionsRequest,
    get_calendar_tool,
    get_fundamentals_tool,
    get_news_tool,
    get_options_tool,
)
from backend.models.aux_data import (
    FundamentalsItem,
    FundamentalsResponse,
    NewsItem,
    NewsResponse,
    OptionsChain,
    OptionsResponse,
)


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

    def get_options(self, symbol, expiration=None):
        return OptionsResponse(
            symbol=symbol,
            chains=[OptionsChain(symbol=symbol, expiration=expiration or "2026-10-16")],
            expirations=[expiration or "2026-10-16"],
            near_term_iv=0.3,
            provider="yfinance_options",
            timestamp=datetime(2026, 9, 22, 12),
        )


def test_aux_tools_preserve_provider_and_payload(monkeypatch) -> None:
    monkeypatch.setattr("backend.ai.market_tools._aux_manager", lambda: _FakeAux())

    news = get_news_tool(NewsRequest(symbol="AAPL", limit=5))
    fundamentals = get_fundamentals_tool(FundamentalsRequest(symbol="AAPL"))
    options = get_options_tool(OptionsRequest(symbol="AAPL", expiration="2026-10-16"))

    assert news.provider == "finnhub_news"
    assert news.items[0]["headline"] == "Test headline"
    assert fundamentals.provider == "yfinance_fundamentals"
    assert fundamentals.data["pe_ratio"] == 20
    assert options.provider == "yfinance_options"
    assert options.expirations == ["2026-10-16"]


def test_calendar_tool_reuses_events_for_symbol(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.market_data.services.calendar_service.events_for_symbol",
        lambda symbol: [{"symbol": symbol, "event_type": "earnings", "date": "2026-10-01"}],
    )

    result = get_calendar_tool(CalendarRequest(symbol="AAPL"))

    assert result.symbol == "AAPL"
    assert result.provider == "yfinance"
    assert result.events == [{"source": "yfinance", "symbol": "AAPL", "event_type": "earnings", "date": "2026-10-01"}]
