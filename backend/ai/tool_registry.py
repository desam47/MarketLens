"""Typed, bounded registry for AI Hub tools.

The registry is deliberately small and explicit: a model can select a named
tool, but it cannot import code, call arbitrary functions, or mutate data
through this interface.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.ai.calculator import CalculationRequest, calculate

ToolKind = Literal["read_only", "calculation"]
MarketSession = Literal["premarket", "regular", "after_hours", "all"]


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool_name: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    kind: ToolKind
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel], BaseModel]
    max_duration_ms: int = Field(default=5_000, gt=0, le=30_000)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValueError(f"Unknown tool: {name}") from exc

    def execute(self, request: ToolRequest) -> ToolResult:
        try:
            spec = self.get(request.tool_name)
            parsed = spec.input_model.model_validate(request.arguments)
            started = time.perf_counter()
            result = spec.handler(parsed)
            duration_ms = (time.perf_counter() - started) * 1000
            warnings = []
            if duration_ms > spec.max_duration_ms:
                warnings.append("Tool exceeded its expected duration budget.")
            return ToolResult(
                tool_name=spec.name,
                ok=True,
                data=result.model_dump(mode="json"),
                duration_ms=round(duration_ms, 3),
                warnings=warnings,
            )
        except (ValueError, TypeError) as exc:
            return ToolResult(tool_name=request.tool_name, ok=False, error=str(exc))


def normalize_session(value: str | None) -> MarketSession:
    normalized = (value or "all").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"premarket": "premarket", "pre_market": "premarket", "regular": "regular", "regular_hours": "regular", "afterhours": "after_hours", "after_hours": "after_hours", "all_sessions": "all", "all": "all"}
    try:
        return aliases[normalized]  # type: ignore[return-value]
    except KeyError as exc:
        raise ValueError("session must be premarket, regular, after_hours, or all") from exc


def normalize_timeframe(value: str) -> str:
    normalized = value.strip().lower()
    allowed = {"1m", "2m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk"}
    if normalized not in allowed:
        raise ValueError(f"unsupported timeframe: {value}")
    return normalized


def normalize_percentage(value: float, *, input_is_percent: bool = True) -> float:
    """Return a percentage in human units (e.g. 38.0, not 0.38)."""
    normalized = float(value) * (100 if not input_is_percent else 1)
    if not -100_000 <= normalized <= 100_000:
        raise ValueError("percentage is outside the supported range")
    return normalized


METRIC_CATALOG: Mapping[str, dict[str, str]] = {
    "price_change_percent": {"formula": "(new - old) / abs(old) * 100", "unit": "percent", "owner": "calculator"},
    "position_risk": {"formula": "abs(entry - stop) * shares", "unit": "currency", "owner": "calculator"},
    "risk_reward": {"formula": "abs(target - entry) / abs(entry - stop)", "unit": "ratio", "owner": "calculator"},
    "expected_move": {"formula": "price * implied_volatility * sqrt(days / 365)", "unit": "currency", "owner": "calculator"},
    "volatility": {"formula": "sample_stddev(sequential_returns)", "unit": "percent", "owner": "calculator"},
    "maximum_drawdown": {"formula": "max((running_peak - price) / running_peak * 100)", "unit": "percent", "owner": "calculator"},
}


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="calculate",
            kind="calculation",
            description="Run one deterministic MarketLens calculation.",
            input_model=CalculationRequest,
            handler=calculate,
        )
    )
    return registry


default_registry = build_default_registry()
