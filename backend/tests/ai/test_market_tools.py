from datetime import UTC, datetime, timedelta

from backend.ai.market_tools import (
    AlertsRequest,
    AnomalyAnalysisRequest,
    ApplicationHelpRequest,
    AssumptionTrackingRequest,
    BarsRequest,
    ChangeAnalysisRequest,
    ComparisonRequest,
    ConfluenceRequest,
    CounterargumentRequest,
    CsvImportRequest,
    DecisionChecklistRequest,
    ExportReportRequest,
    HistoricalSimilarityRequest,
    IndicatorRequest,
    JournalCoachRequest,
    JournalEntryInput,
    MarketEventTimelineRequest,
    MoveAnalysisRequest,
    OptionLegRef,
    OptionsResearchRequest,
    OptionsSpreadRef,
    PortfolioRiskLimits,
    PortfolioRiskRequest,
    PositionInput,
    ProposedTrade,
    RiskDashboardRequest,
    SaveToJournalRequest,
    ScenarioRequest,
    SensitivityRequest,
    SessionStatsRequest,
    SignalExplanationRequest,
    SymbolRequest,
    TapeRequest,
    TradeJournalRequest,
    TradePlanRequest,
    TrendRequest,
    anomaly_analysis_tool,
    assess_portfolio_risk_tool,
    assumption_tracking_tool,
    build_trade_plan_tool,
    compare_symbols_tool,
    counterargument_review_tool,
    decision_checklist_tool,
    export_report_tool,
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
    market_event_timeline_tool,
    options_research_tool,
    save_to_journal_tool,
    scenario_analysis_tool,
    sensitivity_analysis_tool,
    signal_explanation_tool,
    trade_journal_coach_tool,
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


def test_signal_explanation_reports_triggers_agreement_and_tape(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    now = datetime.now().astimezone().isoformat()

    def fake_trend(request):
        direction = "bearish" if request.timeframe == "1h" else "bullish"
        return _Payload(symbol=request.symbol, timeframe=request.timeframe, direction=direction, confidence=0.8, data_age_seconds=5, provider="test", timestamp=now)

    monkeypatch.setattr("backend.ai.market_tools.get_trend_tool", fake_trend)
    monkeypatch.setattr("backend.ai.market_tools.get_confluence_tool", lambda request: _Payload(symbol=request.symbol, direction="bullish", strength=0.8, alignment_score=0.67, provider="test", timestamp=now))
    monkeypatch.setattr("backend.ai.market_tools.get_tape_state_tool", lambda request: _Payload(symbol=request.symbol, provider="webull", source_timestamp=now, snapshot={"pressure": "buy"}))
    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(
            symbol=request.symbol,
            provider="test",
            source_timestamp=now,
            bars=[{"close": 100 + index, "volume": 1_000 if index < 20 else 2_000} for index in range(25)],
        ),
    )

    result = signal_explanation_tool(SignalExplanationRequest(symbol="AAPL", timeframes=["5m", "15m", "1h"]))

    assert result.direction == "bullish"
    assert result.timeframe_agreement["bullish"] == 2
    assert result.timeframe_agreement["bearish"] == 1
    assert result.tape_relation["confirms"] is True
    assert any(trigger["indicator"] == "rsi_14" for trigger in result.triggers)
    assert result.conclusion["status"] == "verified_explanation"


def test_counterargument_review_only_surfaces_available_opposing_evidence(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.signal_explanation_tool",
        lambda request: _Payload(
            symbol="AAPL",
            direction="bullish",
            indicators={"sma_20": 100},
            triggers=[{"indicator": "macd", "direction": "bullish"}],
            timeframe_agreement={"dominant": "bullish", "bullish": 2, "bearish": 1, "timeframes": [{"timeframe": "1h", "direction": "bearish", "confidence": 0.4}]},
            tape_relation={"contradicts": True},
            tape={"direction": "bearish"},
            freshness={"status": "fresh"},
            sources=[],
            unknowns=[],
        ),
    )

    result = counterargument_review_tool(CounterargumentRequest(symbol="AAPL"))

    assert result.direction == "bullish"
    assert any(item["type"] == "timeframe_conflict" for item in result.counterarguments)
    assert any(item["type"] == "tape_conflict" for item in result.counterarguments)
    assert result.invalidations[0]["condition"] == "close_below"
    assert result.conclusion["status"] == "verified_review"


def test_sensitivity_analysis_varies_one_factor_at_a_time() -> None:
    result = sensitivity_analysis_tool(
        SensitivityRequest(
            entry_price=100,
            stop_price=95,
            target_price=110,
            quantity=100,
            portfolio_value=100_000,
            entry_prices=[105],
            stop_prices=[90],
            quantities=[200],
        )
    )

    assert result.available is True
    assert result.base["risk_dollars"] == 500
    assert len(result.scenarios) == 3
    assert result.scenarios[0]["case"] == "entry"
    assert result.scenarios[1]["risk_dollars"] == 1000
    assert result.scenarios[2]["allocation_percent"] == 20.0
    assert result.conclusion["status"] == "verified_sensitivity"


def test_market_event_timeline_normalizes_and_orders_events(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(
            symbol="AAPL",
            provider="webull",
            source_timestamp="2026-09-22T16:00:00-04:00",
            bars=[
                {"timestamp": "2026-09-22T09:30:00-04:00", "session": "regular", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
                {"timestamp": "2026-09-22T09:31:00-04:00", "session": "regular", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1200},
            ],
        ),
    )
    monkeypatch.setattr("backend.ai.market_tools.get_confluence_tool", lambda request: _Payload(symbol="AAPL", timestamp="2026-09-22T10:00:00-04:00", direction="bullish", provider="engine"))
    monkeypatch.setattr("backend.ai.market_tools.get_alerts_tool", lambda request: _Payload(provider="db", alerts=[{"name": "breakout", "recent_triggers": [{"triggered_at": "2026-09-22T09:45:00-04:00", "observed_value": 101}]}]))
    monkeypatch.setattr("backend.ai.market_tools.get_news_tool", lambda request: _Payload(provider="news", source_timestamp="2026-09-22T09:40:00-04:00", items=[{"timestamp": "2026-09-22T09:40:00-04:00", "headline": "AAPL update"}]))
    monkeypatch.setattr("backend.ai.market_tools.get_calendar_tool", lambda request: _Payload(provider="yfinance", events=[]))
    monkeypatch.setattr("backend.ai.market_tools.get_fundamentals_tool", lambda request: _Payload(provider="finnhub", source_timestamp="2026-09-22T08:00:00-04:00", data={"recommendation": "buy", "analyst_target": 120, "insider_ownership": 0.02}))
    monkeypatch.setattr("backend.ai.market_tools.get_options_tool", lambda request: _Payload(provider="yahoo_finance", source_timestamp="2026-09-22T09:00:00-04:00", chains=[]))

    result = market_event_timeline_tool(MarketEventTimelineRequest(symbol="AAPL", start="2026-09-22T09:30:00-04:00"))

    assert result.conclusion["status"] == "verified_timeline"
    assert result.events[0]["timestamp"].endswith("-04:00")
    assert any(event["type"] == "session_transition" for event in result.events)
    assert any(event["type"] == "signal" for event in result.events)
    assert any(event["type"] == "alert" for event in result.events)
    assert any(event["type"] == "news" for event in result.events)


def test_anomaly_analysis_reports_baseline_deviations_and_corroboration(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    closes = [100 + index for index in range(24)] + [160]
    bars = [{"close": close, "volume": 100 + index * 5} for index, close in enumerate(closes)]
    monkeypatch.setattr("backend.ai.market_tools.get_bars_tool", lambda request: _Payload(symbol=request.symbol, provider="test", source_timestamp="2026-09-22T16:00:00-04:00", bars=bars))
    monkeypatch.setattr("backend.ai.market_tools.get_quote_tool", lambda request: _Payload(symbol=request.symbol, provider="webull", timestamp="2026-09-22T16:00:00-04:00", bid=100, ask=101, price=100))
    monkeypatch.setattr("backend.ai.market_tools.get_tape_state_tool", lambda request: _Payload(symbol=request.symbol, provider="webull", source_timestamp="now", snapshot={"large_prints": [{"size": 10}], "pressure": "buy"}))
    monkeypatch.setattr("backend.ai.market_tools.get_options_tool", lambda request: _Payload(symbol=request.symbol, provider="yahoo_finance", source_timestamp="now", chains=[{"unusual_activity": "unusual"}]))

    result = anomaly_analysis_tool(
        AnomalyAnalysisRequest(
            symbol="AAPL",
            baseline_bars=20,
            positions=[PositionInput(symbol="AAPL", quantity=100, entry_price=100, current_price=100)],
        )
    )

    types = {anomaly["type"] for anomaly in result.anomalies}
    assert "price_return" in types
    assert "spread" in types
    assert "large_prints" in types
    assert "options_activity" in types
    assert "portfolio_concentration" in types
    assert result.conclusion["status"] == "verified_anomalies"


def test_assumption_tracking_preserves_originals_and_marks_contradiction() -> None:
    saved = assumption_tracking_tool(
        AssumptionTrackingRequest(
            operation="save",
            symbol="AAPL",
            assumptions=[
                {
                    "category": "growth",
                    "statement": "Expected growth is 10%",
                    "expected_value": 10,
                    "unit": "%",
                    "source": "user thesis",
                }
            ],
        )
    ).model_dump()
    record = saved["assumptions"][0]
    assert record["status"] == "active"
    assert record["original_value"] == 10
    assert record["original_created_at"] == record["created_at"]

    reviewed = assumption_tracking_tool(
        AssumptionTrackingRequest(
            operation="review",
            existing_assumptions=saved["assumptions"],
            evidence=[
                {
                    "assumption_id": record["id"],
                    "observed_value": 25,
                    "source": "verified earnings",
                    "contradicts": True,
                }
            ],
        )
    ).model_dump()
    updated = reviewed["assumptions"][0]
    assert updated["status"] == "broken"
    assert updated["original_statement"] == record["original_statement"]
    assert updated["original_value"] == 10
    assert updated["original_source"] == "user thesis"
    assert reviewed["changed"][0]["to"] == "broken"


def test_assumption_tracking_marks_old_unverified_record_stale() -> None:
    old = {
        "id": "a-old",
        "symbol": "MSFT",
        "category": "stop",
        "statement": "Stop is 400",
        "expected_value": 400,
        "source": "user",
        "created_at": "2020-01-01T00:00:00+00:00",
        "status": "active",
        "original_statement": "Stop is 400",
        "original_value": 400,
        "original_source": "user",
        "original_created_at": "2020-01-01T00:00:00+00:00",
        "stale_after_hours": 24,
    }
    result = assumption_tracking_tool(
        AssumptionTrackingRequest(operation="review", existing_assumptions=[old])
    ).model_dump()
    assert result["assumptions"][0]["status"] == "stale"


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


def test_build_trade_plan_computes_reward_risk_and_position_size_via_calculator() -> None:
    result = build_trade_plan_tool(
        TradePlanRequest(
            symbol="aapl",
            direction="long",
            entry_price=200,
            stop_price=190,
            targets=[220, 230],
            account_value=100_000,
            risk_percent=1,
            timeframe="1d",
            session="regular",
            catalysts=["Earnings next week"],
            risks=["Broad market pullback"],
        )
    ).model_dump()

    assert result["symbol"] == "AAPL"
    assert result["entry_reference"] == 200
    assert result["stop_price"] == 190
    # risk = 10, reward = 20 -> risk_reward = 2; second target risk_reward = 3
    assert result["targets"][0]["price"] == 220
    assert result["targets"][0]["risk"] == 10
    assert result["targets"][0]["reward"] == 20
    assert result["targets"][0]["risk_reward"] == 2
    assert result["targets"][1]["risk_reward"] == 3
    # position size: risk_dollars = 100000 * 1% = 1000; shares = 1000 / 10 = 100
    assert result["position_size"]["shares"] == 100
    assert result["position_size_reason"] is None
    assert "abs(entry_price - stop_price)" in result["formulas"]
    assert result["catalysts"] == ["Earnings next week"]
    assert result["risks"] == ["Broad market pullback"]
    assert "190" in result["invalidation"] or "closes below" in result["invalidation"]


def test_build_trade_plan_supports_entry_zone_and_short_direction() -> None:
    result = build_trade_plan_tool(
        TradePlanRequest(
            symbol="TSLA",
            direction="short",
            entry_zone_low=200,
            entry_zone_high=210,
            stop_price=220,
            targets=[180],
        )
    ).model_dump()

    assert result["entry_reference"] == 205
    assert result["entry_zone"] == {"low": 200, "high": 210}
    assert result["targets"][0]["risk"] == 15
    assert result["targets"][0]["reward"] == 25


def test_build_trade_plan_reports_reason_when_sizing_inputs_missing() -> None:
    result = build_trade_plan_tool(
        TradePlanRequest(symbol="MSFT", direction="long", entry_price=100, stop_price=95, targets=[110])
    ).model_dump()

    assert result["position_size"] is None
    assert "account_value" in result["position_size_reason"]
    assert "No catalysts were supplied." in result["assumptions"]


def test_build_trade_plan_generates_default_invalidation_when_not_supplied() -> None:
    result = build_trade_plan_tool(
        TradePlanRequest(symbol="MSFT", direction="short", entry_price=100, stop_price=105, targets=[90])
    ).model_dump()

    assert "MSFT" in result["invalidation"]
    assert "above" in result["invalidation"]
    assert "105" in result["invalidation"]


def test_build_trade_plan_requires_stop_and_target() -> None:
    try:
        build_trade_plan_tool(TradePlanRequest(symbol="AAPL", direction="long", entry_price=200, targets=[210]))
        raise AssertionError("expected ValueError for missing stop_price")
    except ValueError as exc:
        assert "stop_price" in str(exc)

    try:
        build_trade_plan_tool(TradePlanRequest(symbol="AAPL", direction="long", entry_price=200, stop_price=190))
        raise AssertionError("expected ValueError for missing targets")
    except ValueError as exc:
        assert "target" in str(exc)


def test_build_trade_plan_rejects_inconsistent_direction() -> None:
    # A long plan with the stop above entry is nonsensical -- must raise,
    # not silently build a backwards plan.
    try:
        build_trade_plan_tool(
            TradePlanRequest(symbol="AAPL", direction="long", entry_price=200, stop_price=210, targets=[220])
        )
        raise AssertionError("expected ValueError for stop above entry on a long plan")
    except ValueError as exc:
        assert "long" in str(exc)


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
        "options": "#options",
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


def _monotonic_bars(closes: list[float]) -> list[dict]:
    return [{"close": close, "volume": 1000} for close in closes]


def test_portfolio_risk_reports_unavailable_without_positions() -> None:
    result = assess_portfolio_risk_tool(PortfolioRiskRequest())
    assert result.available is False
    assert "browser" in result.reason


def test_portfolio_risk_explains_concentration_sector_correlation_volatility_drawdown(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    # Identical, strictly increasing series for both symbols: forces
    # correlation == 1.0 exactly, equal volatility for both, and a
    # zero-drawdown portfolio equity curve (never below a prior peak) --
    # all cleanly assertable without approximate-equality fuzz.
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(symbol=request.symbol, provider="test", source_timestamp="2026-09-22T16:00:00-04:00", bars=_monotonic_bars(closes)),
    )
    positions = [
        PositionInput(symbol="AAPL", quantity=10, entry_price=90, current_price=115, sector="Technology"),
        PositionInput(symbol="MSFT", quantity=10, entry_price=90, current_price=115, sector="Technology"),
    ]

    result = assess_portfolio_risk_tool(PortfolioRiskRequest(positions=positions, lookback_days=20)).model_dump()

    assert result["available"] is True
    assert result["concentration"]["top_3_weight_percent"] == 100.0
    assert result["sector_exposure"][0]["sector"] == "Technology"
    assert result["volatility"]["AAPL"] == result["volatility"]["MSFT"]
    assert result["volatility"]["AAPL"] > 0
    assert len(result["correlation_matrix"]) == 1
    assert result["correlation_matrix"][0]["correlation"] == 1.0
    assert result["portfolio_drawdown"]["maximum_drawdown_percent"] == 0.0
    assert result["scenario"]["symbol_count"] if "symbol_count" in result["scenario"] else True
    assert "gross_exposure" in result


def test_portfolio_risk_proposed_trade_requires_entry_and_stop(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(symbol=request.symbol, provider="test", source_timestamp="now", bars=_monotonic_bars([100, 101, 102, 103, 104])),
    )
    positions = [PositionInput(symbol="AAPL", quantity=10, entry_price=100, current_price=110)]

    result = assess_portfolio_risk_tool(PortfolioRiskRequest(
        positions=positions,
        proposed_trade=ProposedTrade(symbol="TSLA"),
    )).model_dump()

    assert result["proposed_trade"]["recommended_size"] is None
    assert "entry_price" in result["proposed_trade"]["reason"]


def test_portfolio_risk_proposed_trade_requires_sizing_inputs(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(symbol=request.symbol, provider="test", source_timestamp="now", bars=_monotonic_bars([100, 101, 102, 103, 104])),
    )
    positions = [PositionInput(symbol="AAPL", quantity=10, entry_price=100, current_price=110)]

    result = assess_portfolio_risk_tool(PortfolioRiskRequest(
        positions=positions,
        proposed_trade=ProposedTrade(symbol="TSLA", entry_price=200, stop_price=190),
    )).model_dump()

    assert result["proposed_trade"]["recommended_size"] is None
    assert "account_value" in result["proposed_trade"]["reason"]


def test_portfolio_risk_sizes_proposed_trade_without_limits(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(symbol=request.symbol, provider="test", source_timestamp="now", bars=_monotonic_bars([100, 101, 102, 103, 104])),
    )
    positions = [PositionInput(symbol="AAPL", quantity=10, entry_price=100, current_price=110)]

    result = assess_portfolio_risk_tool(PortfolioRiskRequest(
        positions=positions,
        proposed_trade=ProposedTrade(symbol="TSLA", entry_price=200, stop_price=190, account_value=50_000, risk_percent=2),
    )).model_dump()

    # risk_dollars = 50000 * 2% = 1000; shares = 1000 / 10 = 100
    assert result["proposed_trade"]["recommended_size"] == 100
    assert "No risk_limits" in result["proposed_trade"]["reason"]


def test_portfolio_risk_refuses_size_that_breaches_max_position_limit(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(symbol=request.symbol, provider="test", source_timestamp="now", bars=_monotonic_bars([100, 101, 102, 103, 104])),
    )
    positions = [PositionInput(symbol="AAPL", quantity=10, entry_price=100, current_price=110)]

    result = assess_portfolio_risk_tool(PortfolioRiskRequest(
        positions=positions,
        proposed_trade=ProposedTrade(symbol="TSLA", entry_price=200, stop_price=190, account_value=50_000, risk_percent=2),
        risk_limits=PortfolioRiskLimits(max_position_percent=5),
    )).model_dump()

    assert result["proposed_trade"]["recommended_size"] is None
    assert "max-position limit" in result["proposed_trade"]["reason"]
    assert result["proposed_trade"]["computed_shares"] == 100


def _fake_options_chain(expiration: str = "2099-12-31") -> dict:
    return {
        "symbol": "AAPL",
        "expiration": expiration,
        "calls": [
            {"strike": 200, "expiration": expiration, "option_type": "call", "bid": 5.0, "ask": 5.4, "last": 5.2, "volume": 100, "open_interest": 500, "implied_volatility": 0.3, "delta": 0.55, "in_the_money": True},
            {"strike": 210, "expiration": expiration, "option_type": "call", "bid": 2.0, "ask": 2.4, "last": 2.2, "volume": 80, "open_interest": 300, "implied_volatility": 0.28, "delta": 0.35, "in_the_money": False},
        ],
        "puts": [
            {"strike": 190, "expiration": expiration, "option_type": "put", "bid": 3.0, "ask": 3.4, "last": None, "volume": 60, "open_interest": 200, "implied_volatility": 0.32, "delta": -0.3, "in_the_money": False},
        ],
        "put_call_ratio": 0.5,
        "total_call_volume": 180,
        "total_put_volume": 60,
        "avg_iv_call": 0.29,
        "avg_iv_put": 0.32,
        "unusual_activity": "normal",
    }


def _patch_options_tools(monkeypatch, chains=None, quote_price=205.0):
    from backend.ai.market_tools import _Payload

    chains = chains if chains is not None else [_fake_options_chain()]
    monkeypatch.setattr(
        "backend.ai.market_tools.get_options_tool",
        lambda request: _Payload(
            symbol="AAPL", chains=chains, expirations=[c["expiration"] for c in chains],
            near_term_iv=0.3, iv_rank=45, provider="yahoo_finance",
            source_timestamp="2026-09-22T16:00:00-04:00", fallback=False,
        ),
    )
    monkeypatch.setattr(
        "backend.ai.market_tools.get_quote_tool",
        lambda request: _Payload(symbol="AAPL", price=quote_price, provider="webull", timestamp="2026-09-22T16:00:00-04:00"),
    )


def test_options_research_reports_unavailable_without_a_chain(monkeypatch) -> None:
    _patch_options_tools(monkeypatch, chains=[])
    result = options_research_tool(OptionsResearchRequest(symbol="AAPL"))
    assert result.available is False
    assert "AAPL" in result.reason


def test_options_research_chain_summary_and_expected_move(monkeypatch) -> None:
    _patch_options_tools(monkeypatch)
    result = options_research_tool(OptionsResearchRequest(symbol="AAPL")).model_dump()

    assert result["available"] is True
    summary = result["chain_summary"]
    assert summary["expiration"] == "2099-12-31"
    assert summary["put_call_ratio"] == 0.5
    assert summary["iv_rank"] == 45
    assert summary["near_expiration_risk"] is False
    assert summary["expected_move"] is not None
    assert summary["expected_move"]["expected_move"] > 0


def test_options_research_explains_a_leg_with_mid_price_and_greeks(monkeypatch) -> None:
    _patch_options_tools(monkeypatch)
    result = options_research_tool(OptionsResearchRequest(
        symbol="AAPL",
        legs=[OptionLegRef(expiration="2099-12-31", strike=200, option_type="call")],
    )).model_dump()

    leg = result["legs"][0]
    assert leg["premium"] == 5.2  # mid(5.0, 5.4)
    assert leg["premium_source"] == "mid_bid_ask"
    assert leg["delta"] == 0.55
    assert leg["breakeven"] == 205.2  # strike + premium for a call
    # underlying=205: intrinsic = max(205-200,0) = 5; extrinsic = premium-intrinsic = 0.2
    assert leg["intrinsic_value"] == 5
    assert round(leg["extrinsic_value"], 4) == 0.2


def test_options_research_falls_back_to_last_price_when_no_bid_ask(monkeypatch) -> None:
    # The fake put has bid/ask but last=None -- flip it to prove the "last"
    # fallback path, not just mid-bid-ask.
    chain = _fake_options_chain()
    chain["puts"][0]["bid"] = None
    chain["puts"][0]["ask"] = None
    chain["puts"][0]["last"] = 3.1
    _patch_options_tools(monkeypatch, chains=[chain])

    result = options_research_tool(OptionsResearchRequest(
        symbol="AAPL",
        legs=[OptionLegRef(expiration="2099-12-31", strike=190, option_type="put")],
    )).model_dump()

    assert result["legs"][0]["premium"] == 3.1
    assert result["legs"][0]["premium_source"] == "last"


def test_options_research_reports_unmatched_leg_as_unknown_not_guessed(monkeypatch) -> None:
    _patch_options_tools(monkeypatch)
    result = options_research_tool(OptionsResearchRequest(
        symbol="AAPL",
        legs=[OptionLegRef(expiration="2099-12-31", strike=999, option_type="call")],
    )).model_dump()

    assert result["legs"] == []
    assert any(item["type"] == "leg" for item in result["unknowns"])


def test_options_research_computes_a_bull_call_debit_spread(monkeypatch) -> None:
    _patch_options_tools(monkeypatch)
    result = options_research_tool(OptionsResearchRequest(
        symbol="AAPL",
        spreads=[OptionsSpreadRef(
            long=OptionLegRef(expiration="2099-12-31", strike=200, option_type="call"),
            short=OptionLegRef(expiration="2099-12-31", strike=210, option_type="call"),
        )],
    )).model_dump()

    spread = result["spreads"][0]
    # long premium mid=5.2, short premium mid=2.2 -> net_debit=3.0, width=10
    assert spread["net_debit"] == 3.0
    assert spread["max_gain_per_share"] == 7.0
    assert spread["max_loss_per_share"] == 3.0
    assert spread["breakeven"] == 203.0
    assert spread["short_leg_assignment_exposure"]["assignment_shares"] == 100


def test_options_research_rejects_mismatched_spread_option_types(monkeypatch) -> None:
    _patch_options_tools(monkeypatch)
    result = options_research_tool(OptionsResearchRequest(
        symbol="AAPL",
        spreads=[OptionsSpreadRef(
            long=OptionLegRef(expiration="2099-12-31", strike=200, option_type="call"),
            short=OptionLegRef(expiration="2099-12-31", strike=190, option_type="put"),
        )],
    )).model_dump()

    assert result["spreads"] == []
    assert any(item["type"] == "spread" for item in result["unknowns"])


def test_options_research_flags_near_expiration_risk(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    soon = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    _patch_options_tools(monkeypatch, chains=[_fake_options_chain(expiration=soon)])

    result = options_research_tool(OptionsResearchRequest(symbol="AAPL")).model_dump()

    assert result["chain_summary"]["near_expiration_risk"] is True


def test_journal_coach_reports_unavailable_without_entries() -> None:
    result = trade_journal_coach_tool(JournalCoachRequest())
    assert result.available is False
    assert "browser" in result.reason


def _coach_entries() -> list[dict]:
    return [
        # AAPL: win, has stop + planned_target, exits before target, evidence attached.
        {"symbol": "AAPL", "side": "long", "status": "closed", "entry_price": 100, "exit_price": 110,
         "quantity": 10, "stop_price": 95, "planned_entry": 99, "planned_target": 115, "setup": "breakout",
         "signals": ["rsi_oversold"], "plan": {"note": "breakout continuation"}},
        # MSFT: loss, blew through planned_stop, untagged, no evidence.
        {"symbol": "MSFT", "side": "long", "status": "closed", "entry_price": 50, "exit_price": 40,
         "quantity": 5, "planned_stop": 45},
        # TSLA: missing exit_price -> skipped from pricing entirely.
        {"symbol": "TSLA", "side": "long", "status": "closed", "entry_price": 200, "quantity": 2},
        # NVDA: win, but no stop recorded at all -> no_stop_defined observation, excluded from R-multiples.
        {"symbol": "NVDA", "side": "long", "status": "closed", "entry_price": 300, "exit_price": 310, "quantity": 1},
        # AMD: win, position 2x the size the stated risk budget implies.
        {"symbol": "AMD", "side": "long", "status": "closed", "entry_price": 100, "exit_price": 105,
         "quantity": 20, "stop_price": 90, "account_value": 10_000, "risk_percent": 1},
        # SPY: winning short, closes before reaching planned_target, same setup as AAPL.
        {"symbol": "SPY", "side": "short", "status": "closed", "entry_price": 400, "exit_price": 390,
         "quantity": 10, "stop_price": 410, "planned_target": 385, "setup": "breakout"},
        # GOOG: still open -- must not affect closed-trade stats at all.
        {"symbol": "GOOG", "side": "long", "status": "open", "entry_price": 150, "quantity": 5},
    ]


def test_journal_coach_computes_win_rate_expectancy_and_r_multiple() -> None:
    result = trade_journal_coach_tool(JournalCoachRequest(entries=_coach_entries())).model_dump()

    assert result["available"] is True
    assert result["total_entries"] == 7
    assert result["closed_entries"] == 6
    assert result["priced_closed_entries"] == 5
    assert len(result["skipped_entries"]) == 1
    assert result["skipped_entries"][0]["symbol"] == "TSLA"

    assert result["win_rate_percent"] == 80.0
    assert result["expectancy_per_trade"] == 52.0
    assert result["average_r_multiple"] == 0.375


def test_journal_coach_groups_setup_performance() -> None:
    result = trade_journal_coach_tool(JournalCoachRequest(entries=_coach_entries())).model_dump()
    by_setup = {row["setup"]: row for row in result["setup_performance"]}

    assert by_setup["breakout"]["trade_count"] == 2
    assert by_setup["breakout"]["win_rate_percent"] == 100.0
    assert by_setup["breakout"]["expectancy_per_trade"] == 100.0
    assert by_setup["untagged"]["trade_count"] == 3
    assert round(by_setup["untagged"]["win_rate_percent"], 2) == 66.67


def test_journal_coach_flags_recurring_observations_not_advice() -> None:
    result = trade_journal_coach_tool(JournalCoachRequest(entries=_coach_entries())).model_dump()
    observations = result["observations"]

    # NVDA has no stop at all; TSLA also has no stop (on top of missing
    # exit_price, which separately puts it in skipped_entries) -- both are
    # real risk-management gaps regardless of whether pricing succeeded.
    assert observations["no_stop_defined"]["count"] == 2
    assert {e["symbol"] for e in observations["no_stop_defined"]["entries"]} == {"NVDA", "TSLA"}
    assert observations["exceeded_planned_stop"]["count"] == 1
    assert observations["exceeded_planned_stop"]["entries"][0]["symbol"] == "MSFT"
    assert observations["exited_before_target"]["count"] == 2
    assert {e["symbol"] for e in observations["exited_before_target"]["entries"]} == {"AAPL", "SPY"}
    assert observations["position_larger_than_risk_budget"]["count"] == 1
    assert observations["position_larger_than_risk_budget"]["entries"][0]["symbol"] == "AMD"
    assert any("not trading advice" in a for a in result["assumptions"])


def test_journal_coach_reports_plan_vs_actual_with_evidence_flags() -> None:
    result = trade_journal_coach_tool(JournalCoachRequest(entries=_coach_entries())).model_dump()
    by_symbol = {row["symbol"]: row for row in result["plan_vs_actual"]}

    assert set(by_symbol) == {"AAPL", "MSFT", "SPY"}
    assert by_symbol["AAPL"]["exit_classification"] == "closed_early"
    assert by_symbol["AAPL"]["planned_entry"] == 99
    assert by_symbol["AAPL"]["evidence_attached"] == {"signals": True, "market_conditions": False, "calculations": False, "plan": True}
    assert by_symbol["MSFT"]["exit_classification"] == "exceeded_planned_stop"
    assert by_symbol["MSFT"]["evidence_attached"] == {"signals": False, "market_conditions": False, "calculations": False, "plan": False}
    assert by_symbol["SPY"]["exit_classification"] == "closed_early"


def test_journal_coach_filters_by_symbol_and_setup() -> None:
    entries = _coach_entries()
    by_symbol = trade_journal_coach_tool(JournalCoachRequest(entries=entries, symbol="AAPL")).model_dump()
    assert by_symbol["total_entries"] == 1
    assert by_symbol["closed_entries"] == 1

    by_setup = trade_journal_coach_tool(JournalCoachRequest(entries=entries, setup="breakout")).model_dump()
    assert by_setup["total_entries"] == 2
    assert {row["symbol"] for row in by_setup["plan_vs_actual"]} == {"AAPL", "SPY"}


def _patch_checklist_tools(monkeypatch, *, trend_direction="uptrend", earnings_dates=None,
                            quote_age_seconds=5, option_oi=500, option_volume=50):
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_trend_tool",
        lambda request: _Payload(symbol=request.symbol, direction=trend_direction, strength="strong", provider="engine"),
    )
    events = [{"symbol": "AAPL", "event_type": "earnings", "date": d, "source": "yfinance"} for d in (earnings_dates or [])]
    monkeypatch.setattr(
        "backend.ai.market_tools.get_calendar_tool",
        lambda request: _Payload(symbol=request.symbol, events=events, provider="yfinance"),
    )
    quote_timestamp = (datetime.now(UTC) - timedelta(seconds=quote_age_seconds)).isoformat()
    monkeypatch.setattr(
        "backend.ai.market_tools.get_quote_tool",
        lambda request: _Payload(symbol=request.symbol, price=205, timestamp=quote_timestamp, provider="webull"),
    )
    chain = {
        "expiration": "2099-12-31",
        "calls": [{"strike": 210, "expiration": "2099-12-31", "option_type": "call", "open_interest": option_oi, "volume": option_volume}],
        "puts": [],
    }
    monkeypatch.setattr(
        "backend.ai.market_tools.get_options_tool",
        lambda request: _Payload(symbol=request.symbol, chains=[chain], expirations=["2099-12-31"], provider="yahoo_finance", source_timestamp="now"),
    )


def test_decision_checklist_all_checks_pass(monkeypatch) -> None:
    _patch_checklist_tools(monkeypatch, trend_direction="uptrend", earnings_dates=[])
    result = decision_checklist_tool(DecisionChecklistRequest(
        symbol="AAPL", direction="long",
        entry_price=200, stop_price=190, account_value=50_000, risk_percent=2,
        option_leg=OptionLegRef(expiration="2099-12-31", strike=210, option_type="call"),
    )).model_dump()

    assert result["ready"] is True
    assert result["counts"]["completed"] == 7
    assert result["counts"]["failed"] == 0
    checks = {c["check"]: c for c in result["checks"]}
    assert checks["trend_alignment"]["status"] == "completed"
    assert checks["verified_position_size"]["evidence"]["shares"] == 100


def test_decision_checklist_reports_failures_and_unavailable(monkeypatch) -> None:
    _patch_checklist_tools(monkeypatch, trend_direction="downtrend", earnings_dates=["2026-09-25"], quote_age_seconds=200)
    result = decision_checklist_tool(DecisionChecklistRequest(symbol="AAPL", direction="long")).model_dump()

    checks = {c["check"]: c for c in result["checks"]}
    assert result["ready"] is False
    assert checks["trend_alignment"]["status"] == "failed"
    assert checks["catalyst_review"]["status"] == "failed"
    assert checks["defined_stop"]["status"] == "failed"
    assert checks["verified_position_size"]["status"] == "unavailable"
    assert checks["earnings_risk"]["status"] == "unavailable"
    assert checks["options_liquidity"]["status"] == "unavailable"
    assert checks["data_freshness"]["status"] == "failed"


def test_decision_checklist_skips_unrequested_checks() -> None:
    result = decision_checklist_tool(DecisionChecklistRequest(
        symbol="AAPL", direction="long", stop_price=190, required_checks=["defined_stop"],
    )).model_dump()

    checks = {c["check"]: c for c in result["checks"]}
    assert checks["defined_stop"]["status"] == "completed"
    assert result["counts"]["skipped"] == 6
    for name in ("trend_alignment", "catalyst_review", "verified_position_size", "earnings_risk", "options_liquidity", "data_freshness"):
        assert checks[name]["status"] == "skipped"
    # ready is unaffected by skipped checks, only by failed ones.
    assert result["ready"] is True


def test_decision_checklist_flags_thin_options_liquidity(monkeypatch) -> None:
    _patch_checklist_tools(monkeypatch, option_oi=5, option_volume=1)
    result = decision_checklist_tool(DecisionChecklistRequest(
        symbol="AAPL", direction="long",
        option_leg=OptionLegRef(expiration="2099-12-31", strike=210, option_type="call"),
        required_checks=["options_liquidity"],
    )).model_dump()

    checks = {c["check"]: c for c in result["checks"]}
    assert checks["options_liquidity"]["status"] == "failed"
    assert checks["options_liquidity"]["evidence"]["open_interest"] == 5


def test_save_to_journal_validates_and_appends_a_typed_entry() -> None:
    existing = [{"id": "old-1", "symbol": "MSFT", "status": "closed"}]
    result = save_to_journal_tool(SaveToJournalRequest(
        existing_entries=existing,
        entry=JournalEntryInput(
            symbol="aapl", side="long", status="planned", entry_price=200, stop_price=190,
            target_price=220, setup="breakout", plan={"invalidation": "close below 190"},
        ),
    )).model_dump()

    assert result["total_entries"] == 2
    assert result["entries"][0] == existing[0]
    saved = result["saved_entry"]
    assert saved["symbol"] == "AAPL"
    assert saved["stop_price"] == 190
    assert saved["plan"] == {"invalidation": "close below 190"}
    assert saved["id"]
    assert saved["created_at"]
    assert result["entries"][1] == saved


def test_save_to_journal_does_not_touch_a_database() -> None:
    result = save_to_journal_tool(SaveToJournalRequest(entry=JournalEntryInput(symbol="AAPL"))).model_dump()
    assert any("does not write to a database" in a or "not a database write" in a for a in result["assumptions"])


def test_export_report_formats_a_verified_trade_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.ai.market_tools._parse_frontend_hash_by_page",
        lambda: {"symbol": "#symbol", "journal": "#journal"},
    )
    monkeypatch.setattr(
        "backend.ai.market_tools._parse_frontend_page_titles",
        lambda: {"symbol": "Symbol", "journal": "Journal"},
    )
    result = export_report_tool(ExportReportRequest(
        report_type="trade_plan",
        trade_plan=TradePlanRequest(symbol="AAPL", direction="long", entry_price=200, stop_price=190, targets=[220]),
    )).model_dump()

    assert result["report_type"] == "trade_plan"
    assert "AAPL" in result["title"]
    assert "Trade Plan" in result["content"]
    assert "220" in result["content"]
    assert result["deep_links"] == {"symbol": "#symbol", "journal": "#journal"}


def test_export_report_custom_wraps_existing_text_verbatim() -> None:
    result = export_report_tool(ExportReportRequest(
        report_type="custom", title="Weekly Review", content="Some analysis already shown to the trader.",
    )).model_dump()

    assert result["title"] == "Weekly Review"
    assert "Some analysis already shown to the trader." in result["content"]


def test_export_report_requires_the_matching_nested_field() -> None:
    try:
        export_report_tool(ExportReportRequest(report_type="trade_plan"))
        raise AssertionError("expected ValueError when trade_plan is missing")
    except ValueError as exc:
        assert "trade_plan" in str(exc)


def test_signal_history_tool_reads_recorded_signals_and_transitions() -> None:
    from datetime import datetime

    from backend.ai.market_tools import SignalHistoryRequest, get_signal_history_tool
    from backend.database import SessionLocal
    from backend.repositories.signal_repository import SignalRepository

    db = SessionLocal()
    created: list[int] = []
    try:
        repository = SignalRepository(db)
        for hour, state, outcome in ((10, "bearish", 1.2), (11, "bullish", None), (12, "bullish", None)):
            created.append(repository.create(
                symbol="ZZSIG", timestamp=datetime(2026, 9, 18, hour, 0), timeframe="1h",
                price=100.0 + hour, trend_state=state, trend_score=10.0, return_5b=outcome,
            ).id)

        result = get_signal_history_tool(SignalHistoryRequest(symbol="zzsig", timeframe="1h"))
        assert result.signal_count == 3
        assert result.signals[0]["timestamp"] == "2026-09-18T12:00:00-04:00"  # newest first
        assert result.source_timestamp == "2026-09-18T12:00:00-04:00"
        assert result.state_counts == {"bullish": 2, "bearish": 1}
        assert result.transitions == [{"timestamp": "2026-09-18T11:00:00-04:00", "symbol": "ZZSIG", "from": "bearish", "to": "bullish"}]
        assert [row["outcome_available"] for row in result.signals] == [False, False, True]

        scoped = get_signal_history_tool(SignalHistoryRequest(symbol="ZZSIG", start="2026-09-18T11:30:00", end="2026-09-18T23:59:59"))
        assert scoped.signal_count == 1

        empty = get_signal_history_tool(SignalHistoryRequest(symbol="ZZNONE"))
        assert empty.available is False
        assert empty.source_timestamp is None
    finally:
        for signal_id in created:
            row = repository.get_by_id(signal_id)
            if row is not None:
                db.delete(row)
        db.commit()
        db.close()


def test_saved_scans_tool_is_honest_without_a_browser_snapshot() -> None:
    from backend.ai.market_tools import SavedScansRequest, get_saved_scans_tool

    unavailable = get_saved_scans_tool(SavedScansRequest())
    assert unavailable.available is False
    assert "browser" in unavailable.reason
    assert unavailable.presets == []

    snapshot = get_saved_scans_tool(SavedScansRequest(
        presets=[{"name": "Breakout", "filters": [{"type": "rsi", "op": "<", "value": 30}], "match": "AND"}],
        name="breakout",
    ))
    assert snapshot.available is True
    assert snapshot.presets[0]["filter_count"] == 1


def test_compare_symbols_fetches_each_symbol_once_and_in_parallel(monkeypatch) -> None:
    import threading
    import time

    from backend.ai.market_tools import ComparisonRequest, _Payload, compare_symbols_tool

    calls: list[str] = []
    lock = threading.Lock()

    def slow_bars(request):
        with lock:
            calls.append(request.symbol)
        time.sleep(0.2)
        return _Payload(
            symbol=request.symbol, provider="fixture", source_timestamp="2026-09-23T15:59:00-04:00",
            bars=[{"close": 100.0}, {"close": 110.0 if request.symbol == "AAA" else 105.0}],
        )

    monkeypatch.setattr("backend.ai.market_tools.get_bars_tool", slow_bars)
    started = time.perf_counter()
    result = compare_symbols_tool(ComparisonRequest(symbols=["AAA", "BBB", "CCC", "DDD"]))
    elapsed = time.perf_counter() - started

    assert sorted(calls) == ["AAA", "BBB", "CCC", "DDD"]
    assert elapsed < 0.6  # four 0.2 s reads run concurrently, not in sequence
    assert result.model_dump()["rankings"][0]["symbol"] == "AAA"
