from datetime import datetime, timedelta

from backend.ai.market_tools import (
    AlertsRequest,
    ApplicationHelpRequest,
    BarsRequest,
    ConfluenceRequest,
    CsvImportRequest,
    IndicatorRequest,
    PositionInput,
    RiskDashboardRequest,
    SymbolRequest,
    TapeRequest,
    TradeJournalRequest,
    TrendRequest,
    get_alerts_tool,
    get_application_help_tool,
    get_bars_tool,
    get_confluence_tool,
    get_indicator_tool,
    get_quote_tool,
    get_relative_strength_tool,
    get_risk_dashboard_tool,
    get_sector_data_tool,
    get_support_resistance_tool,
    get_tape_state_tool,
    get_trade_journal_tool,
    get_trend_tool,
    import_csv_tool,
)
from backend.models.market_data import Bar, DataStatus
from backend.repositories.alert_repository import AlertRepository
from backend.utils.timezone import format_edt_iso


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
                "created_at": format_edt_iso(alert.created_at),
                "updated_at": format_edt_iso(alert.updated_at),
            }
        ]
    finally:
        for row in repository.get_all():
            repository.delete(row.id)
        repository.close()


def test_sector_data_tool_reports_alignment(monkeypatch) -> None:
    from datetime import UTC, datetime

    class _FakeSignal:
        def to_dict(self):
            return {
                "symbol": "AAPL",
                "sector": "Technology",
                "sector_etf": "XLK",
                "stock_trend": "up",
                "sector_trend": "up",
                "market_trend": "up",
                "alignment_score": 1.0,
                "alignment_level": "perfect",
                "contributing_factors": {},
                "timestamp": datetime(2026, 9, 22, tzinfo=UTC).isoformat(),
            }

    class _FakeSectorEngine:
        def get_current_signal(self):
            return _FakeSignal()

    import importlib

    # backend.api.regime's __init__ does `from .router import router as router`,
    # which rebinds the package's "router" attribute to the APIRouter instance
    # and shadows the submodule name — importlib.import_module bypasses that
    # by going through sys.modules instead of attribute lookup.
    regime_router_module = importlib.import_module("backend.api.regime.router")
    monkeypatch.setattr(regime_router_module, "_get_sector_engine", lambda symbol: _FakeSectorEngine())

    result = get_sector_data_tool(SymbolRequest(symbol="AAPL"))

    assert result.sector == "Technology"
    assert result.sector_etf == "XLK"
    assert result.alignment_level == "perfect"


def test_application_help_returns_verified_routes() -> None:
    result = get_application_help_tool(ApplicationHelpRequest(query="where are my alerts"))
    assert result.matches[0]["title"] == "Alerts"
    assert result.matches[0]["route"] == "#alerts"


def test_application_help_routes_match_frontend_canonical_hashes() -> None:
    """Drift trip-wire: frontend/src/utils/appNavigation.ts's HASH_BY_PAGE is
    the actual routing source of truth (Python can't import it), so this
    hardcodes its 12 canonical page->hash pairs and fails loudly if either
    side adds/renames/removes a page without updating the other. This is
    exactly the mismatch that let "signals" silently point at the legacy
    "#historical-replay" alias instead of the canonical "#signals" hash.
    """
    frontend_hash_by_page = {
        "dashboard": "#dashboard",
        "watchlist": "#watchlist",
        "health": "#system-health",
        "alerts": "#alerts",
        "backtest": "#backtest",
        "symbol": "#symbol",
        "signals": "#signals",
        "scanner": "#scanner",
        "hub": "#ai-hub",
        "risk": "#risk",
        "journal": "#journal",
        "calendar": "#calendar",
    }

    result = get_application_help_tool(ApplicationHelpRequest(limit=20))
    tool_hash_by_page = {match["page"]: match["route"] for match in result.matches}

    assert tool_hash_by_page == frontend_hash_by_page


def test_application_help_declares_required_state_for_symbol_scoped_pages() -> None:
    result = get_application_help_tool(ApplicationHelpRequest(query="chart for a stock"))
    symbol_page = next(match for match in result.matches if match["page"] == "symbol")
    assert symbol_page["required_state"] == ["symbol"]

    dashboard = get_application_help_tool(ApplicationHelpRequest(query="dashboard overview"))
    dashboard_page = next(match for match in dashboard.matches if match["page"] == "dashboard")
    assert dashboard_page["required_state"] == []


def test_trend_tool_reports_current_trend_via_shared_payload_builder(monkeypatch) -> None:
    """Cold-engine path: proves get_trend_tool delegates to the real
    _build_trend_payload (same function GET /api/trend/.../current/... uses)
    rather than re-deriving the response shape — see the confluence/regime
    duplication incident this pattern is meant to avoid repeating.
    """

    class _FakeTrendEngine:
        def get_current_trend(self, tf):
            return None

        def get_timeframe_metadata(self, tf):
            return {}

    monkeypatch.setattr(
        "backend.api.trend.registry.get_engine", lambda symbol: _FakeTrendEngine()
    )

    result = get_trend_tool(TrendRequest(symbol="AAPL", timeframe="1d"))

    assert result.symbol == "AAPL"
    assert result.timeframe == "1d"
    assert result.direction == "unknown"
    assert result.confidence == 0.0


def test_confluence_tool_reports_neutral_on_cold_engine(monkeypatch) -> None:
    import importlib

    class _FakeConfluenceEngine:
        preset_name = "day_trading"

        def get_current_confluence(self):
            return None

    mtf_router_module = importlib.import_module("backend.api.multitimeframe.router")
    monkeypatch.setattr(
        mtf_router_module, "get_engine", lambda symbol, preset="day_trading": _FakeConfluenceEngine()
    )

    result = get_confluence_tool(ConfluenceRequest(symbol="AAPL", preset="day_trading"))

    assert result.symbol == "AAPL"
    assert result.direction == "neutral"
    assert result.preset == "day_trading"
    assert result.timeframe_signals == {}


def test_relative_strength_tool_reports_computed_signals(monkeypatch) -> None:
    import importlib

    class _FakeSignal:
        def to_dict(self):
            return {"symbol": "AAPL", "benchmark": "SPY", "rs_pct": 1.5, "classification": "leading"}

    class _FakeRsEngine:
        def compute(self):
            return [_FakeSignal(), _FakeSignal()]

    regime_router_module = importlib.import_module("backend.api.regime.router")
    monkeypatch.setattr(regime_router_module, "_get_rs_engine", lambda symbol: _FakeRsEngine())

    result = get_relative_strength_tool(SymbolRequest(symbol="AAPL"))

    assert result.symbol == "AAPL"
    assert result.count == 2
    assert result.signals[0]["benchmark"] == "SPY"


def test_tape_state_tool_reports_disabled_error(monkeypatch) -> None:
    from backend.config.settings import settings

    monkeypatch.setattr(settings.tape, "enabled", False)

    try:
        get_tape_state_tool(TapeRequest(symbol="AAPL"))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "TAPE_ENABLED" in str(exc)


def test_tape_state_tool_reports_snapshot_when_enabled(monkeypatch) -> None:
    from backend.config.settings import settings

    monkeypatch.setattr(settings.tape, "enabled", True)
    monkeypatch.setattr(
        "backend.api.tape.registry.get_tape_engine",
        lambda symbol: type("_FakeTapeEngine", (), {"get_snapshot": lambda self: {"buy_volume": 100, "sell_volume": 40}})(),
    )

    result = get_tape_state_tool(TapeRequest(symbol="AAPL"))

    assert result.symbol == "AAPL"
    assert result.snapshot == {"buy_volume": 100, "sell_volume": 40}
    assert result.source_timestamp


def test_import_csv_parses_positions() -> None:
    csv_content = "symbol,side,quantity,entry_price,stop_price\nAAPL,long,100,220,212\nMSFT,short,50,300,\n"
    result = import_csv_tool(CsvImportRequest(import_type="positions", csv_content=csv_content))

    assert result.row_count == 2
    assert result.errors == []
    assert result.rows[0] == {
        "symbol": "AAPL",
        "side": "long",
        "quantity": 100.0,
        "entry_price": 220.0,
        "stop_price": 212.0,
        "current_price": None,
        "sector": None,
    }
    assert result.rows[1]["side"] == "short"
    assert result.rows[1]["stop_price"] is None


def test_import_csv_parses_watchlist_and_dedupes() -> None:
    csv_content = "symbol\naapl\nMSFT\nAAPL\n"
    result = import_csv_tool(CsvImportRequest(import_type="watchlist", csv_content=csv_content))

    assert result.row_count == 2
    assert result.rows == [{"symbol": "AAPL"}, {"symbol": "MSFT"}]


def test_import_csv_parses_trade_journal_as_loose_rows() -> None:
    csv_content = "symbol,status,notes\nAAPL,closed,Great breakout\n"
    result = import_csv_tool(CsvImportRequest(import_type="trade_journal", csv_content=csv_content))

    assert result.rows == [{"symbol": "AAPL", "status": "closed", "notes": "Great breakout"}]


def test_import_csv_reports_missing_required_columns() -> None:
    csv_content = "name,quantity\nAAPL,100\n"
    result = import_csv_tool(CsvImportRequest(import_type="positions", csv_content=csv_content))

    assert result.row_count == 0
    assert "symbol" in result.errors[0]
    assert "entry_price" in result.errors[0]


def test_import_csv_reports_invalid_numeric_values_per_row() -> None:
    csv_content = "symbol,quantity,entry_price\nAAPL,not_a_number,220\nMSFT,50,300\n"
    result = import_csv_tool(CsvImportRequest(import_type="positions", csv_content=csv_content))

    assert result.row_count == 1
    assert result.rows[0]["symbol"] == "MSFT"
    assert any("Row 1" in error for error in result.errors)


def test_import_csv_never_evaluates_formula_looking_cells() -> None:
    """Cells starting with =/+/-/@ (classic spreadsheet-formula-injection
    triggers) must survive as inert literal text — this module never opens
    the content in a spreadsheet engine, only stdlib csv.reader.
    """
    csv_content = 'symbol,status,notes\nAAPL,closed,"=cmd|\'/c calc\'!A1"\n'
    result = import_csv_tool(CsvImportRequest(import_type="trade_journal", csv_content=csv_content))

    assert result.rows[0]["notes"] == "=cmd|'/c calc'!A1"


def test_import_csv_rejects_too_many_rows() -> None:
    lines = ["symbol"] + ["AAPL"] * 501
    csv_content = "\n".join(lines)

    try:
        import_csv_tool(CsvImportRequest(import_type="watchlist", csv_content=csv_content))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "500" in str(exc)


def test_import_csv_supports_headerless_mode() -> None:
    csv_content = "AAPL,long,100,220\n"
    result = import_csv_tool(
        CsvImportRequest(import_type="watchlist", csv_content=csv_content, has_header=False)
    )
    # headerless watchlist import needs a "symbol" column name to match —
    # column_1/column_2/... won't satisfy the required-columns check, so
    # this documents the honest failure mode rather than a silent guess.
    assert result.row_count == 0
    assert "symbol" in result.errors[0]
