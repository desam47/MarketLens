from datetime import datetime, timedelta

from backend.ai.market_tools import (
    AlertsRequest,
    ApplicationHelpRequest,
    BarsRequest,
    ChangeAnalysisRequest,
    ComparisonRequest,
    ConfluenceRequest,
    CsvImportRequest,
    HistoricalSimilarityRequest,
    IndicatorRequest,
    MoveAnalysisRequest,
    PositionInput,
    RiskDashboardRequest,
    ScenarioRequest,
    SessionStatsRequest,
    SymbolRequest,
    TapeRequest,
    TradeJournalRequest,
    TrendRequest,
    compare_symbols_tool,
    get_alerts_tool,
    get_application_help_tool,
    get_bars_tool,
    get_confluence_tool,
    get_indicator_tool,
    get_market_context_tool,
    get_market_regime_tool,
    get_quote_tool,
    get_relative_strength_tool,
    get_risk_dashboard_tool,
    get_sector_data_tool,
    get_session_stats_tool,
    get_support_resistance_tool,
    get_tape_state_tool,
    get_trade_journal_tool,
    get_trend_tool,
    historical_similarity_tool,
    import_csv_tool,
    scenario_analysis_tool,
    what_changed_tool,
    why_did_it_move_tool,
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
    assert bars.provider == "test"
    assert bars.fallback is True  # "test" != configured primary provider
    assert indicator.value == 118
    assert indicator.fallback is True  # inherited from get_bars_tool's payload
    assert levels.support == 108
    assert levels.resistance == 121
    quote = get_quote_tool(SymbolRequest(symbol="AAPL"))
    assert quote.selected_provider == "webull"
    assert quote.reconciliation["conflict"] is False


def test_move_analysis_separates_facts_correlations_and_unknowns(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(
            symbol=request.symbol,
            provider="test",
            source_timestamp="2026-09-22T16:00:00-04:00",
            bars=[
                {"close": 100, "volume": 1000},
                {"close": 105, "volume": 2500},
            ],
        ),
    )
    monkeypatch.setattr(
        "backend.ai.market_tools.get_news_tool",
        lambda request: _Payload(symbol=request.symbol, items=[], provider="news", source_timestamp="now"),
    )
    monkeypatch.setattr(
        "backend.ai.market_tools.get_options_tool",
        lambda request: _Payload(symbol=request.symbol, chains=[], provider="options", source_timestamp="now"),
    )
    monkeypatch.setattr(
        "backend.ai.market_tools.get_sector_data_tool",
        lambda request: _Payload(symbol=request.symbol, signal="bullish", provider="engine"),
    )
    monkeypatch.setattr(
        "backend.ai.market_tools.get_market_regime_tool",
        lambda request: _Payload(symbol=request.symbol, regime="risk_on", provider="engine"),
    )
    monkeypatch.setattr(
        "backend.ai.market_tools.get_tape_state_tool",
        lambda request: _Payload(symbol=request.symbol, pressure="buy", provider="webull"),
    )

    result = why_did_it_move_tool(MoveAnalysisRequest(symbol="AAPL"))

    assert result.facts[0]["type"] == "price_move"
    assert result.facts[0]["change_percent"] == 5.0
    assert result.facts[1]["ratio"] == 2.5
    assert any(item["type"] == "news" for item in result.unknowns)
    assert result.conclusion["status"] == "evidence_only"


def test_what_changed_compares_current_bar_with_previous_close(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(
            symbol=request.symbol,
            provider="test",
            source_timestamp="2026-09-22T16:00:00-04:00",
            bars=[
                {"timestamp": "2026-09-21T16:00:00-04:00", "close": 100},
                {"timestamp": "2026-09-22T16:00:00-04:00", "close": 103},
            ],
        ),
    )
    result = what_changed_tool(ChangeAnalysisRequest(symbol="AAPL"))
    assert result.changes[0]["percent"] == 3.0
    assert result.conclusion["status"] == "verified_comparison"


def test_compare_symbols_ranks_verified_returns(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    bars_by_symbol = {
        "AAPL": [{"close": 100}, {"close": 110}],
        "MSFT": [{"close": 100}, {"close": 105}],
        "NVDA": [{"close": 100}, {"close": 120}],
    }

    def fake_bars(request):
        return _Payload(
            symbol=request.symbol,
            bars=bars_by_symbol[request.symbol],
            provider="test",
            source_timestamp="2026-09-22T16:00:00-04:00",
            session=request.session,
        )

    monkeypatch.setattr("backend.ai.market_tools.get_bars_tool", fake_bars)
    result = compare_symbols_tool(ComparisonRequest(symbols=["AAPL", "MSFT", "NVDA"]))

    assert [row["symbol"] for row in result.rankings] == ["NVDA", "AAPL", "MSFT"]
    assert [row["rank"] for row in result.rankings] == [1, 2, 3]
    assert result.rankings[0]["value"] == 20.0
    assert result.conclusion["status"] == "verified_ranking"


def test_compare_symbols_reports_missing_symbol_without_failing_all(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    def fake_bars(request):
        if request.symbol == "BAD":
            raise ValueError("no bars")
        return _Payload(
            symbol=request.symbol,
            bars=[{"close": 100}, {"close": 110}],
            provider="test",
            source_timestamp="2026-09-22T16:00:00-04:00",
            session=request.session,
        )

    monkeypatch.setattr("backend.ai.market_tools.get_bars_tool", fake_bars)
    result = compare_symbols_tool(ComparisonRequest(symbols=["AAPL", "BAD"]))

    assert result.evaluated_count == 1
    assert result.rankings[0]["symbol"] == "AAPL"
    assert result.unknowns[0]["symbol"] == "BAD"


def test_scenario_analysis_recalculates_price_and_stop_risk() -> None:
    result = scenario_analysis_tool(
        ScenarioRequest(
            positions=[
                PositionInput(
                    symbol="AAPL",
                    quantity=100,
                    entry_price=200,
                    current_price=220,
                    stop_price=190,
                    sector="Technology",
                )
            ],
            price_shocks={"AAPL": -5},
            stop_price_overrides={"AAPL": 195},
            portfolio_value=100_000,
        )
    )

    assert result.available is True
    assert result.positions[0]["scenario_price"] == 209
    assert result.positions[0]["pnl_delta"] == -1100
    assert result.base_stop_loss_risk == 1000
    assert result.scenario_stop_loss_risk == 500
    assert result.scenario_stop_risk_percent == 0.5
    assert result.conclusion["status"] == "verified_scenario"


def test_scenario_analysis_is_honest_without_browser_positions() -> None:
    result = scenario_analysis_tool(ScenarioRequest(price_shocks={"AAPL": -5}))
    assert result.available is False
    assert "browser-local" in result.reason


def test_historical_similarity_excludes_current_setup_from_matches(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    bars = [
        {"timestamp": f"2026-01-{index + 1:02d}T16:00:00-05:00", "close": 100 + index}
        for index in range(30)
    ]
    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(
            symbol=request.symbol,
            timeframe=request.timeframe,
            session=request.session,
            bars=bars,
            provider="test",
            source_timestamp=bars[-1]["timestamp"],
        ),
    )

    result = historical_similarity_tool(
        HistoricalSimilarityRequest(symbol="AAPL", lookback=3, horizons=[1, 2], tolerance=10, max_matches=3)
    )

    assert result.available is True
    assert result.look_ahead_safe is True
    assert all(match["end_index"] <= 26 for match in result.matches)
    assert result.summaries[0]["sample_size"] == 3
    assert result.conclusion["status"] == "verified_similarity"


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

    class _FakeStockEngine:
        def get_timeframe_metadata(self, tf):
            return {}

    class _FakeSectorEngine:
        _stock_eng = _FakeStockEngine()

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
    assert result.provider == "MarketLens engine"
    assert result.fallback is False


def test_application_help_returns_verified_routes() -> None:
    result = get_application_help_tool(ApplicationHelpRequest(query="where are my alerts"))
    assert result.matches[0]["title"] == "Alerts"
    assert result.matches[0]["route"] == "#alerts"


def test_application_help_routes_match_frontend_canonical_hashes() -> None:
    """Regression snapshot for the now-genuinely-parsed route table: routes
    come from _parse_frontend_hash_by_page() reading
    frontend/src/utils/appNavigation.ts directly (see
    test_parse_frontend_hash_by_page_reads_real_file below for that in
    isolation), not a hand-copied Python dict any more — this just pins
    the currently-expected values so an unexpected frontend change (or a
    parser regression silently falling back) is still visible here.
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


def test_application_help_titles_are_parsed_from_app_tsx() -> None:
    """App.tsx's actual title for the signals page is "Historical
    Signals" (from its pageName="..." prop), not "Historical Replay" — a
    previously hand-maintained table had this wrong; genuine parsing
    can't drift from the file it reads.
    """
    result = get_application_help_tool(ApplicationHelpRequest(limit=20))
    titles = {match["page"]: match["title"] for match in result.matches}

    assert titles["signals"] == "Historical Signals"
    assert titles["dashboard"] == "Dashboard"
    assert titles["hub"] == "AI Hub"


def test_parse_frontend_hash_by_page_reads_real_file() -> None:
    from backend.ai.market_tools import _parse_frontend_hash_by_page

    parsed = _parse_frontend_hash_by_page()

    assert parsed is not None
    assert parsed["signals"] == "#signals"
    assert parsed["hub"] == "#ai-hub"
    assert len(parsed) >= 12


def test_parse_frontend_page_titles_reads_real_file() -> None:
    from backend.ai.market_tools import _parse_frontend_page_titles

    parsed = _parse_frontend_page_titles()

    assert parsed is not None
    assert parsed["signals"] == "Historical Signals"
    assert parsed["calendar"] == "Earnings & Events"


def test_application_help_falls_back_when_frontend_source_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("backend.ai.market_tools._parse_frontend_hash_by_page", lambda: None)
    monkeypatch.setattr("backend.ai.market_tools._parse_frontend_page_titles", lambda: None)

    result = get_application_help_tool(ApplicationHelpRequest(query="where are my alerts"))

    assert result.matches[0]["title"] == "Alerts"
    assert result.matches[0]["route"] == "#alerts"
    assert any("fallback" in warning.lower() for warning in result.warnings)


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
        trend_engines: dict = {}

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
    assert result.provider == "MarketLens engine"


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
    assert result.provider == "webull"


def test_market_regime_tool_reports_provider_and_fallback(monkeypatch) -> None:
    import importlib

    class _FakeTrendEngine:
        def get_timeframe_metadata(self, tf):
            return {"provider": "yfinance"}

    class _FakeRegimeEngine:
        trend_engine = _FakeTrendEngine()

        def get_current_regime(self):
            return None

    regime_router_module = importlib.import_module("backend.api.regime.router")
    monkeypatch.setattr(regime_router_module, "get_engine", lambda symbol: _FakeRegimeEngine())

    from backend.config.settings import settings

    monkeypatch.setattr(settings.market_data, "primary_provider", "webull")

    result = get_market_regime_tool(SymbolRequest(symbol="AAPL"))

    assert result.provider == "yfinance"
    assert result.fallback is True


def test_market_context_tool_reports_composite_provider(monkeypatch) -> None:
    import importlib

    class _FakeContextEngine:
        def get_current_context(self):
            return None

    market_context_module = importlib.import_module("backend.api.market_context.router")
    monkeypatch.setattr(market_context_module, "get_engine", lambda: _FakeContextEngine())

    result = get_market_context_tool(SymbolRequest(symbol="AAPL"))

    assert result.regime == "unknown"
    assert result.provider == "MarketLens engine"


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


class _FakeSessionManager:
    """Two calendar days of bars; day 2 mixes premarket and regular
    sessions so scoping (latest date, then requested session) is
    actually exercised, not just the fetch/derive plumbing.
    """

    def get_historical_bars(self, symbol, timeframe, range_, include_extended_hours):
        del range_, include_extended_hours
        return [
            Bar(symbol=symbol, timestamp=datetime(2026, 9, 21, 10), open=50, high=51, low=49, close=50.5, volume=500, timeframe=timeframe, provider="test", data_status=DataStatus.HISTORICAL, session="regular"),
            Bar(symbol=symbol, timestamp=datetime(2026, 9, 22, 8, 0), open=100, high=101, low=99, close=100.5, volume=200, timeframe=timeframe, provider="test", data_status=DataStatus.HISTORICAL, session="premarket"),
            Bar(symbol=symbol, timestamp=datetime(2026, 9, 22, 9, 30), open=101, high=105, low=100, close=104, volume=1000, timeframe=timeframe, provider="test", data_status=DataStatus.HISTORICAL, session="regular"),
            Bar(symbol=symbol, timestamp=datetime(2026, 9, 22, 15, 59), open=104, high=106, low=103, close=105, volume=1500, timeframe=timeframe, provider="test", data_status=DataStatus.HISTORICAL, session="regular"),
        ]


def test_session_stats_scopes_to_latest_date_and_requested_session(monkeypatch) -> None:
    monkeypatch.setattr("backend.ai.market_tools._manager", lambda: _FakeSessionManager())

    result = get_session_stats_tool(SessionStatsRequest(symbol="AAPL", session="regular"))

    assert result.available is True
    assert result.date == "2026-09-22"
    assert result.open == 101
    assert result.close == 105
    assert result.high == 106
    assert result.low == 100
    assert result.volume == 2500
    assert result.range == 6
    assert result.bar_count == 2


def test_session_stats_scopes_to_premarket_only(monkeypatch) -> None:
    monkeypatch.setattr("backend.ai.market_tools._manager", lambda: _FakeSessionManager())

    result = get_session_stats_tool(SessionStatsRequest(symbol="AAPL", session="premarket"))

    assert result.available is True
    assert result.open == 100
    assert result.close == 100.5
    assert result.bar_count == 1


def test_session_stats_all_sessions_spans_the_whole_day(monkeypatch) -> None:
    monkeypatch.setattr("backend.ai.market_tools._manager", lambda: _FakeSessionManager())

    result = get_session_stats_tool(SessionStatsRequest(symbol="AAPL", session="all"))

    assert result.available is True
    assert result.open == 100  # premarket bar is chronologically first
    assert result.close == 105
    assert result.volume == 200 + 1000 + 1500
    assert result.bar_count == 3


def test_session_stats_reports_unavailable_for_missing_session(monkeypatch) -> None:
    monkeypatch.setattr("backend.ai.market_tools._manager", lambda: _FakeSessionManager())

    result = get_session_stats_tool(SessionStatsRequest(symbol="AAPL", session="after_hours"))

    assert result.available is False
    assert "after_hours" in result.reason
