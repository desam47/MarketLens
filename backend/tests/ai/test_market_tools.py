from datetime import datetime, timedelta

from backend.ai.market_tools import (
    BarsRequest,
    IndicatorRequest,
    get_bars_tool,
    get_indicator_tool,
    get_support_resistance_tool,
)
from backend.models.market_data import Bar, DataStatus


class _FakeManager:
    def get_historical_bars(self, symbol, timeframe, range_, include_extended_hours):
        del range_, include_extended_hours
        return [
            Bar(
                symbol=symbol,
                timestamp=datetime(2026, 9, 22, 10) + timedelta(hours=index),
                open=100 + index,
                high=102 + index,
                low=98 + index,
                close=101 + index,
                volume=1000,
                timeframe=timeframe,
                provider="test",
                data_status=DataStatus.HISTORICAL,
            )
            for index in range(20)
        ]


def test_market_tools_compute_from_provider_bars(monkeypatch) -> None:
    monkeypatch.setattr("backend.ai.market_tools._manager", lambda: _FakeManager())

    request = BarsRequest(symbol="AAPL", timeframe="1d", limit=10)
    bars = get_bars_tool(request)
    indicator = get_indicator_tool(
        IndicatorRequest(symbol="AAPL", timeframe="1d", indicator="sma", period=5)
    )
    levels = get_support_resistance_tool(request)

    assert bars.symbol == "AAPL"
    assert len(bars.bars) == 10
    assert indicator.value == 118
    assert levels.support == 108
    assert levels.resistance == 121
