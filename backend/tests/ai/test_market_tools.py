from datetime import datetime, timedelta

from backend.ai.market_tools import (
    AlertsRequest,
    ApplicationHelpRequest,
    BarsRequest,
    IndicatorRequest,
    PositionInput,
    RiskDashboardRequest,
    SymbolRequest,
    TradeJournalRequest,
    get_alerts_tool,
    get_application_help_tool,
    get_bars_tool,
    get_indicator_tool,
    get_quote_tool,
    get_risk_dashboard_tool,
    get_support_resistance_tool,
    get_trade_journal_tool,
)
from backend.models.market_data import Bar, DataStatus
from backend.repositories.alert_repository import AlertRepository


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

    def get_quote(self, symbol):
        from backend.models.market_data import DataStatus, Quote

        return Quote(symbol=symbol, price=101, timestamp=datetime(2026, 9, 22, 10), provider="webull", data_status=DataStatus.LIVE)


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
    quote = get_quote_tool(SymbolRequest(symbol="AAPL"))
    assert quote.selected_provider == "webull"
    assert quote.reconciliation["conflict"] is False


def test_risk_tool_calculates_explicit_position_snapshot() -> None:
    result = get_risk_dashboard_tool(
        RiskDashboardRequest(
            positions=[PositionInput(symbol="AAPL", quantity=100, entry_price=200, current_price=210, stop_price=190)]
        )
    )
    assert result.available is True
    assert result.gross_exposure == 21000
    assert result.stop_loss_risk == 1000
    assert result.positions[0]["pnl"] == 1000


def test_local_tools_are_honest_without_browser_snapshots() -> None:
    risk = get_risk_dashboard_tool(RiskDashboardRequest())
    journal = get_trade_journal_tool(TradeJournalRequest())
    assert risk.available is False
    assert "browser" in risk.reason
    assert journal.available is False
    assert "browser" in journal.reason


def test_alerts_tool_reads_database_backed_rules_and_triggers() -> None:
    repository = AlertRepository()
    try:
        alert = repository.create(name="AAPL breakout", symbol="AAPL", condition_type="price_above", parameter="220")
        repository.create(name="MSFT dip", symbol="MSFT", condition_type="price_below", parameter="300")

        all_alerts = get_alerts_tool(AlertsRequest())
        scoped = get_alerts_tool(AlertsRequest(symbol="AAPL"))

        assert len(all_alerts.alerts) >= 2
        assert scoped.alerts == [
            {
                "id": alert.id,
                "name": "AAPL breakout",
                "symbol": "AAPL",
                "condition_type": "price_above",
                "parameter": "220",
                "is_enabled": True,
                "created_at": alert.created_at.isoformat(),
                "updated_at": alert.updated_at.isoformat(),
            }
        ]
    finally:
        for row in repository.get_all():
            repository.delete(row.id)
        repository.close()


def test_application_help_returns_verified_routes() -> None:
    result = get_application_help_tool(ApplicationHelpRequest(query="where are my alerts"))
    assert result.matches[0]["title"] == "Alerts"
    assert result.matches[0]["route"] == "#alerts"
