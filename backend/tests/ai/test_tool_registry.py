import threading

import pytest
from pydantic import BaseModel

from backend.ai.calculator import CalculationRequest, calculate
from backend.ai.tool_registry import (
    METRIC_CATALOG,
    ProviderObservation,
    ToolRegistry,
    ToolRequest,
    ToolSpec,
    _entitlement_status,
    default_registry,
    normalize_percentage,
    normalize_session,
    normalize_timeframe,
    reconcile_observations,
)


def test_default_registry_exposes_only_named_calculator() -> None:
    assert default_registry.names() == (
        "anomaly_analysis",
        "assess_portfolio_risk",
        "assumption_tracking",
        "build_trade_plan",
        "calculate",
        "compare_symbols",
        "counterargument_review",
        "decision_checklist",
        "export_report",
        "get_alerts",
        "get_application_help",
        "get_bars",
        "get_calendar",
        "get_confluence",
        "get_fundamentals",
        "get_indicator",
        "get_market_context",
        "get_market_regime",
        "get_news",
        "get_options_snapshot",
        "get_quote",
        "get_relative_strength",
        "get_risk_dashboard",
        "get_sector_data",
        "get_session_stats",
        "get_support_resistance",
        "get_tape_state",
        "get_trade_journal",
        "get_trend",
        "get_watchlist",
        "get_watchlist_intelligence",
        "historical_similarity",
        "import_csv",
        "market_event_timeline",
        "options_research",
        "save_to_journal",
        "scenario_analysis",
        "sensitivity_analysis",
        "signal_explanation",
        "trade_journal_coach",
        "what_changed",
        "why_did_it_move",
    )
    result = default_registry.execute(
        ToolRequest(
            tool_name="calculate",
            arguments={"calculation": "dollar_change", "old_value": 10, "new_value": 13},
        )
    )
    assert result.ok is True
    assert result.data["values"]["dollar_change"] == 3
    assert result.duration_ms >= 0
    assert result.provider == "MarketLens calculator"
    # Calculator output is not provider market data; execution time must not
    # be mislabeled as a source timestamp or freshness signal.
    assert result.source_timestamp is None
    assert result.freshness_seconds is None


def test_registry_does_not_promote_execution_time_to_source_timestamp() -> None:
    class EmptyRequest(BaseModel):
        pass

    class NoTimestampPayload(BaseModel):
        provider: str = "fixture"
        value: float = 1

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="no_timestamp",
            kind="read_only",
            description="test",
            input_model=EmptyRequest,
            handler=lambda _: NoTimestampPayload(),
        )
    )

    result = registry.execute(ToolRequest(tool_name="no_timestamp"))

    assert result.ok is True
    assert result.source_timestamp is None
    assert result.freshness_seconds is None


def test_default_registry_executes_build_trade_plan() -> None:
    result = default_registry.execute(
        ToolRequest(
            tool_name="build_trade_plan",
            arguments={
                "symbol": "AAPL",
                "direction": "long",
                "entry_price": 200,
                "stop_price": 190,
                "targets": [220],
                "account_value": 50_000,
                "risk_percent": 2,
            },
        )
    )
    assert result.ok is True
    assert result.data["symbol"] == "AAPL"
    assert result.data["targets"][0]["risk_reward"] == 2
    assert result.data["position_size"]["shares"] == 100
    assert result.provider == "MarketLens calculator"

    missing_stop = default_registry.execute(
        ToolRequest(
            tool_name="build_trade_plan",
            arguments={"symbol": "AAPL", "direction": "long", "entry_price": 200, "targets": [220]},
        )
    )
    assert missing_stop.ok is False
    assert "stop_price" in (missing_stop.error or "")


def test_default_registry_executes_assess_portfolio_risk(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_bars_tool",
        lambda request: _Payload(
            symbol=request.symbol, provider="test", source_timestamp="now",
            bars=[{"close": price, "volume": 1000} for price in (100, 101, 102, 103, 104)],
        ),
    )

    no_positions = default_registry.execute(ToolRequest(tool_name="assess_portfolio_risk", arguments={}))
    assert no_positions.ok is True
    assert no_positions.data["available"] is False

    result = default_registry.execute(
        ToolRequest(
            tool_name="assess_portfolio_risk",
            arguments={
                "positions": [
                    {"symbol": "AAPL", "quantity": 10, "entry_price": 90, "current_price": 110, "sector": "Technology"},
                ],
            },
        )
    )
    assert result.ok is True
    assert result.data["available"] is True
    assert result.data["concentration"]["top_position"]["symbol"] == "AAPL"
    assert result.provider == "MarketLens calculator"


def test_default_registry_executes_options_research(monkeypatch) -> None:
    from backend.ai.market_tools import _Payload

    monkeypatch.setattr(
        "backend.ai.market_tools.get_options_tool",
        lambda request: _Payload(symbol="AAPL", chains=[], provider="yahoo_finance", source_timestamp="now"),
    )

    result = default_registry.execute(ToolRequest(tool_name="options_research", arguments={"symbol": "AAPL"}))

    assert result.ok is True
    assert result.data["available"] is False
    assert result.provider == "yahoo_finance"


def test_default_registry_executes_trade_journal_coach() -> None:
    no_entries = default_registry.execute(ToolRequest(tool_name="trade_journal_coach", arguments={}))
    assert no_entries.ok is True
    assert no_entries.data["available"] is False

    result = default_registry.execute(
        ToolRequest(
            tool_name="trade_journal_coach",
            arguments={
                "entries": [
                    {"symbol": "AAPL", "side": "long", "status": "closed", "entry_price": 100, "exit_price": 110, "quantity": 10},
                ],
            },
        )
    )
    assert result.ok is True
    assert result.data["available"] is True
    assert result.data["win_rate_percent"] == 100.0
    assert result.provider == "MarketLens local journal"


def test_default_registry_executes_decision_checklist() -> None:
    result = default_registry.execute(
        ToolRequest(
            tool_name="decision_checklist",
            arguments={"symbol": "AAPL", "direction": "long", "stop_price": 190, "required_checks": ["defined_stop"]},
        )
    )
    assert result.ok is True
    checks = {c["check"]: c for c in result.data["checks"]}
    assert checks["defined_stop"]["status"] == "completed"
    assert checks["trend_alignment"]["status"] == "skipped"
    assert result.provider == "MarketLens checklist"


def test_registry_rejects_unknown_tools_and_bad_arguments() -> None:
    unknown = default_registry.execute(ToolRequest(tool_name="run_code", arguments={}))
    bad = default_registry.execute(
        ToolRequest(tool_name="calculate", arguments={"calculation": "percentage_change", "new_value": 2})
    )
    assert unknown.ok is False
    assert "Unknown tool" in (unknown.error or "")
    assert bad.ok is False
    assert "old_value" in (bad.error or "")


def test_normalizers_and_catalog_are_canonical() -> None:
    assert normalize_session("After-hours") == "after_hours"
    assert normalize_session("regular hours") == "regular"
    assert normalize_timeframe("1H") == "1h"
    assert normalize_percentage(0.38, input_is_percent=False) == 38
    assert "risk_reward" in METRIC_CATALOG
    assert {"relative_volume", "tape_pressure", "market_regime", "win_rate", "put_call_ratio"} <= METRIC_CATALOG.keys()

    with pytest.raises(ValueError):
        normalize_session("overnight")
    with pytest.raises(ValueError):
        normalize_timeframe("10m")


def test_registry_enforces_per_tool_rate_limit() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="calculate_once",
            kind="calculation",
            permission="calculation",
            description="test",
            input_model=CalculationRequest,
            handler=calculate,
            rate_limit_per_minute=1,
        )
    )
    request = ToolRequest(
        tool_name="calculate_once",
        arguments={"calculation": "dollar_change", "old_value": 1, "new_value": 2},
    )
    assert registry.execute(request).ok is True
    limited = registry.execute(request)
    assert limited.ok is False
    assert "rate limit" in (limited.error or "").lower()


class _EmptyRequest(BaseModel):
    pass


def _slow_registry(*, permission: str = "read_only", timeout_ms: int | None = 50) -> tuple[ToolRegistry, threading.Event]:
    release = threading.Event()

    def slow(_):
        release.wait(2)
        return _EmptyRequest()

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="slow_tool",
            kind="read_only",
            permission=permission,
            description="test",
            input_model=_EmptyRequest,
            handler=slow,
            timeout_ms=timeout_ms,
        )
    )
    return registry, release


def test_registry_times_out_a_hung_read_only_tool() -> None:
    registry, release = _slow_registry()
    try:
        result = registry.execute(ToolRequest(tool_name="slow_tool"))
    finally:
        release.set()
    assert result.ok is False
    assert result.failure_kind == "timeout"
    assert "timed out" in (result.error or "")
    assert result.duration_ms < 1000


def test_registry_uses_configured_default_tool_timeout(monkeypatch) -> None:
    from backend.config.settings import settings

    monkeypatch.setattr(settings.ai, "chat_tool_timeout_seconds", 0.05)
    registry, release = _slow_registry(timeout_ms=None)
    try:
        result = registry.execute(ToolRequest(tool_name="slow_tool"))
    finally:
        release.set()
    assert result.failure_kind == "timeout"


def test_registry_never_times_out_a_mutating_tool() -> None:
    registry, release = _slow_registry(permission="mutating")
    # Finishes well after the 50 ms deadline; a mutating call must wait for it.
    timer = threading.Timer(0.2, release.set)
    timer.start()
    result = registry.execute(ToolRequest(tool_name="slow_tool", confirmed=True))
    timer.join()
    assert result.ok is True
    assert result.failure_kind is None


def test_invalid_arguments_report_invalid_failure_kind() -> None:
    result = default_registry.execute(ToolRequest(tool_name="calculate", arguments={"calculation": "nope"}))
    assert result.ok is False
    assert result.failure_kind == "invalid"


def test_registry_requires_confirmation_for_mutating_tools() -> None:
    class EmptyRequest(BaseModel):
        pass

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="delete_thing",
            kind="read_only",
            permission="mutating",
            description="test",
            input_model=EmptyRequest,
            handler=lambda _: EmptyRequest(),
        )
    )

    unconfirmed = registry.execute(ToolRequest(tool_name="delete_thing"))
    assert unconfirmed.ok is False
    assert "confirmation" in (unconfirmed.error or "").lower()

    confirmed = registry.execute(ToolRequest(tool_name="delete_thing", confirmed=True))
    assert confirmed.ok is True


def test_registry_surfaces_provider_freshness_and_quality_warning() -> None:
    class EmptyRequest(BaseModel):
        pass

    class ProviderPayload(BaseModel):
        provider: str = "webull"
        source_timestamp: str = "2020-01-01T00:00:00+00:00"
        data_status: str = "STALE"

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="provider_test",
            kind="read_only",
            description="test",
            input_model=EmptyRequest,
            handler=lambda _: ProviderPayload(),
        )
    )
    result = registry.execute(ToolRequest(tool_name="provider_test"))

    assert result.provider == "webull"
    assert result.freshness_seconds and result.freshness_seconds > 0
    assert any("STALE" in warning for warning in result.warnings)


def test_stale_warning_notes_market_closed_when_session_is_shut(monkeypatch) -> None:
    import backend.ai.tool_registry as tool_registry_module

    class EmptyRequest(BaseModel):
        pass

    class ProviderPayload(BaseModel):
        provider: str = "webull"
        source_timestamp: str = "2020-01-01T00:00:00+00:00"
        data_status: str = "STALE"

    monkeypatch.setattr(tool_registry_module, "_is_market_session_closed", lambda: True)

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="provider_test",
            kind="read_only",
            description="test",
            input_model=EmptyRequest,
            handler=lambda _: ProviderPayload(),
        )
    )
    result = registry.execute(ToolRequest(tool_name="provider_test"))

    assert any("STALE" in warning and "market is currently closed" in warning.lower() for warning in result.warnings)


def test_delayed_warning_does_not_note_market_closed() -> None:
    """DELAYED is a fixed feed-type label (Webull's REST snapshot), not an age
    judgment — it must not get the "expected, market is closed" caveat that
    STALE/GAP/INCOMPLETE get, since DELAYED is true whether or not the market
    is open."""

    class EmptyRequest(BaseModel):
        pass

    class ProviderPayload(BaseModel):
        provider: str = "webull"
        source_timestamp: str = "2020-01-01T00:00:00+00:00"
        data_status: str = "DELAYED"

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="provider_test_delayed",
            kind="read_only",
            description="test",
            input_model=EmptyRequest,
            handler=lambda _: ProviderPayload(),
        )
    )
    result = registry.execute(ToolRequest(tool_name="provider_test_delayed"))

    assert any("DELAYED" in warning for warning in result.warnings)
    assert not any("market is currently closed" in warning.lower() for warning in result.warnings)


def test_reconciliation_prefers_primary_and_flags_material_conflicts() -> None:
    result = reconcile_observations(
        [
            ProviderObservation(provider="webull", value=100, source_timestamp="2026-09-22T12:00:00Z"),
            ProviderObservation(provider="yfinance", value=103, source_timestamp="2026-09-22T12:01:00Z"),
        ],
        primary_provider="webull",
    )

    assert result.value == 100
    assert result.provider == "webull"
    assert result.conflict is True
    assert "yfinance" in result.warnings[0]


def test_reconciliation_allows_small_differences_and_requires_observations() -> None:
    result = reconcile_observations(
        [
            ProviderObservation(provider="webull", value=100, source_timestamp="2026-09-22T12:00:00Z"),
            ProviderObservation(provider="yfinance", value=100.005, source_timestamp="2026-09-22T12:01:00Z"),
        ],
        primary_provider="webull",
    )
    assert result.conflict is False
    with pytest.raises(ValueError):
        reconcile_observations([])


def test_entitlement_status_not_applicable_for_internal_providers() -> None:
    assert _entitlement_status(None) == "not_applicable"
    assert _entitlement_status("MarketLens") == "not_applicable"
    assert _entitlement_status("MarketLens engine") == "not_applicable"
    assert _entitlement_status("MarketLens calculator") == "not_applicable"
    assert _entitlement_status("MarketLens database") == "not_applicable"


def test_entitlement_status_verified_for_configured_primary_provider(monkeypatch) -> None:
    from backend.config.settings import settings

    monkeypatch.setattr(settings.market_data, "primary_provider", "webull")

    assert _entitlement_status("webull") == "verified"


def test_entitlement_status_configured_for_non_primary_known_provider(monkeypatch) -> None:
    from backend.config.settings import settings

    monkeypatch.setattr(settings.market_data, "primary_provider", "webull")
    monkeypatch.setattr(settings.webull, "declared_entitlements", "")

    assert _entitlement_status("yfinance") == "configured"


def test_entitlement_status_declared_for_webull_with_user_declared_coverage(monkeypatch) -> None:
    from backend.config.settings import settings

    monkeypatch.setattr(settings.market_data, "primary_provider", "yfinance")
    monkeypatch.setattr(settings.webull, "declared_entitlements", "time_and_sales")

    assert _entitlement_status("webull") == "declared"


def test_registry_execute_propagates_entitlement(monkeypatch) -> None:
    from backend.config.settings import settings

    monkeypatch.setattr(settings.market_data, "primary_provider", "webull")

    class EmptyRequest(BaseModel):
        pass

    class ProviderPayload(BaseModel):
        provider: str = "webull"

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="provider_test",
            kind="read_only",
            description="test",
            input_model=EmptyRequest,
            handler=lambda _: ProviderPayload(),
        )
    )
    result = registry.execute(ToolRequest(tool_name="provider_test"))

    assert result.entitlement == "verified"
