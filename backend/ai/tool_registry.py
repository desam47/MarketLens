"""Typed, bounded registry for AI Hub tools.

The registry is deliberately small and explicit: a model can select a named
tool, but it cannot import code, call arbitrary functions, or mutate data
through this interface.
"""

from __future__ import annotations

import contextvars
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.ai.calculator import CalculationRequest, calculate
from backend.config.settings import settings

ToolKind = Literal["read_only", "calculation"]
ToolPermission = Literal["read_only", "calculation", "mutating"]
MarketSession = Literal["premarket", "regular", "after_hours", "all"]


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    session: MarketSession = "all"
    timeframe: str | None = None
    confirmed: bool = False

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
    entitlement: str = "not_applicable"
    # "invalid" (rejected request), "timeout" (deadline exceeded); None on success.
    failure_kind: str | None = None


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
    # Hard deadline; None uses AI_CHAT_TOOL_TIMEOUT_SECONDS. Not applied to
    # mutating tools, whose outcome must never be left unknown.
    timeout_ms: int | None = Field(default=None, gt=0, le=120_000)
    permission: ToolPermission = "read_only"
    rate_limit_per_minute: int = Field(default=60, gt=0, le=10_000)


# Shared worker pool for deadline-bounded handler calls. A timed-out handler
# keeps its worker until it returns, so the pool is sized for a few stragglers.
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="chat-tool")


def _tool_timeout_seconds(spec: ToolSpec) -> float | None:
    if spec.permission == "mutating":
        return None
    if spec.timeout_ms is not None:
        return spec.timeout_ms / 1000
    return float(getattr(settings.ai, "chat_tool_timeout_seconds", 20.0))


class ToolTimeoutError(Exception):
    """A tool handler exceeded its hard deadline."""


def _run_handler(spec: ToolSpec, parsed: BaseModel) -> BaseModel:
    timeout = _tool_timeout_seconds(spec)
    if timeout is None:
        return spec.handler(parsed)
    context = contextvars.copy_context()
    future = _TOOL_EXECUTOR.submit(context.run, spec.handler, parsed)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError as exc:
        raise ToolTimeoutError(f"Tool timed out after {timeout:g}s: {spec.name}") from exc


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
        started: float | None = None
        try:
            spec = self.get(request.tool_name)
            if spec.permission == "mutating" and not request.confirmed:
                raise ValueError(f"Tool requires confirmation: {spec.name}")
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
            result = _run_handler(spec, parsed)
            duration_ms = (time.perf_counter() - started) * 1000
            finished_at = datetime.now(UTC).isoformat()
            payload = result.model_dump(mode="json")
            provider = str(payload.get("provider") or ("MarketLens calculator" if spec.name == "calculate" else "MarketLens"))
            source_timestamp = (
                payload.get("source_timestamp")
                or payload.get("timestamp")
                or _oldest_nested_timestamp(payload)
            )
            daily = str(payload.get("timeframe") or request.timeframe or "").lower() == "1d"
            freshness_seconds = _freshness_seconds(source_timestamp, daily=daily)
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
                # An execution timestamp is not a provider timestamp. Keep
                # provenance unknown when the handler did not supply one so
                # live-answer verification cannot mistake call time for data
                # freshness.
                source_timestamp=source_timestamp,
                freshness_seconds=freshness_seconds,
                fallback=bool(payload.get("fallback", False)),
                entitlement=_entitlement_status(provider),
            )
        except (ValueError, TypeError, ToolTimeoutError) as exc:
            return ToolResult(
                tool_name=request.tool_name,
                ok=False,
                error=str(exc),
                failure_kind="timeout" if isinstance(exc, ToolTimeoutError) else "invalid",
                duration_ms=round((time.perf_counter() - started) * 1000, 3) if started is not None else 0,
                started_at=started_at,
                finished_at=datetime.now(UTC).isoformat(),
                session=request.session,
                timeframe=request.timeframe,
                provider="MarketLens",
                source_timestamp=None,
            )


_NESTED_SOURCE_KEYS = ("sources", "signals")


def _oldest_nested_timestamp(payload: Mapping[str, Any]) -> str | None:
    """The oldest per-source timestamp of a composite result.

    Composite tools (why_did_it_move, compare_symbols, the event timeline,
    relative strength, ...) combine several timestamped reads. Their answer
    is only as fresh as the oldest of them, so that is its source time.
    """
    oldest: tuple[float, str] | None = None
    for key in _NESTED_SOURCE_KEYS:
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items[:200]:
            if not isinstance(item, Mapping):
                continue
            value = item.get("source_timestamp") or item.get("timestamp")
            age = _freshness_seconds(value)
            if age is not None and (oldest is None or age > oldest[0]):
                oldest = (age, str(value))
    return oldest[1] if oldest else None


def _freshness_seconds(source_timestamp: Any, *, daily: bool = False) -> float | None:
    if not source_timestamp:
        return None
    if daily:
        # A daily bar stamped at its session date's midnight holds that
        # day's close, so its data is as of 16:00 ET, not midnight.
        from backend.engines.market_calendar import daily_bar_reference_time

        reference = daily_bar_reference_time(source_timestamp)
        if reference is None:
            return None
        return round(max(0.0, (datetime.now(UTC) - reference).total_seconds()), 3)
    try:
        source = datetime.fromisoformat(str(source_timestamp).replace("Z", "+00:00"))
        if source.tzinfo is None:
            # Project convention: a naive timestamp is America/New_York local
            # time. Reading it as UTC would age every naive bar by 4-5 hours.
            from backend.utils.timezone import NY

            source = source.replace(tzinfo=NY)
        return round(max(0.0, (datetime.now(UTC) - source).total_seconds()), 3)
    except (TypeError, ValueError):
        return None


def _data_quality_warnings(payload: Mapping[str, Any], freshness_seconds: float | None) -> list[str]:
    warnings: list[str] = []
    status = str(payload.get("data_status") or "").upper()
    if status in {"STALE", "DELAYED", "ERROR", "GAP", "INCOMPLETE"}:
        note = f"Provider data status: {status}."
        # STALE/GAP/INCOMPLETE can just mean "the market is closed, nothing new
        # has traded" rather than a real ingestion problem. DELAYED is a fixed
        # feed-type label (e.g. Webull's REST snapshot), not an age judgment,
        # so it's excluded here rather than tagged as expected.
        if status in {"STALE", "GAP", "INCOMPLETE"} and _is_market_session_closed():
            note += " Market is currently closed, so this is expected."
        warnings.append(note)
    if freshness_seconds is None and payload.get("provider"):
        warnings.append("Provider returned no parseable source timestamp.")
    if payload.get("fallback"):
        warnings.append("Fallback provider data was used.")
    return warnings


def _is_market_session_closed() -> bool:
    from backend.engines.market_calendar import SessionType, us_market_calendar

    return us_market_calendar.get_session_type(datetime.now(UTC)) == SessionType.CLOSED


# Providers whose access is actually gated by an account/subscription tier
# (as opposed to "MarketLens ..." labels for internally-computed engines,
# the local calculator, or database reads, where entitlement genuinely does
# not apply — there is no subscription to lack). This intentionally
# mirrors, in simplified form, the verified/declared/configured/unavailable
# vocabulary backend/api/system/router.py's entitlement_status() already
# uses for System Health — but that function also cross-references
# per-capability runtime observation history (provider_history_stats()) to
# earn "verified"; this tool-layer version only has the single completed
# call's provider name to go on, so it collapses to a coarser signal:
# "verified" the provider matched the configured primary, "declared" the
# user declared coverage for it (Webull entitlements only, the one setting
# that exists today), otherwise "configured" it is a recognized provider
# without confirmed/declared access.
_LIVE_DATA_PROVIDER_MARKERS = ("webull", "yfinance", "alpaca", "finnhub")


def _entitlement_status(provider: str | None) -> str:
    if not provider:
        return "not_applicable"
    normalized = provider.lower()
    if not any(marker in normalized for marker in _LIVE_DATA_PROVIDER_MARKERS):
        return "not_applicable"
    from backend.config.settings import settings

    if normalized == settings.market_data.primary_provider.lower():
        return "verified"
    if "webull" in normalized:
        declared = {
            item.strip().lower()
            for item in settings.webull.declared_entitlements.split(",")
            if item.strip()
        }
        if declared:
            return "declared"
    return "configured"


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


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_RELATIVE_DATE_RE = re.compile(
    r"\b(?P<today>today)\b"
    r"|\b(?P<yesterday>yesterday)\b"
    r"|\b(?P<days>\d{1,3})\s+(?:trading\s+)?days?\s+ago\b"
    r"|\b(?P<lastweek>last\s+week)\b"
    r"|\b(?:last|on|since|this\s+past)\s+(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.I,
)


def resolve_relative_date(text: str, now: datetime | None = None) -> dict[str, str] | None:
    """Resolve the first relative date phrase in ``text`` (plan 5.1.3).

    Dates are calendar days in America/New_York. Returns ``{"phrase",
    "start", "end"}`` with naive New York ISO timestamps covering the whole
    day (or Monday–Sunday for "last week"), or ``None`` when no supported
    phrase is present. "N days ago" counts calendar days, not sessions.
    """
    from datetime import timedelta

    from backend.utils.timezone import now_ny

    match = _RELATIVE_DATE_RE.search(text or "")
    if not match:
        return None
    today = (now or now_ny()).date()
    if match.group("today"):
        start = end = today
    elif match.group("yesterday"):
        start = end = today - timedelta(days=1)
    elif match.group("days"):
        start = end = today - timedelta(days=int(match.group("days")))
    elif match.group("lastweek"):
        this_monday = today - timedelta(days=today.weekday())
        start, end = this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    else:
        target = _WEEKDAYS.index(match.group("weekday").lower())
        # The most recent such weekday strictly before today.
        delta = (today.weekday() - target) % 7 or 7
        start = end = today - timedelta(days=delta)
    return {
        "phrase": match.group(0).lower(),
        "start": f"{start.isoformat()}T00:00:00",
        "end": f"{end.isoformat()}T23:59:59",
    }


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
    "return_correlation": {"formula": "pearson(returns, comparison_returns)", "unit": "ratio", "owner": "calculator"},
    "maximum_drawdown": {"formula": "max((running_peak - price) / running_peak * 100)", "unit": "percent", "owner": "calculator"},
    "options_breakeven": {"formula": "strike +/- premium by option type", "unit": "currency", "owner": "calculator"},
    "options_intrinsic_value": {"formula": "max(in_the_money_amount, 0)", "unit": "currency", "owner": "calculator"},
    "options_assignment_exposure": {"formula": "strike * contracts * contract_multiplier", "unit": "currency", "owner": "calculator"},
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
        AlertsRequest,
        AnomalyAnalysisRequest,
        ApplicationHelpRequest,
        AssumptionTrackingRequest,
        BarsRequest,
        CalendarRequest,
        ChangeAnalysisRequest,
        ComparisonRequest,
        ConfluenceRequest,
        CounterargumentRequest,
        CsvImportRequest,
        DecisionChecklistRequest,
        ExportReportRequest,
        FundamentalsRequest,
        HistoricalSimilarityRequest,
        IndicatorRequest,
        JournalCoachRequest,
        MarketContextRequest,
        MarketEventTimelineRequest,
        MoveAnalysisRequest,
        NewsRequest,
        OptionsRequest,
        OptionsResearchRequest,
        PortfolioRiskRequest,
        PriceStatisticsRequest,
        RiskDashboardRequest,
        SavedScansRequest,
        SaveToJournalRequest,
        ScenarioRequest,
        SensitivityRequest,
        SessionStatsRequest,
        SignalExplanationRequest,
        SignalHistoryRequest,
        SymbolRequest,
        TapeRequest,
        TradeJournalRequest,
        TradePlanRequest,
        TrendRequest,
        WatchlistIntelligenceRequest,
        WatchlistRequest,
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
        get_calendar_tool,
        get_confluence_tool,
        get_fundamentals_tool,
        get_indicator_tool,
        get_market_context_tool,
        get_market_regime_tool,
        get_news_tool,
        get_options_tool,
        get_price_statistics_tool,
        get_quote_tool,
        get_relative_strength_tool,
        get_risk_dashboard_tool,
        get_saved_scans_tool,
        get_sector_data_tool,
        get_session_stats_tool,
        get_signal_history_tool,
        get_support_resistance_tool,
        get_tape_state_tool,
        get_trade_journal_tool,
        get_trend_tool,
        get_watchlist_intelligence_tool,
        get_watchlist_tool,
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

    registry.register(ToolSpec(name="get_quote", kind="read_only", description="Get a verified quote.", input_model=SymbolRequest, handler=get_quote_tool))
    registry.register(ToolSpec(name="get_bars", kind="read_only", description="Get verified historical bars.", input_model=BarsRequest, handler=get_bars_tool))
    registry.register(ToolSpec(name="get_indicator", kind="read_only", description="Compute a supported indicator from verified bars.", input_model=IndicatorRequest, handler=get_indicator_tool))
    registry.register(ToolSpec(name="get_support_resistance", kind="read_only", description="Get range support and resistance from verified bars.", input_model=BarsRequest, handler=get_support_resistance_tool))
    registry.register(ToolSpec(name="get_market_regime", kind="read_only", description="Get the current warmed market regime.", input_model=SymbolRequest, handler=get_market_regime_tool))
    registry.register(ToolSpec(name="get_market_context", kind="read_only", description="Get the current warmed market context.", input_model=MarketContextRequest, handler=get_market_context_tool))
    registry.register(ToolSpec(name="get_sector_data", kind="read_only", description="Get a symbol's sector alignment vs. its sector ETF and SPY.", input_model=SymbolRequest, handler=get_sector_data_tool))
    registry.register(ToolSpec(name="get_session_stats", kind="read_only", description="Get O/H/L/C, volume, VWAP, and range for the most recent trading day, scoped to one session.", input_model=SessionStatsRequest, handler=get_session_stats_tool))
    registry.register(ToolSpec(name="get_trend", kind="read_only", description="Get the current trend for a symbol on one timeframe.", input_model=TrendRequest, handler=get_trend_tool))
    registry.register(ToolSpec(name="get_confluence", kind="read_only", description="Get multi-timeframe confluence for a symbol under a trading-style preset.", input_model=ConfluenceRequest, handler=get_confluence_tool))
    registry.register(ToolSpec(name="get_relative_strength", kind="read_only", description="Get relative-strength signals for a symbol vs. SPY/QQQ.", input_model=SymbolRequest, handler=get_relative_strength_tool))
    registry.register(ToolSpec(name="get_tape_state", kind="read_only", description="Get the current tape snapshot: BBO, tape pressure, and large prints.", input_model=TapeRequest, handler=get_tape_state_tool))
    registry.register(ToolSpec(name="get_calendar", kind="read_only", description="Get upcoming earnings/dividend catalyst events for a symbol.", input_model=CalendarRequest, handler=get_calendar_tool))
    registry.register(ToolSpec(name="get_news", kind="read_only", description="Get recent provider news.", input_model=NewsRequest, handler=get_news_tool))
    registry.register(ToolSpec(name="get_fundamentals", kind="read_only", description="Get a fundamentals snapshot.", input_model=FundamentalsRequest, handler=get_fundamentals_tool))
    registry.register(ToolSpec(name="get_options_snapshot", kind="read_only", description="Get an options chain snapshot.", input_model=OptionsRequest, handler=get_options_tool))
    registry.register(ToolSpec(name="options_research", kind="read_only", description="Explain and compare calls, puts, and defined-risk vertical spreads: IV, IV rank, expected move, volume, open interest, put/call ratio, unusual activity, breakeven, max gain/loss, and assignment exposure.", input_model=OptionsResearchRequest, handler=options_research_tool))
    registry.register(ToolSpec(name="get_watchlist", kind="read_only", description="Read an application watchlist and its symbols.", input_model=WatchlistRequest, handler=get_watchlist_tool))
    registry.register(ToolSpec(name="get_watchlist_intelligence", kind="read_only", description="Rank enabled names by weakness, strength, deterioration, or relative underperformance using cached scanner results and warming missing names on demand.", input_model=WatchlistIntelligenceRequest, handler=get_watchlist_intelligence_tool, max_duration_ms=30_000))
    registry.register(ToolSpec(name="why_did_it_move", kind="read_only", description="Assemble evidence for a symbol's move without claiming causation.", input_model=MoveAnalysisRequest, handler=why_did_it_move_tool))
    registry.register(ToolSpec(name="what_changed", kind="read_only", description="Compare current verified data with a selected baseline.", input_model=ChangeAnalysisRequest, handler=what_changed_tool))
    registry.register(ToolSpec(name="get_price_statistics", kind="read_only", description="Return, volatility, max drawdown, or return correlation from verified daily closes over an explicit window.", input_model=PriceStatisticsRequest, handler=get_price_statistics_tool))
    registry.register(ToolSpec(name="compare_symbols", kind="read_only", description="Rank symbols or a watchlist using verified bar metrics.", input_model=ComparisonRequest, handler=compare_symbols_tool))
    registry.register(ToolSpec(name="scenario_analysis", kind="read_only", description="Recalculate explicit positions under deterministic what-if shocks.", input_model=ScenarioRequest, handler=scenario_analysis_tool))
    registry.register(ToolSpec(name="historical_similarity", kind="read_only", description="Find prior bar windows with similar verified features and forward outcomes.", input_model=HistoricalSimilarityRequest, handler=historical_similarity_tool))
    registry.register(ToolSpec(name="signal_explanation", kind="read_only", description="Explain indicator, timeframe, tape, freshness, and signal-state evidence.", input_model=SignalExplanationRequest, handler=signal_explanation_tool))
    registry.register(ToolSpec(name="counterargument_review", kind="read_only", description="Review opposing evidence and evidence-backed invalidation thresholds.", input_model=CounterargumentRequest, handler=counterargument_review_tool))
    registry.register(ToolSpec(name="sensitivity_analysis", kind="read_only", description="Run bounded one-factor sensitivity calculations.", input_model=SensitivityRequest, handler=sensitivity_analysis_tool))
    registry.register(ToolSpec(name="market_event_timeline", kind="read_only", description="Combine normalized price, session, signal, alert, and auxiliary events.", input_model=MarketEventTimelineRequest, handler=market_event_timeline_tool))
    registry.register(ToolSpec(name="anomaly_analysis", kind="read_only", description="Detect unusual price, volume, spread, tape, options, correlation, and concentration observations.", input_model=AnomalyAnalysisRequest, handler=anomaly_analysis_tool))
    # The ledger is scoped to the caller's explicit chat-session snapshot; a
    # save is permitted only when the user asked for it (the deterministic
    # chat route sets operation=save), while review remains read-only.
    registry.register(ToolSpec(name="assumption_tracking", kind="read_only", description="Save and verify research assumptions without rewriting their original values.", input_model=AssumptionTrackingRequest, handler=assumption_tracking_tool))
    registry.register(ToolSpec(name="get_signal_history", kind="read_only", description="Read recorded engine signals, trend-state transitions, and forward outcomes from the application database.", input_model=SignalHistoryRequest, handler=get_signal_history_tool))
    registry.register(ToolSpec(name="get_saved_scans", kind="read_only", description="Summarize saved Scanner presets from an explicit browser snapshot; unavailable without one.", input_model=SavedScansRequest, handler=get_saved_scans_tool))
    registry.register(ToolSpec(name="get_alerts", kind="read_only", description="Read application alert rules and optionally their recent triggers.", input_model=AlertsRequest, handler=get_alerts_tool))
    registry.register(ToolSpec(name="get_risk_dashboard", kind="read_only", description="Summarize an explicitly supplied manual position snapshot.", input_model=RiskDashboardRequest, handler=get_risk_dashboard_tool))
    registry.register(ToolSpec(name="assess_portfolio_risk", kind="read_only", description="Explain concentration, sector exposure, correlation, volatility, stop risk, drawdown, and scenario results for an explicit position snapshot; optionally size a new trade against the portfolio's risk capacity.", input_model=PortfolioRiskRequest, handler=assess_portfolio_risk_tool))
    registry.register(ToolSpec(name="build_trade_plan", kind="read_only", description="Build a verified trade plan: entry/stop/targets/reward-risk/position size, for the user to review before saving.", input_model=TradePlanRequest, handler=build_trade_plan_tool))
    registry.register(ToolSpec(name="decision_checklist", kind="read_only", description="Configurable pre-plan checklist (trend alignment, catalyst review, defined stop, verified position size, earnings risk, options liquidity, data freshness) with completed/failed/unavailable/skipped states.", input_model=DecisionChecklistRequest, handler=decision_checklist_tool))
    registry.register(ToolSpec(name="get_trade_journal", kind="read_only", description="Search or summarize an explicitly supplied local trade journal snapshot.", input_model=TradeJournalRequest, handler=get_trade_journal_tool))
    registry.register(ToolSpec(name="save_to_journal", kind="read_only", permission="mutating", description="Validate and append one typed entry to a browser-local Journal snapshot; requires confirmation.", input_model=SaveToJournalRequest, handler=save_to_journal_tool))
    registry.register(ToolSpec(name="export_report", kind="read_only", description="Format a verified trade plan/portfolio risk/options research/journal review (or an already-produced reply) into a markdown report with real frontend deep links.", input_model=ExportReportRequest, handler=export_report_tool))
    registry.register(ToolSpec(name="trade_journal_coach", kind="read_only", description="Evidence-based Journal coaching: plan-vs-actual, recurring mistake observations, per-setup performance, win rate, and expectancy from an explicitly supplied journal snapshot.", input_model=JournalCoachRequest, handler=trade_journal_coach_tool))
    registry.register(ToolSpec(name="get_application_help", kind="read_only", description="Find verified MarketLens pages and navigation targets.", input_model=ApplicationHelpRequest, handler=get_application_help_tool))
    registry.register(ToolSpec(name="import_csv", kind="read_only", description="Parse and validate local CSV text into positions, watchlist symbols, or trade-journal rows.", input_model=CsvImportRequest, handler=import_csv_tool))
    return registry


default_registry = build_default_registry()
