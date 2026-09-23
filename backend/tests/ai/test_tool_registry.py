import pytest
from pydantic import BaseModel

from backend.ai.calculator import CalculationRequest, calculate
from backend.ai.tool_registry import (
    METRIC_CATALOG,
    ProviderObservation,
    ToolRegistry,
    ToolRequest,
    ToolSpec,
    default_registry,
    normalize_percentage,
    normalize_session,
    normalize_timeframe,
    reconcile_observations,
)


def test_default_registry_exposes_only_named_calculator() -> None:
    assert default_registry.names() == (
        "calculate",
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
        "import_csv",
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
    assert result.source_timestamp


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
