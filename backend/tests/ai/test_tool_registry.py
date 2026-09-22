import pytest

from backend.ai.tool_registry import (
    METRIC_CATALOG,
    ToolRequest,
    default_registry,
    normalize_percentage,
    normalize_session,
    normalize_timeframe,
)


def test_default_registry_exposes_only_named_calculator() -> None:
    assert default_registry.names() == ("calculate",)
    result = default_registry.execute(
        ToolRequest(
            tool_name="calculate",
            arguments={"calculation": "dollar_change", "old_value": 10, "new_value": 13},
        )
    )
    assert result.ok is True
    assert result.data["values"]["dollar_change"] == 3
    assert result.duration_ms >= 0


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
    assert {"relative_volume", "tape_pressure", "market_regime"} <= METRIC_CATALOG.keys()

    with pytest.raises(ValueError):
        normalize_session("overnight")
    with pytest.raises(ValueError):
        normalize_timeframe("10m")
