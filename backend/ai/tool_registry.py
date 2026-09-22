"""Typed, bounded registry for AI Hub tools.

The registry is deliberately small and explicit: a model can select a named
tool, but it cannot import code, call arbitrary functions, or mutate data
through this interface.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.ai.calculator import CalculationRequest, calculate

ToolKind = Literal["read_only", "calculation"]
MarketSession = Literal["premarket", "regular", "after_hours", "all"]


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    session: MarketSession = "all"
    timeframe: str | None = None

    @field_validator("session", mode="before")
    @classmethod
    def _normalize_session(cls, value: str) -> MarketSession:
        return normalize_session(value)

    @field_validator("timeframe")
    @classmethod
    def _normalize_timeframe(cls, value: str | None) -> str | None:
        return normalize_timeframe(value) if value else None


class ToolResult(BaseModel):
    tool_name: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    session: MarketSession = "all"
    timeframe: str | None = None
    provider: str = "MarketLens"
    source_timestamp: str | None = None
    freshness_seconds: float | None = Field(default=0, ge=0)
    fallback: bool = False


class ProviderObservation(BaseModel):
    """A value already obtained from one provider, used for reconciliation."""

    provider: str
    value: float
    source_timestamp: str


class ReconciledValue(BaseModel):
    value: float
    provider: str
    source_timestamp: str
    conflict: bool = False
    warnings: list[str] = Field(default_factory=list)


def reconcile_observations(
    observations: list[ProviderObservation],
    *,
    primary_provider: str | None = None,
    relative_tolerance: float = 0.01,
) -> ReconciledValue:
    """Select a value from existing observations and flag disagreements.

    No provider calls happen here. Primary wins when it is present; if it is
    absent, the newest timestamp wins. A conflict is meaningful only when
    values differ by more than the configured relative tolerance.
    """
    if not observations:
        raise ValueError("at least one provider observation is required")
    if relative_tolerance < 0:
        raise ValueError("relative_tolerance must be non-negative")
    selected = next(
        (item for item in observations if primary_provider and item.provider == primary_provider),
        max(observations, key=lambda item: item.source_timestamp),
    )
    conflict = any(
        abs(item.value - selected.value) > max(abs(selected.value), 1.0) * relative_tolerance
        for item in observations
        if item.provider != selected.provider
    )
    warnings = []
    if conflict:
        providers = ", ".join(item.provider for item in observations if item.provider != selected.provider)
        warnings.append(f"Provider conflict with {providers}; selected {selected.provider}.")
    return ReconciledValue(
        value=selected.value,
        provider=selected.provider,
        source_timestamp=selected.source_timestamp,
        conflict=conflict,
        warnings=warnings,
    )


class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    kind: ToolKind
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel], BaseModel]
    max_duration_ms: int = Field(default=5_000, gt=0, le=30_000)
    permission: ToolKind = "read_only"
    rate_limit_per_minute: int = Field(default=60, gt=0, le=10_000)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._calls: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

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
        started_at = datetime.now(UTC).isoformat()
        try:
            spec = self.get(request.tool_name)
            now = time.monotonic()
            with self._lock:
                calls = self._calls.setdefault(spec.name, deque())
                while calls and now - calls[0] >= 60:
                    calls.popleft()
                if len(calls) >= spec.rate_limit_per_minute:
                    raise ValueError(f"Tool rate limit exceeded: {spec.name}")
                calls.append(now)
            parsed = spec.input_model.model_validate(request.arguments)
            started = time.perf_counter()
            result = spec.handler(parsed)
            duration_ms = (time.perf_counter() - started) * 1000
            finished_at = datetime.now(UTC).isoformat()
            payload = result.model_dump(mode="json")
            provider = str(payload.get("provider") or ("MarketLens calculator" if spec.name == "calculate" else "MarketLens"))
            source_timestamp = payload.get("source_timestamp") or payload.get("timestamp")
            freshness_seconds = _freshness_seconds(source_timestamp)
            warnings = _data_quality_warnings(payload, freshness_seconds)
            if duration_ms > spec.max_duration_ms:
                warnings.append("Tool exceeded its expected duration budget.")
            return ToolResult(
                tool_name=spec.name,
                ok=True,
                data=payload,
                duration_ms=round(duration_ms, 3),
                warnings=warnings,
                started_at=started_at,
                finished_at=finished_at,
                session=request.session,
                timeframe=request.timeframe,
                provider=provider,
                source_timestamp=source_timestamp or started_at,
                freshness_seconds=freshness_seconds,
                fallback=bool(payload.get("fallback", False)),
            )
        except (ValueError, TypeError) as exc:
            return ToolResult(
                tool_name=request.tool_name,
                ok=False,
                error=str(exc),
                started_at=started_at,
                finished_at=datetime.now(UTC).isoformat(),
                session=request.session,
                timeframe=request.timeframe,
                provider="MarketLens",
                source_timestamp=started_at,
            )


def _freshness_seconds(source_timestamp: Any) -> float | None:
    if not source_timestamp:
        return None
    try:
        source = datetime.fromisoformat(str(source_timestamp).replace("Z", "+00:00"))
        if source.tzinfo is None:
            source = source.replace(tzinfo=UTC)
        return round(max(0.0, (datetime.now(UTC) - source).total_seconds()), 3)
    except (TypeError, ValueError):
        return None


def _data_quality_warnings(payload: Mapping[str, Any], freshness_seconds: float | None) -> list[str]:
    warnings: list[str] = []
    status = str(payload.get("data_status") or "").upper()
    if status in {"STALE", "DELAYED", "ERROR", "GAP", "INCOMPLETE"}:
        warnings.append(f"Provider data status: {status}.")
    if freshness_seconds is None and payload.get("provider"):
        warnings.append("Provider returned no parseable source timestamp.")
    if payload.get("fallback"):
        warnings.append("Fallback provider data was used.")
    return warnings
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
    "dollar_change": {"formula": "new - old", "unit": "currency", "owner": "calculator"},
    "return": {"formula": "(end - start) / abs(start) * 100", "unit": "percent", "owner": "calculator"},
    "cagr": {"formula": "((end / start) ** (1 / years) - 1) * 100", "unit": "percent", "owner": "calculator"},
    "weighted_average": {"formula": "sum(value[i] * weight[i]) / sum(weight)", "unit": "currency", "owner": "calculator"},
    "position_risk": {"formula": "abs(entry - stop) * shares", "unit": "currency", "owner": "calculator"},
    "position_size": {"formula": "account_value * risk_percent / 100 / abs(entry - stop)", "unit": "shares", "owner": "calculator"},
    "risk_reward": {"formula": "abs(target - entry) / abs(entry - stop)", "unit": "ratio", "owner": "calculator"},
    "allocation": {"formula": "position_value / portfolio_value * 100", "unit": "percent", "owner": "calculator"},
    "relative_volume": {"formula": "current_volume / average_volume", "unit": "ratio", "owner": "scanner"},
    "tape_pressure": {"formula": "(buy_volume - sell_volume) / total_volume", "unit": "ratio", "owner": "microstructure"},
    "bid_ask_imbalance": {"formula": "(bid_size - ask_size) / (bid_size + ask_size)", "unit": "ratio", "owner": "microstructure"},
    "confidence": {"formula": "owning_signal_engine", "unit": "score", "owner": "signal_engine"},
    "trend_strength": {"formula": "owning_trend_engine", "unit": "score", "owner": "trend_engine"},
    "confluence": {"formula": "owning_confluence_engine", "unit": "score", "owner": "confluence_engine"},
    "market_regime": {"formula": "owning_regime_engine", "unit": "label", "owner": "regime_engine"},
    "signal_confidence": {"formula": "owning_signal_engine", "unit": "score", "owner": "signal_engine"},
    "signal_success_rate": {"formula": "successful_similar_signals / similar_signals", "unit": "percent", "owner": "signal_engine"},
    "put_call_ratio": {"formula": "put_volume / call_volume", "unit": "ratio", "owner": "options"},
    "implied_volatility": {"formula": "provider_reported_implied_volatility", "unit": "percent", "owner": "options"},
    "win_rate": {"formula": "winning_trades / total_trades * 100", "unit": "percent", "owner": "backtest"},
    "expectancy": {"formula": "average_win * win_rate - average_loss * loss_rate", "unit": "currency", "owner": "backtest"},
    "sample_size": {"formula": "count_of_observations", "unit": "count", "owner": "backtest"},
    "expected_move": {"formula": "price * implied_volatility * sqrt(days / 365)", "unit": "currency", "owner": "calculator"},
    "volatility": {"formula": "sample_stddev(sequential_returns)", "unit": "percent", "owner": "calculator"},
    "maximum_drawdown": {"formula": "max((running_peak - price) / running_peak * 100)", "unit": "percent", "owner": "calculator"},
    "options_breakeven": {"formula": "strike +/- premium by option type", "unit": "currency", "owner": "calculator"},
    "options_intrinsic_value": {"formula": "max(in_the_money_amount, 0)", "unit": "currency", "owner": "calculator"},
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
    from backend.ai.market_tools import (
        BarsRequest,
        FundamentalsRequest,
        IndicatorRequest,
        NewsRequest,
        OptionsRequest,
        SymbolRequest,
        get_bars_tool,
        get_fundamentals_tool,
        get_indicator_tool,
        get_market_context_tool,
        get_market_regime_tool,
        get_news_tool,
        get_options_tool,
        get_quote_tool,
        get_support_resistance_tool,
    )

    registry.register(ToolSpec(name="get_quote", kind="read_only", description="Get a verified quote.", input_model=SymbolRequest, handler=get_quote_tool))
    registry.register(ToolSpec(name="get_bars", kind="read_only", description="Get verified historical bars.", input_model=BarsRequest, handler=get_bars_tool))
    registry.register(ToolSpec(name="get_indicator", kind="read_only", description="Compute a supported indicator from verified bars.", input_model=IndicatorRequest, handler=get_indicator_tool))
    registry.register(ToolSpec(name="get_support_resistance", kind="read_only", description="Get range support and resistance from verified bars.", input_model=BarsRequest, handler=get_support_resistance_tool))
    registry.register(ToolSpec(name="get_market_regime", kind="read_only", description="Get the current warmed market regime.", input_model=SymbolRequest, handler=get_market_regime_tool))
    registry.register(ToolSpec(name="get_market_context", kind="read_only", description="Get the current warmed market context.", input_model=BaseModel, handler=get_market_context_tool))
    registry.register(ToolSpec(name="get_news", kind="read_only", description="Get recent provider news.", input_model=NewsRequest, handler=get_news_tool))
    registry.register(ToolSpec(name="get_fundamentals", kind="read_only", description="Get a fundamentals snapshot.", input_model=FundamentalsRequest, handler=get_fundamentals_tool))
    registry.register(ToolSpec(name="get_options_snapshot", kind="read_only", description="Get an options chain snapshot.", input_model=OptionsRequest, handler=get_options_tool))
    return registry


default_registry = build_default_registry()
