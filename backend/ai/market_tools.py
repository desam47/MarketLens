"""Read-only market-data tools for grounded AI answers."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import median, stdev
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SymbolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    session: Literal["premarket", "regular", "after_hours", "all"] = "all"


class BarsRequest(SymbolRequest):
    timeframe: str = "1d"
    range: str = Field(default="3mo", pattern=r"^[0-9]+(d|mo|y)$")
    limit: int = Field(default=200, ge=1, le=2_000)


class MoveAnalysisRequest(SymbolRequest):
    timeframe: str = "1d"
    range: str = Field(default="5d", pattern=r"^[0-9]+(d|mo|y)$")


class ChangeAnalysisRequest(SymbolRequest):
    timeframe: str = "1d"
    range: str = Field(default="1mo", pattern=r"^[0-9]+(d|mo|y)$")
    reference: Literal["previous_close", "yesterday", "last_visit", "timestamp"] = "previous_close"
    since: str | None = None


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(default_factory=list, max_length=25)
    watchlist: str | None = Field(default=None, min_length=1, max_length=100)
    metric: Literal["return_percent", "change_percent", "volatility_percent", "volume", "price"] = "return_percent"
    timeframe: str = "1d"
    range: str = Field(default="1mo", pattern=r"^[0-9]+(d|mo|y)$")
    session: Literal["premarket", "regular", "after_hours", "all"] = "all"
    direction: Literal["desc", "asc"] = "desc"
    limit: int = Field(default=25, ge=1, le=25)


class IndicatorRequest(BarsRequest):
    indicator: Literal["sma", "ema", "rsi", "change_percent"]
    period: int = Field(default=14, ge=2, le=200)


class TrendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    timeframe: str = "1d"


class ConfluenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    preset: Literal["scalper", "day_trading", "swing", "all"] = "day_trading"


class SessionStatsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    session: Literal["premarket", "regular", "after_hours", "all"] = "regular"


class TapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")


class CalendarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")


class NewsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    limit: int = Field(default=10, ge=1, le=50)


class FundamentalsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")


class OptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    expiration: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class WatchlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    watchlist_id: int | None = Field(default=None, ge=1)
    include_disabled: bool = False


class PositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    side: Literal["long", "short"] = "long"
    quantity: float = Field(..., gt=0)
    entry_price: float = Field(..., gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    current_price: float | None = Field(default=None, gt=0)
    sector: str | None = Field(default=None, max_length=100)


class RiskDashboardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Risk positions are browser-local in the current UI. Callers may pass a
    # snapshot explicitly; without it the tool must say that no server-side
    # portfolio is configured instead of pretending to see localStorage.
    positions: list[PositionInput] = Field(default_factory=list, max_length=500)


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Positions are browser-local in the current UI. Callers must pass the
    # explicit snapshot when they want a quantified scenario.
    positions: list[PositionInput] = Field(default_factory=list, max_length=500)
    price_shocks: dict[str, float] = Field(default_factory=dict, max_length=25)
    portfolio_shock_percent: float | None = None
    stop_price_overrides: dict[str, float] = Field(default_factory=dict, max_length=25)
    portfolio_value: float | None = Field(default=None, gt=0)


class HistoricalSimilarityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    timeframe: str = "1d"
    range: str = Field(default="2y", pattern=r"^[0-9]+(d|mo|y)$")
    session: Literal["premarket", "regular", "after_hours", "all"] = "all"
    lookback: int = Field(default=5, ge=2, le=50)
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 20], max_length=3)
    max_matches: int = Field(default=10, ge=1, le=25)
    tolerance: float = Field(default=2.0, gt=0, le=20)


class SignalExplanationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    preset: Literal["scalper", "day_trading", "swing", "all"] = "day_trading"
    timeframes: list[str] = Field(default_factory=lambda: ["5m", "15m", "1h", "1d"], max_length=10)
    include_historical: bool = False


class CounterargumentRequest(SignalExplanationRequest):
    pass


class SensitivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_price: float = Field(..., gt=0)
    stop_price: float = Field(..., gt=0)
    target_price: float | None = Field(default=None, gt=0)
    quantity: float = Field(..., gt=0)
    portfolio_value: float | None = Field(default=None, gt=0)
    entry_prices: list[float] = Field(default_factory=list, max_length=5)
    stop_prices: list[float] = Field(default_factory=list, max_length=5)
    target_prices: list[float] = Field(default_factory=list, max_length=5)
    quantities: list[float] = Field(default_factory=list, max_length=5)
    underlying_price: float | None = Field(default=None, gt=0)
    volatility_percent: float | None = Field(default=None, ge=0)
    days_to_expiration: float | None = Field(default=None, gt=0)
    volatility_percentages: list[float] = Field(default_factory=list, max_length=5)


class MarketEventTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    timeframe: str = "1d"
    range: str = Field(default="5d", pattern=r"^[0-9]+(d|mo|y)$")
    session: Literal["premarket", "regular", "after_hours", "all"] = "all"
    start: str | None = None
    end: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class AnomalyAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    timeframe: str = "1d"
    range: str = Field(default="3mo", pattern=r"^[0-9]+(d|mo|y)$")
    baseline_bars: int = Field(default=20, ge=5, le=200)
    z_threshold: float = Field(default=2.0, gt=0, le=10)
    spread_threshold_bps: float = Field(default=50.0, gt=0, le=10_000)
    benchmark_symbol: str | None = Field(default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    include_tape: bool = True
    include_options: bool = True
    positions: list[PositionInput] = Field(default_factory=list, max_length=500)
    portfolio_concentration_threshold: float = Field(default=40.0, gt=0, le=100)


class AssumptionInput(BaseModel):
    """One user-owned research assumption.

    ``original_*`` fields are deliberately not accepted from callers.  They
    are stamped by the ledger when a record is first saved and are never
    overwritten by later verification.
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    symbol: str | None = Field(default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    category: Literal["growth", "stop", "catalyst_date", "volatility", "invalidation", "custom"] = "custom"
    statement: str = Field(..., min_length=1, max_length=500)
    expected_value: float | str | None = None
    unit: str | None = Field(default=None, max_length=30)
    source: str = Field(default="user", min_length=1, max_length=200)
    stale_after_hours: float = Field(default=24.0, gt=0, le=8_760)


class AssumptionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assumption_id: str = Field(..., min_length=1, max_length=80)
    observed_value: float | str | None = None
    source: str = Field(..., min_length=1, max_length=200)
    observed_at: str | None = None
    contradicts: bool = False
    note: str | None = Field(default=None, max_length=500)


class AssumptionTrackingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["save", "review"] = "review"
    symbol: str | None = Field(default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    assumptions: list[AssumptionInput] = Field(default_factory=list, max_length=100)
    # Chat sessions keep this list in their structured planner state.  It is
    # also accepted by the standalone registry so the tool remains pure and
    # deterministic for API callers and tests.
    existing_assumptions: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    evidence: list[AssumptionEvidence] = Field(default_factory=list, max_length=200)


class TradeJournalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str | None = Field(default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    status: Literal["planned", "open", "closed"] | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list, max_length=1_000)


class CsvImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_type: Literal["positions", "watchlist", "trade_journal"]
    csv_content: str = Field(..., min_length=1, max_length=200_000)
    has_header: bool = True


class AlertsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str | None = Field(default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    enabled_only: bool = False
    include_recent_triggers: bool = False
    trigger_limit: int = Field(default=10, ge=1, le=100)


class ApplicationHelpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=5, ge=1, le=20)


def _manager():
    from backend.market_data.services.manager import market_data_manager

    return market_data_manager


def _trend_engine_provider(trend_engine) -> str | None:
    """Best-effort provider name fed into a single TrendEngine instance.

    Checks the daily then minute timeframe's bar metadata (the two most
    reliably warmed timeframes) and returns the first populated provider
    name. Returns None if the engine has no warmed metadata yet (cold
    engine) rather than guessing.
    """
    from backend.engines.timeframe import Timeframe

    for tf in (Timeframe.ONE_DAY, Timeframe.ONE_MINUTE):
        metadata = trend_engine.get_timeframe_metadata(tf)
        provider = metadata.get("provider")
        if provider:
            return provider
    return None


def get_quote_tool(request: SymbolRequest) -> BaseModel:
    from backend.ai.tool_registry import normalize_session
    from backend.config.settings import settings

    quote = _manager().get_quote(request.symbol.upper())
    primary = settings.market_data.primary_provider
    # MarketDataManager already applies the configured fallback chain and
    # returns one selected observation. Record that deterministic selection in
    # the tool payload without issuing a second provider request. If a future
    # manager path supplies multiple observations, the registry reconciliation
    # helper can compare them without changing this contract.
    fallback = quote.provider != primary
    return _Payload(
        symbol=quote.symbol,
        price=quote.price,
        bid=quote.bid,
        ask=quote.ask,
        volume=quote.volume,
        timestamp=quote.timestamp,
        provider=quote.provider,
        data_status=quote.data_status,
        session=normalize_session(request.session),
        selected_provider=quote.provider,
        primary_provider=primary,
        fallback=fallback,
        reconciliation={"observations": 1, "conflict": False, "selection": "manager_primary_or_fallback"},
    )


class _Payload(BaseModel):
    model_config = ConfigDict(extra="allow")


def get_bars_tool(request: BarsRequest) -> BaseModel:
    from backend.ai.tool_registry import normalize_session, normalize_timeframe
    from backend.config.settings import settings

    timeframe = normalize_timeframe(request.timeframe)
    bars = _manager().get_historical_bars(
        request.symbol.upper(),
        timeframe=timeframe,
        range_=request.range,
        include_extended_hours=request.session != "regular",
    )
    if not bars:
        raise ValueError(f"No {timeframe} bars available for {request.symbol.upper()}")
    selected = bars[-request.limit :]
    provider = selected[-1].provider
    return _Payload(
        symbol=request.symbol.upper(),
        timeframe=timeframe,
        session=normalize_session(request.session),
        bars=[bar.model_dump(mode="json") for bar in selected],
        provider=provider,
        source_timestamp=selected[-1].timestamp,
        fallback=provider != settings.market_data.primary_provider,
    )


def get_indicator_tool(request: IndicatorRequest) -> BaseModel:
    payload = get_bars_tool(request)
    bars = payload.bars
    if len(bars) < request.period:
        raise ValueError(f"At least {request.period} bars are required for {request.indicator}")
    closes = [float(bar["close"]) for bar in bars]
    if request.indicator == "sma":
        value = sum(closes[-request.period :]) / request.period
    elif request.indicator == "ema":
        value = closes[0]
        alpha = 2 / (request.period + 1)
        for close in closes[1:]:
            value = alpha * close + (1 - alpha) * value
    elif request.indicator == "change_percent":
        baseline = closes[-request.period]
        value = (closes[-1] - baseline) / abs(baseline) * 100 if baseline else 0
    else:
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        window = changes[-request.period :]
        gains = sum(max(change, 0) for change in window) / len(window)
        losses = sum(max(-change, 0) for change in window) / len(window)
        value = 100 if losses == 0 else 100 - (100 / (1 + gains / losses))
    return _Payload(**payload.model_dump(), indicator=request.indicator, period=request.period, value=round(value, 10))


def get_support_resistance_tool(request: BarsRequest) -> BaseModel:
    payload = get_bars_tool(request)
    bars = payload.bars
    return _Payload(
        **payload.model_dump(),
        support=min(float(bar["low"]) for bar in bars),
        resistance=max(float(bar["high"]) for bar in bars),
    )


def get_session_stats_tool(request: SessionStatsRequest) -> BaseModel:
    """Compute session-level statistics (O/H/L/C, volume, VWAP, range) for
    the most recent trading day, scoped to one market session.

    No dedicated page endpoint exists for this — same category as
    get_indicator/get_support_resistance, derived directly from bars
    already fetched through the shared manager/cache. Scoping honors each
    bar's own per-bar session classification (Bar.session, stamped by the
    provider/session-calendar boundary) rather than re-deriving session
    windows independently, so it cannot disagree with what the bars
    themselves already say about their session.
    """
    from backend.ai.tool_registry import normalize_session

    bars_payload = get_bars_tool(
        BarsRequest(symbol=request.symbol, timeframe="1m", range="5d", limit=2_000, session="all")
    )
    bars = bars_payload.bars
    if not bars:
        raise ValueError(f"No bars available for {request.symbol.upper()}")

    latest_date = max(bar["timestamp"][:10] for bar in bars)
    day_bars = [bar for bar in bars if bar["timestamp"].startswith(latest_date)]

    session = normalize_session(request.session)
    scoped_bars = day_bars if session == "all" else [bar for bar in day_bars if bar.get("session") == session]

    if not scoped_bars:
        return _Payload(
            symbol=request.symbol.upper(),
            session=session,
            date=latest_date,
            available=False,
            reason=f"No {session} bars available for {latest_date}",
            provider=bars_payload.provider,
            fallback=bars_payload.fallback,
        )

    open_price = float(scoped_bars[0]["open"])
    close_price = float(scoped_bars[-1]["close"])
    high_price = max(float(bar["high"]) for bar in scoped_bars)
    low_price = min(float(bar["low"]) for bar in scoped_bars)
    volume = sum(float(bar["volume"]) for bar in scoped_bars)
    typical_price_volume = sum(
        ((float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3) * float(bar["volume"])
        for bar in scoped_bars
    )
    vwap = typical_price_volume / volume if volume else None

    return _Payload(
        symbol=request.symbol.upper(),
        session=session,
        date=latest_date,
        available=True,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
        range=round(high_price - low_price, 6),
        change=round(close_price - open_price, 6),
        change_percent=round((close_price - open_price) / abs(open_price) * 100, 6) if open_price else None,
        vwap=round(vwap, 6) if vwap is not None else None,
        bar_count=len(scoped_bars),
        provider=bars_payload.provider,
        source_timestamp=bars_payload.source_timestamp,
        fallback=bars_payload.fallback,
    )


def get_market_regime_tool(request: SymbolRequest) -> BaseModel:
    from backend.api.regime.router import (
        _data_age_seconds,
        _freshness,
        _to_dashboard_tz,
        get_engine,
    )
    from backend.config.settings import settings

    symbol = request.symbol.upper()
    regime_engine = get_engine(symbol)
    provider = _trend_engine_provider(regime_engine.trend_engine) or "MarketLens engine"
    fallback = provider != "MarketLens engine" and provider != settings.market_data.primary_provider
    signal = regime_engine.get_current_regime()
    if signal is None:
        return _Payload(
            symbol=symbol,
            regime="unknown",
            confidence=0.0,
            strength=0.0,
            supporting_factors={},
            timestamp=None,
            data_age_seconds=None,
            freshness="unknown",
            provider=provider,
            fallback=fallback,
        )
    age = _data_age_seconds(signal.timestamp)
    return _Payload(
        symbol=signal.symbol,
        regime=signal.regime.value,
        confidence=signal.confidence,
        strength=signal.strength,
        supporting_factors=signal.supporting_factors,
        timestamp=_to_dashboard_tz(signal.timestamp),
        data_age_seconds=age,
        freshness=_freshness(age),
        provider=provider,
        fallback=fallback,
    )


def get_sector_data_tool(request: SymbolRequest) -> BaseModel:
    """Get the symbol's sector alignment vs. its sector ETF and SPY."""
    from backend.api.regime.router import _get_sector_engine
    from backend.config.settings import settings

    engine = _get_sector_engine(request.symbol.upper())
    signal = engine.get_current_signal()
    # The stock's own engine is the most relevant single provider to name —
    # the sector-ETF and SPY engines back the comparison, not the subject.
    provider = _trend_engine_provider(engine._stock_eng) or "MarketLens engine"
    fallback = provider != "MarketLens engine" and provider != settings.market_data.primary_provider
    return _Payload(**signal.to_dict(), provider=provider, fallback=fallback)


def get_trend_tool(request: TrendRequest) -> BaseModel:
    """Get the current trend for a symbol on one timeframe.

    Reuses backend.api.trend.router's own payload builder (the same one
    GET /api/trend/{symbol}/current/{timeframe} uses) rather than
    re-deriving the signal shape here — see the get_market_regime_tool/
    get_market_context_tool incident for why that separation matters.
    """
    from backend.ai.tool_registry import normalize_timeframe
    from backend.api.trend.registry import get_engine
    from backend.api.trend.router import _build_trend_payload
    from backend.config.settings import settings
    from backend.engines.timeframe import Timeframe

    symbol = request.symbol.upper()
    timeframe = normalize_timeframe(request.timeframe)
    tf = Timeframe(timeframe)
    engine = get_engine(symbol)
    payload = _build_trend_payload(engine, symbol, timeframe, tf)
    # Leave payload["provider"] exactly as the shared payload builder set it
    # (including None on a cold engine) so this stays contract-identical to
    # GET /api/trend/.../current/... — the registry already substitutes a
    # generic label for a missing provider; duplicating that here would
    # silently diverge from what the endpoint actually returns.
    provider = payload.get("provider")
    payload["fallback"] = bool(provider) and provider != settings.market_data.primary_provider
    return _Payload(**payload)


def get_confluence_tool(request: ConfluenceRequest) -> BaseModel:
    """Get multi-timeframe confluence for a symbol under a trading-style preset."""
    from backend.api.multitimeframe.router import build_confluence_payload, get_engine
    from backend.config.settings import settings

    symbol = request.symbol.upper()
    engine = get_engine(symbol, preset=request.preset)
    payload = build_confluence_payload(engine, symbol)
    provider = payload.get("provider", "MarketLens engine")
    payload["fallback"] = provider != "MarketLens engine" and provider != settings.market_data.primary_provider
    return _Payload(**payload)


def get_relative_strength_tool(request: SymbolRequest) -> BaseModel:
    """Get relative-strength signals for a symbol vs. its SPY/QQQ benchmarks."""
    from backend.api.regime.router import _get_rs_engine

    symbol = request.symbol.upper()
    engine = _get_rs_engine(symbol)
    signals = engine.compute()
    return _Payload(
        symbol=symbol,
        signals=[signal.to_dict() for signal in signals],
        count=len(signals),
        # Each signal already carries its own benchmark; this tool compares
        # the symbol against several of them at once, so no single
        # "provider" name describes the whole result the way it does for a
        # single-instrument tool like get_trend.
        provider="MarketLens engine",
    )


def get_tape_state_tool(request: TapeRequest) -> BaseModel:
    """Get the current tape snapshot: BBO, tape pressure, and large prints.

    Mirrors GET /api/tape/{symbol}, including its disabled-feature error —
    tape analytics require TAPE_ENABLED=true, and this must surface that as
    a normal tool error rather than a stack trace.
    """
    from backend.config.settings import settings

    if not settings.tape.enabled:
        raise ValueError("Tape analytics are disabled (set TAPE_ENABLED=true).")

    from backend.api.tape.registry import get_tape_engine
    from backend.utils.timezone import format_edt_iso, now_ny

    symbol = request.symbol.upper()
    snapshot = get_tape_engine(symbol).get_snapshot()
    # Tape data only ever comes from the Webull trade-tick MQTT stream
    # (backend/config/settings.py's TapeSettings docstring: "Off unless
    # TAPE_ENABLED=true (and the Webull MQTT stream running)") — there is
    # no fallback chain to be behind, so fallback is always False here.
    return _Payload(
        symbol=symbol, snapshot=snapshot, provider="webull", source_timestamp=format_edt_iso(now_ny())
    )


def get_market_context_tool(_: BaseModel) -> BaseModel:
    from backend.api.market_context.router import _to_dashboard_tz, get_engine

    # Composite across 4 different index symbols (SPY/QQQ/IWM/VIX), not one
    # instrument — no single provider name describes "the market", so this
    # is intentionally the composite-engine label, not a guess at which
    # index's provider matters most.
    provider = "MarketLens engine"
    signal = get_engine().get_current_context()
    if signal is None:
        return _Payload(
            regime="unknown",
            confidence=0.0,
            trend_strength=0.0,
            momentum=0.0,
            volatility_state="unknown",
            sub_regimes={},
            contributing_factors={"reason": "no_data"},
            timestamp=None,
            provider=provider,
        )
    payload = signal.to_dict()
    payload["timestamp"] = _to_dashboard_tz(signal.timestamp)
    payload["provider"] = provider
    return _Payload(**payload)


def _aux_manager():
    from backend.aux_data.services.manager import aux_data_manager

    return aux_data_manager


def get_news_tool(request: NewsRequest) -> BaseModel:
    from backend.config.settings import settings

    response = _aux_manager().get_news(request.symbol.upper(), limit=request.limit)
    return _Payload(
        symbol=response.symbol,
        items=[item.model_dump(mode="json") for item in response.items],
        provider=response.provider,
        source_timestamp=response.timestamp,
        fallback=response.provider != settings.aux_data.news.primary_provider,
    )


def get_calendar_tool(request: CalendarRequest) -> BaseModel:
    """Get upcoming earnings and dividend catalyst events for a symbol.

    Reuses backend.market_data.services.calendar_service.events_for_symbol
    directly — the exact function GET /api/calendar/symbol/{symbol} calls,
    including its 7-day-lookback/180-day-lookahead window and TTL cache —
    so results cannot diverge from what the Earnings & Events page shows.
    Insider ownership and analyst recommendation/target are already
    covered by get_fundamentals rather than duplicated here.
    """
    from backend.market_data.services.calendar_service import events_for_symbol

    symbol = request.symbol.upper()
    # The endpoint constructs each raw dict as CalendarEvent(**event), whose
    # `source: str = "yfinance"` default fills in a field events_for_symbol
    # itself never sets — match that here so the shapes agree exactly.
    events = [{"source": "yfinance", **event} for event in events_for_symbol(symbol)]
    return _Payload(symbol=symbol, events=events, provider="yfinance")


def get_fundamentals_tool(request: FundamentalsRequest) -> BaseModel:
    from backend.config.settings import settings

    response = _aux_manager().get_fundamentals(request.symbol.upper())
    return _Payload(
        symbol=response.symbol,
        data=response.data.model_dump(mode="json"),
        provider=response.provider,
        source_timestamp=response.timestamp,
        fallback=response.provider != settings.aux_data.fundamentals.primary_provider,
    )


def get_options_tool(request: OptionsRequest) -> BaseModel:
    from backend.config.settings import settings

    response = _aux_manager().get_options(request.symbol.upper(), expiration=request.expiration)
    return _Payload(
        symbol=response.symbol,
        chains=[chain.model_dump(mode="json") for chain in response.chains],
        expirations=response.expirations,
        near_term_iv=response.near_term_iv,
        iv_rank=response.iv_rank,
        provider=response.provider,
        source_timestamp=response.timestamp,
        fallback=response.provider != settings.aux_data.options.primary_provider,
    )


def why_did_it_move_tool(request: MoveAnalysisRequest) -> BaseModel:
    """Assemble evidence for a move without claiming an unverified cause."""
    symbol = request.symbol.upper()
    facts: list[dict] = []
    correlations: list[dict] = []
    unknowns: list[dict] = []
    sources: list[dict] = []

    try:
        bars_payload = get_bars_tool(
            BarsRequest(symbol=symbol, timeframe=request.timeframe, range=request.range, limit=200, session=request.session)
        ).model_dump(mode="json")
        bars = bars_payload.get("bars", [])
        if len(bars) >= 2:
            previous = float(bars[-2]["close"])
            latest = float(bars[-1]["close"])
            change_percent = (latest - previous) / abs(previous) * 100 if previous else None
            volumes = [float(bar.get("volume", 0) or 0) for bar in bars]
            baseline = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
            volume_ratio = volumes[-1] / baseline if baseline else None
            facts.append({"type": "price_move", "latest": latest, "previous": previous, "change_percent": change_percent})
            facts.append({"type": "volume", "latest": volumes[-1], "baseline": baseline, "ratio": volume_ratio})
        else:
            unknowns.append({"type": "price_move", "reason": "fewer than two bars"})
        sources.append({"name": "bars", "provider": bars_payload.get("provider"), "timestamp": bars_payload.get("source_timestamp")})
    except Exception as exc:  # optional evidence must not hide other sources
        unknowns.append({"type": "price_move", "reason": str(exc)})

    optional_sources = (
        ("news", lambda: get_news_tool(NewsRequest(symbol=symbol, limit=10))),
        ("options", lambda: get_options_tool(OptionsRequest(symbol=symbol))),
        ("sector", lambda: get_sector_data_tool(SymbolRequest(symbol=symbol))),
        ("regime", lambda: get_market_regime_tool(SymbolRequest(symbol=symbol))),
        ("tape", lambda: get_tape_state_tool(TapeRequest(symbol=symbol))),
    )
    for name, loader in optional_sources:
        try:
            payload = loader().model_dump(mode="json")
            source = {"name": name, "provider": payload.get("provider"), "timestamp": payload.get("source_timestamp") or payload.get("timestamp")}
            sources.append(source)
            if name == "news":
                items = payload.get("items") or []
                if items:
                    correlations.append({"type": "news_present", "items": items[:5], "causal": False})
                else:
                    unknowns.append({"type": "news", "reason": "no recent provider headlines"})
            elif name == "options":
                correlations.append({"type": "options_activity", "data": payload, "causal": False})
            else:
                correlations.append({"type": name, "data": payload, "causal": False})
        except Exception as exc:
            unknowns.append({"type": name, "reason": str(exc)})

    return _Payload(
        symbol=symbol,
        timeframe=request.timeframe,
        session=request.session,
        facts=facts,
        correlations=correlations,
        unknowns=unknowns,
        sources=sources,
        conclusion={
            "status": "evidence_only",
            "message": "Evidence can support or correlate with the move; it does not establish causation without a confirmed catalyst.",
        },
        provider="MarketLens composite",
    )


def what_changed_tool(request: ChangeAnalysisRequest) -> BaseModel:
    """Compare current verified bars with an explicit baseline."""
    symbol = request.symbol.upper()
    unknowns: list[dict] = []
    sources: list[dict] = []
    changes: list[dict] = []
    try:
        payload = get_bars_tool(
            BarsRequest(symbol=symbol, timeframe=request.timeframe, range=request.range, limit=500, session=request.session)
        ).model_dump(mode="json")
        bars = payload.get("bars", [])
        sources.append({"name": "bars", "provider": payload.get("provider"), "timestamp": payload.get("source_timestamp")})
        if len(bars) < 2:
            unknowns.append({"type": "baseline", "reason": "fewer than two bars available"})
        else:
            current = bars[-1]
            baseline = bars[-2]
            if request.reference == "timestamp":
                if not request.since:
                    unknowns.append({"type": "baseline", "reason": "timestamp reference requires since"})
                else:
                    candidates = [bar for bar in bars if str(bar.get("timestamp", "")) <= request.since]
                    if candidates:
                        baseline = candidates[-1]
                    else:
                        unknowns.append({"type": "baseline", "reason": "no bar at or before since timestamp"})
            elif request.reference == "last_visit" and not request.since:
                unknowns.append({"type": "baseline", "reason": "last_visit requires a persisted visit timestamp"})
            if not unknowns or unknowns[-1].get("type") != "baseline":
                current_close = float(current["close"])
                baseline_close = float(baseline["close"])
                changes.append(
                    {
                        "type": "price",
                        "current": current_close,
                        "baseline": baseline_close,
                        "delta": current_close - baseline_close,
                        "percent": (current_close - baseline_close) / abs(baseline_close) * 100 if baseline_close else None,
                    }
                )
                changes.append(
                    {
                        "type": "timestamp",
                        "current": current.get("timestamp"),
                        "baseline": baseline.get("timestamp"),
                        "reference": request.reference,
                    }
                )
    except Exception as exc:
        unknowns.append({"type": "bars", "reason": str(exc)})
    return _Payload(
        symbol=symbol,
        timeframe=request.timeframe,
        session=request.session,
        reference=request.reference,
        since=request.since,
        changes=changes,
        unknowns=unknowns,
        sources=sources,
        conclusion={"status": "verified_comparison" if changes else "insufficient_baseline"},
        provider="MarketLens comparison",
    )


def compare_symbols_tool(request: ComparisonRequest) -> BaseModel:
    """Rank verified symbol-bar metrics without asking the model to calculate."""
    symbols = [symbol.strip().upper() for symbol in request.symbols if symbol.strip()]
    invalid = [symbol for symbol in symbols if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol)]
    if invalid:
        raise ValueError(f"Invalid symbol(s): {', '.join(invalid)}")

    unknowns: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    if request.watchlist:
        try:
            watchlist_payload = get_watchlist_tool(WatchlistRequest(name=request.watchlist)).model_dump(mode="json")
            symbols.extend(str(item["symbol"]).upper() for item in watchlist_payload.get("symbols", []) if item.get("symbol"))
            sources.append(
                {
                    "name": "watchlist",
                    "provider": watchlist_payload.get("provider"),
                    "timestamp": watchlist_payload.get("source_timestamp"),
                }
            )
        except Exception as exc:
            unknowns.append({"type": "watchlist", "reason": str(exc)})

    symbols = list(dict.fromkeys(symbols))
    invalid = [symbol for symbol in symbols if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol)]
    if invalid:
        raise ValueError(f"Invalid symbol(s): {', '.join(invalid)}")
    if len(symbols) < 2:
        raise ValueError("Provide at least two symbols or a watchlist with at least two symbols to compare")
    if len(symbols) > 25:
        unknowns.append({"type": "symbol_limit", "reason": "Only the first 25 symbols were evaluated", "requested": len(symbols)})
        symbols = symbols[:25]

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            payload = get_bars_tool(
                BarsRequest(
                    symbol=symbol,
                    timeframe=request.timeframe,
                    range=request.range,
                    limit=2_000,
                    session=request.session,
                )
            ).model_dump(mode="json")
            bars = payload.get("bars", [])
            closes = [float(bar["close"]) for bar in bars if bar.get("close") is not None]
            volumes = [float(bar.get("volume", 0) or 0) for bar in bars]
            if len(closes) < 2:
                unknowns.append({"type": "symbol", "symbol": symbol, "reason": "fewer than two bars available"})
                continue
            returns = [
                (closes[index] - closes[index - 1]) / abs(closes[index - 1]) * 100
                for index in range(1, len(closes))
                if closes[index - 1]
            ]
            if request.metric == "return_percent":
                value = (closes[-1] - closes[0]) / abs(closes[0]) * 100 if closes[0] else None
            elif request.metric == "change_percent":
                value = returns[-1] if returns else None
            elif request.metric == "volatility_percent":
                value = stdev(returns) if len(returns) >= 2 else None
            elif request.metric == "volume":
                value = volumes[-1]
            else:
                value = closes[-1]
            if value is None:
                unknowns.append({"type": "symbol", "symbol": symbol, "reason": "insufficient observations for metric"})
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "value": round(value, 8),
                    "metric": request.metric,
                    "latest_price": closes[-1],
                    "bar_count": len(bars),
                    "provider": payload.get("provider"),
                    "source_timestamp": payload.get("source_timestamp"),
                    "session": payload.get("session", request.session),
                }
            )
            sources.append(
                {
                    "name": "bars",
                    "symbol": symbol,
                    "provider": payload.get("provider"),
                    "timestamp": payload.get("source_timestamp"),
                }
            )
        except Exception as exc:
            unknowns.append({"type": "symbol", "symbol": symbol, "reason": str(exc)})

    rows.sort(key=lambda row: (-row["value"] if request.direction == "desc" else row["value"], row["symbol"]))
    for rank, row in enumerate(rows[: request.limit], start=1):
        row["rank"] = rank
    rows = rows[: request.limit]
    return _Payload(
        symbols=symbols,
        metric=request.metric,
        direction=request.direction,
        timeframe=request.timeframe,
        session=request.session,
        range=request.range,
        rankings=rows,
        evaluated_count=len(rows),
        unknowns=unknowns,
        sources=sources,
        provider="MarketLens comparison",
        conclusion={
            "status": "verified_ranking" if rows else "insufficient_data",
            "message": "Rankings are calculated from returned provider bars; they are not a forecast.",
        },
    )


def scenario_analysis_tool(request: ScenarioRequest) -> BaseModel:
    """Recalculate a supplied position snapshot under explicit price/stop shocks."""
    if not request.positions:
        return _Payload(
            available=False,
            reason="No position snapshot was supplied. Risk Dashboard positions are browser-local; pass them to run a quantified scenario.",
            positions=[],
            assumptions=["No forecast or execution was performed."],
            provider="MarketLens calculator",
            source_timestamp=_database_timestamp(),
        )

    price_shocks = {symbol.upper(): value for symbol, value in request.price_shocks.items()}
    stop_price_overrides = {symbol.upper(): value for symbol, value in request.stop_price_overrides.items()}
    if request.portfolio_shock_percent is not None and not (-100 <= request.portfolio_shock_percent):
        raise ValueError("portfolio_shock_percent cannot be below -100")
    for mapping_name, mapping in (("price_shocks", price_shocks), ("stop_price_overrides", stop_price_overrides)):
        for symbol, value in mapping.items():
            if not re.fullmatch(r"[A-Za-z0-9.\-]{1,20}", symbol) or not math.isfinite(float(value)):
                raise ValueError(f"Invalid {mapping_name} entry for {symbol}")
            if mapping_name == "price_shocks" and value < -100:
                raise ValueError("price shocks cannot be below -100%")
            if mapping_name == "stop_price_overrides" and value <= 0:
                raise ValueError("stop price overrides must be positive")

    rows: list[dict[str, Any]] = []
    base_gross = scenario_gross = base_net = scenario_net = 0.0
    base_stop_risk = scenario_stop_risk = 0.0
    total_pnl_delta = 0.0
    sectors: dict[str, float] = {}
    unknowns: list[dict[str, Any]] = []
    for position in request.positions:
        symbol = position.symbol.upper()
        base_price = position.current_price or position.entry_price
        shock = price_shocks.get(symbol, request.portfolio_shock_percent or 0.0)
        scenario_price = base_price * (1 + shock / 100)
        sign = 1 if position.side == "long" else -1
        base_value = base_price * position.quantity
        scenario_value = scenario_price * position.quantity
        entry_pnl = (base_price - position.entry_price) * position.quantity * sign
        scenario_pnl = (scenario_price - position.entry_price) * position.quantity * sign
        stop_price = stop_price_overrides.get(symbol, position.stop_price)
        base_risk = scenario_risk = None
        if position.stop_price is not None:
            base_risk = max(0.0, (position.entry_price - position.stop_price) * position.quantity * sign)
        if stop_price is not None:
            scenario_risk = max(0.0, (position.entry_price - stop_price) * position.quantity * sign)
            scenario_stop_risk += scenario_risk
        if base_risk is not None:
            base_stop_risk += base_risk
        base_gross += base_value
        scenario_gross += scenario_value
        base_net += base_value * sign
        scenario_net += scenario_value * sign
        total_pnl_delta += scenario_pnl - entry_pnl
        sector = position.sector or "Unknown"
        sectors[sector] = sectors.get(sector, 0.0) + scenario_value
        rows.append(
            {
                "symbol": symbol,
                "side": position.side,
                "quantity": position.quantity,
                "base_price": base_price,
                "price_shock_percent": shock,
                "scenario_price": scenario_price,
                "market_value_delta": scenario_value - base_value,
                "base_pnl": entry_pnl,
                "scenario_pnl": scenario_pnl,
                "pnl_delta": scenario_pnl - entry_pnl,
                "stop_price": stop_price,
                "stop_risk": scenario_risk,
                "sector": sector,
            }
        )

    if request.portfolio_value is None:
        unknowns.append({"type": "portfolio_risk_percent", "reason": "portfolio_value was not supplied"})
    delta_percent = total_pnl_delta / base_gross * 100 if base_gross else None
    return _Payload(
        available=True,
        positions=rows,
        base_gross_exposure=round(base_gross, 8),
        scenario_gross_exposure=round(scenario_gross, 8),
        base_net_exposure=round(base_net, 8),
        scenario_net_exposure=round(scenario_net, 8),
        total_pnl_delta=round(total_pnl_delta, 8),
        portfolio_change_percent=round(delta_percent, 8) if delta_percent is not None else None,
        base_stop_loss_risk=round(base_stop_risk, 8),
        scenario_stop_loss_risk=round(scenario_stop_risk, 8),
        scenario_stop_risk_percent=round(scenario_stop_risk / request.portfolio_value * 100, 8) if request.portfolio_value else None,
        sector_exposure=[
            {"sector": sector, "market_value": round(value, 8), "weight_percent": round(value / scenario_gross * 100, 8) if scenario_gross else 0.0}
            for sector, value in sorted(sectors.items(), key=lambda item: item[1], reverse=True)
        ],
        unknowns=unknowns,
        assumptions=[
            "Scenario prices are deterministic mark-to-market estimates; they are not a forecast.",
            "Quantities are unchanged and commissions, slippage, taxes, dividends, and execution are excluded.",
            "Short positions use inverse P&L and exposure signs.",
        ],
        provider="MarketLens calculator",
        source_timestamp=_database_timestamp(),
        conclusion={"status": "verified_scenario", "message": "Scenario outputs reflect only the supplied assumptions."},
    )


def historical_similarity_tool(request: HistoricalSimilarityRequest) -> BaseModel:
    """Find prior bar windows with similar return/volatility features."""
    symbol = request.symbol.upper()
    horizons = sorted(set(request.horizons))
    if not horizons or any(horizon < 1 or horizon > 252 for horizon in horizons):
        raise ValueError("horizons must contain values from 1 through 252")
    try:
        payload = get_bars_tool(
            BarsRequest(
                symbol=symbol,
                timeframe=request.timeframe,
                range=request.range,
                limit=2_000,
                session=request.session,
            )
        ).model_dump(mode="json")
    except Exception as exc:
        return _Payload(
            available=False,
            symbol=symbol,
            reason=str(exc),
            matches=[],
            summaries=[],
            unknowns=[{"type": "bars", "reason": str(exc)}],
            provider="MarketLens similarity",
        )

    bars = payload.get("bars", [])
    closes = [float(bar["close"]) for bar in bars if bar.get("close") is not None]
    if len(closes) < request.lookback + max(horizons) + 2:
        return _Payload(
            available=False,
            symbol=symbol,
            timeframe=request.timeframe,
            lookback=request.lookback,
            reason="Not enough bars for a non-overlapping historical comparison and requested horizons",
            matches=[],
            summaries=[],
            unknowns=[{"type": "baseline", "reason": "insufficient bars"}],
            provider="MarketLens similarity",
            source_timestamp=payload.get("source_timestamp"),
        )

    def features(end_index: int) -> tuple[float, float]:
        window = closes[end_index - request.lookback + 1 : end_index + 1]
        returns = [(window[index] / window[index - 1] - 1) * 100 for index in range(1, len(window)) if window[index - 1]]
        cumulative = (window[-1] - window[0]) / abs(window[0]) * 100 if window[0] else 0.0
        volatility = stdev(returns) if len(returns) >= 2 else 0.0
        return cumulative, volatility

    current_end = len(closes) - 1
    current_return, current_volatility = features(current_end)
    # Candidate windows end before the current lookback window. Their outcome
    # bars may extend forward, but never into the current setup window.
    latest_candidate_end = min(
        len(closes) - request.lookback - 1,
        len(closes) - max(horizons) - 1,
    )
    earliest_candidate_end = request.lookback - 1
    candidates: list[dict[str, Any]] = []
    for end_index in range(earliest_candidate_end, latest_candidate_end + 1):
        candidate_return, candidate_volatility = features(end_index)
        distance = abs(candidate_return - current_return) + abs(candidate_volatility - current_volatility)
        if distance > request.tolerance:
            continue
        outcomes = {}
        for horizon in horizons:
            outcomes[str(horizon)] = (closes[end_index + horizon] - closes[end_index]) / abs(closes[end_index]) * 100 if closes[end_index] else None
        candidates.append(
            {
                "end_index": end_index,
                "timestamp": bars[end_index].get("timestamp") if end_index < len(bars) else None,
                "distance": round(distance, 8),
                "feature_return_percent": round(candidate_return, 8),
                "feature_volatility_percent": round(candidate_volatility, 8),
                "outcomes": outcomes,
            }
        )
    candidates.sort(key=lambda item: (item["distance"], item["timestamp"] or ""), reverse=False)
    matches = candidates[: request.max_matches]
    summaries = []
    for horizon in horizons:
        values = [match["outcomes"][str(horizon)] for match in matches if match["outcomes"].get(str(horizon)) is not None]
        summaries.append(
            {
                "horizon": horizon,
                "sample_size": len(values),
                "mean_return_percent": round(sum(values) / len(values), 8) if values else None,
                "median_return_percent": round(median(values), 8) if values else None,
                "win_rate_percent": round(sum(value > 0 for value in values) / len(values) * 100, 8) if values else None,
                "min_return_percent": round(min(values), 8) if values else None,
                "max_return_percent": round(max(values), 8) if values else None,
            }
        )
    return _Payload(
        available=True,
        symbol=symbol,
        timeframe=payload.get("timeframe", request.timeframe),
        session=payload.get("session", request.session),
        lookback=request.lookback,
        horizons=horizons,
        current_features={"return_percent": round(current_return, 8), "volatility_percent": round(current_volatility, 8)},
        matches=matches,
        summaries=summaries,
        sample_size=len(matches),
        look_ahead_safe=True,
        unknowns=[] if matches else [{"type": "matches", "reason": "no prior windows within tolerance"}],
        sources=[{"name": "bars", "provider": payload.get("provider"), "timestamp": payload.get("source_timestamp")}],
        provider="MarketLens similarity",
        source_timestamp=payload.get("source_timestamp"),
        conclusion={
            "status": "verified_similarity" if matches else "insufficient_similarity",
            "message": "Historical outcomes are descriptive samples, not forecasts; small samples should not be generalized.",
        },
    )


def signal_explanation_tool(request: SignalExplanationRequest) -> BaseModel:
    """Assemble indicator, timeframe, tape, freshness, and state evidence."""
    symbol = request.symbol.upper()
    unknowns: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    trends: dict[str, dict[str, Any]] = {}
    for timeframe in request.timeframes:
        try:
            payload = get_trend_tool(TrendRequest(symbol=symbol, timeframe=timeframe)).model_dump(mode="json")
            trends[timeframe] = payload
            sources.append({"name": "trend", "timeframe": timeframe, "provider": payload.get("provider"), "timestamp": payload.get("timestamp")})
        except Exception as exc:
            unknowns.append({"type": "trend", "timeframe": timeframe, "reason": str(exc)})

    try:
        confluence = get_confluence_tool(ConfluenceRequest(symbol=symbol, preset=request.preset)).model_dump(mode="json")
        sources.append({"name": "confluence", "provider": confluence.get("provider"), "timestamp": confluence.get("timestamp")})
    except Exception as exc:
        confluence = {}
        unknowns.append({"type": "confluence", "reason": str(exc)})

    indicators: dict[str, Any] = {}
    triggers: list[dict[str, Any]] = []
    try:
        bars_payload = get_bars_tool(BarsRequest(symbol=symbol, timeframe="1d", range="3mo", limit=200)).model_dump(mode="json")
        bars = bars_payload.get("bars", [])
        closes = [float(bar["close"]) for bar in bars if bar.get("close") is not None]
        volumes = [float(bar.get("volume", 0) or 0) for bar in bars]
        if len(closes) >= 2:
            latest = closes[-1]
            indicators["price"] = latest
            indicators["change_percent"] = (latest - closes[-2]) / abs(closes[-2]) * 100 if closes[-2] else None
            if len(closes) >= 20:
                sma20 = sum(closes[-20:]) / 20
                ema20 = closes[0]
                alpha = 2 / 21
                for close in closes[1:]:
                    ema20 = alpha * close + (1 - alpha) * ema20
                indicators["sma_20"] = sma20
                indicators["ema_20"] = ema20
                if latest > sma20:
                    triggers.append({"indicator": "sma_20", "direction": "bullish", "value": round(sma20, 8), "reason": "price is above SMA 20"})
                elif latest < sma20:
                    triggers.append({"indicator": "sma_20", "direction": "bearish", "value": round(sma20, 8), "reason": "price is below SMA 20"})
            changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
            window = changes[-14:]
            gains = sum(max(change, 0) for change in window) / len(window)
            losses = sum(max(-change, 0) for change in window) / len(window)
            rsi = 100 if losses == 0 else 100 - (100 / (1 + gains / losses))
            indicators["rsi_14"] = rsi
            if rsi < 30:
                triggers.append({"indicator": "rsi_14", "direction": "bullish", "value": round(rsi, 8), "reason": "RSI is oversold"})
            elif rsi > 70:
                triggers.append({"indicator": "rsi_14", "direction": "bearish", "value": round(rsi, 8), "reason": "RSI is overbought"})
            if len(volumes) >= 21:
                average_volume = sum(volumes[-21:-1]) / 20
                volume_ratio = volumes[-1] / average_volume if average_volume else None
                indicators["volume_ratio"] = volume_ratio
                if volume_ratio is not None and volume_ratio >= 1.5:
                    triggers.append({"indicator": "volume_ratio", "direction": "context", "value": round(volume_ratio, 8), "reason": "volume is elevated versus its 20-bar average"})
        sources.append({"name": "bars", "provider": bars_payload.get("provider"), "timestamp": bars_payload.get("source_timestamp")})
    except Exception as exc:
        unknowns.append({"type": "indicators", "reason": str(exc)})

    try:
        tape = get_tape_state_tool(TapeRequest(symbol=symbol)).model_dump(mode="json")
        sources.append({"name": "tape", "provider": tape.get("provider"), "timestamp": tape.get("source_timestamp")})
        snapshot = tape.get("snapshot") or {}
        pressure = snapshot.get("pressure") or snapshot.get("tape_pressure") or snapshot.get("direction")
        tape_direction = "bullish" if str(pressure).lower() in {"buy", "buying", "bullish", "positive"} else "bearish" if str(pressure).lower() in {"sell", "selling", "bearish", "negative"} else "neutral"
        tape_evidence = {"available": True, "pressure": pressure, "direction": tape_direction, "snapshot": snapshot}
    except Exception as exc:
        tape_evidence = {"available": False, "direction": "unknown"}
        unknowns.append({"type": "tape", "reason": str(exc)})

    timeframe_rows: list[dict[str, Any]] = []
    counts = {"bullish": 0, "bearish": 0, "neutral": 0, "unknown": 0}
    for timeframe, payload in trends.items():
        raw_direction = str(payload.get("direction") or "unknown").lower()
        direction = "bullish" if raw_direction in {"bullish", "uptrend", "strong_uptrend"} or "up" in raw_direction else "bearish" if raw_direction in {"bearish", "downtrend", "strong_downtrend"} or "down" in raw_direction else "neutral" if raw_direction not in {"unknown", "none"} else "unknown"
        counts[direction] += 1
        timeframe_rows.append({"timeframe": timeframe, "direction": direction, "raw_direction": raw_direction, "confidence": payload.get("confidence"), "age_seconds": payload.get("data_age_seconds")})
    known_count = counts["bullish"] + counts["bearish"] + counts["neutral"]
    dominant = max(("bullish", counts["bullish"]), ("bearish", counts["bearish"]), ("neutral", counts["neutral"]), key=lambda item: item[1])[0] if known_count else "unknown"
    agreement = {
        "dominant": dominant,
        "bullish": counts["bullish"],
        "bearish": counts["bearish"],
        "neutral": counts["neutral"],
        "unknown": counts["unknown"],
        "total": len(timeframe_rows),
        "alignment_percent": round(max(counts["bullish"], counts["bearish"], counts["neutral"]) / known_count * 100, 8) if known_count else 0.0,
        "timeframes": timeframe_rows,
    }

    signal_timestamp = confluence.get("timestamp") or next((row.get("timestamp") for row in trends.values() if row.get("timestamp")), None)
    age_seconds = None
    if signal_timestamp:
        try:
            parsed_timestamp = datetime.fromisoformat(str(signal_timestamp).replace("Z", "+00:00"))
            if parsed_timestamp.tzinfo is None:
                parsed_timestamp = parsed_timestamp.replace(tzinfo=UTC)
            age_seconds = max(0.0, (datetime.now(UTC) - parsed_timestamp.astimezone(UTC)).total_seconds())
        except ValueError:
            unknowns.append({"type": "freshness", "reason": "signal timestamp could not be parsed"})
    freshness = {"status": "fresh" if age_seconds is not None and age_seconds <= 60 else "recent" if age_seconds is not None and age_seconds <= 900 else "stale" if age_seconds is not None else "unknown", "age_seconds": round(age_seconds, 8) if age_seconds is not None else None, "timestamp": signal_timestamp}

    previous_changes: list[dict[str, Any]] = []
    try:
        from backend.api.trend.registry import get_engine
        from backend.engines.timeframe import Timeframe

        engine = get_engine(symbol)
        for timeframe in request.timeframes:
            history = engine.get_trend_history(Timeframe(timeframe), limit=2)
            if len(history) >= 2:
                previous = history[-2]
                current = history[-1]
                previous_changes.append({"timeframe": timeframe, "changed": previous.direction != current.direction or previous.classification != current.classification, "previous_direction": previous.direction.value, "current_direction": current.direction.value, "previous_timestamp": previous.timestamp.isoformat(), "current_timestamp": current.timestamp.isoformat()})
    except Exception as exc:
        unknowns.append({"type": "previous_signal", "reason": str(exc)})
    if not previous_changes:
        unknowns.append({"type": "previous_signal", "reason": "no prior signal state is available"})

    historical_performance = None
    historical_note = "Historical setup outcomes were not requested."
    if request.include_historical:
        historical = historical_similarity_tool(
            HistoricalSimilarityRequest(symbol=symbol, lookback=5, horizons=[1, 5, 20], max_matches=10)
        ).model_dump(mode="json")
        historical_performance = historical.get("summaries")
        historical_note = "Historical outcomes are descriptive samples, not forecasts; review sample size before generalizing."
        if historical.get("available") is False:
            unknowns.append({"type": "historical_performance", "reason": historical.get("reason", "unavailable")})

    tape_confirmed = tape_evidence.get("direction") in {"bullish", "bearish"} and tape_evidence.get("direction") == dominant
    tape_contradicts = tape_evidence.get("direction") in {"bullish", "bearish"} and dominant in {"bullish", "bearish"} and tape_evidence.get("direction") != dominant
    return _Payload(
        symbol=symbol,
        preset=request.preset,
        direction=confluence.get("direction", dominant),
        confidence=confluence.get("strength") or confluence.get("alignment_score"),
        indicators=indicators,
        triggers=triggers,
        timeframe_agreement=agreement,
        tape=tape_evidence,
        tape_relation={"confirms": tape_confirmed, "contradicts": tape_contradicts},
        freshness=freshness,
        previous_signal_changes=previous_changes,
        historical_performance=historical_performance,
        historical_performance_note=historical_note,
        unknowns=unknowns,
        sources=sources,
        provider="MarketLens signal explanation",
        conclusion={"status": "verified_explanation" if trends or triggers else "insufficient_data", "message": "Evidence describes the current signal; it does not guarantee a future outcome."},
    )


def counterargument_review_tool(request: CounterargumentRequest) -> BaseModel:
    """Surface only evidence-backed opposing evidence and invalidations."""
    explanation = signal_explanation_tool(request).model_dump(mode="json")
    direction = str(explanation.get("direction") or "neutral").lower()
    dominant = direction if direction in {"bullish", "bearish"} else explanation.get("timeframe_agreement", {}).get("dominant", "neutral")
    opposite = "bearish" if dominant == "bullish" else "bullish" if dominant == "bearish" else None
    counterarguments: list[dict[str, Any]] = []
    if opposite:
        for row in explanation.get("timeframe_agreement", {}).get("timeframes", []):
            if row.get("direction") == opposite:
                counterarguments.append({"type": "timeframe_conflict", "timeframe": row.get("timeframe"), "direction": opposite, "confidence": row.get("confidence")})
        for trigger in explanation.get("triggers", []):
            if trigger.get("direction") == opposite:
                counterarguments.append({"type": "indicator_conflict", **trigger})
        if explanation.get("tape_relation", {}).get("contradicts"):
            counterarguments.append({"type": "tape_conflict", "tape": explanation.get("tape")})

    indicators = explanation.get("indicators", {})
    invalidations: list[dict[str, Any]] = []
    sma20 = indicators.get("sma_20")
    if isinstance(sma20, (int, float)) and dominant in {"bullish", "bearish"}:
        invalidations.append({"type": "price_level", "condition": "close_below" if dominant == "bullish" else "close_above", "level": sma20, "reason": "a moving-average break would contradict the current directional evidence"})
    agreement = explanation.get("timeframe_agreement", {})
    if dominant in {"bullish", "bearish"}:
        invalidations.append({"type": "timeframe_alignment", "condition": "opposite_side_dominates", "threshold": {"current_dominant": dominant, "current_count": agreement.get(dominant), "opposite_count": agreement.get(opposite)}})
    unknowns = list(explanation.get("unknowns", []))
    if not counterarguments:
        unknowns.append({"type": "counterargument", "reason": "no credible opposing evidence was available"})
    if not invalidations:
        unknowns.append({"type": "invalidation", "reason": "no evidence-backed invalidation threshold was available"})
    return _Payload(
        symbol=explanation.get("symbol", request.symbol.upper()),
        direction=dominant,
        supporting_evidence=explanation.get("triggers", []),
        counterarguments=counterarguments,
        invalidations=invalidations,
        freshness=explanation.get("freshness"),
        sources=explanation.get("sources", []),
        unknowns=unknowns,
        provider="MarketLens signal review",
        conclusion={"status": "verified_review", "message": "Counterarguments and invalidations reflect available evidence; absent evidence is not treated as a balanced opposing case."},
    )


def sensitivity_analysis_tool(request: SensitivityRequest) -> BaseModel:
    """Run bounded one-factor-at-a-time sensitivity calculations."""
    if request.entry_price == request.stop_price:
        raise ValueError("entry_price and stop_price must differ")
    for name, values in (
        ("entry_prices", request.entry_prices),
        ("stop_prices", request.stop_prices),
        ("target_prices", request.target_prices),
        ("quantities", request.quantities),
        ("volatility_percentages", request.volatility_percentages),
    ):
        if any(float(value) <= 0 for value in values):
            raise ValueError(f"{name} must contain positive values")

    def row(label: str, entry: float, stop: float, target: float | None, quantity: float, volatility: float | None) -> dict[str, Any]:
        risk = abs(entry - stop) * quantity
        reward = abs(target - entry) * quantity if target is not None else None
        expected_move = None
        if request.underlying_price is not None and volatility is not None and request.days_to_expiration:
            iv = volatility / 100 if volatility > 10 else volatility
            expected_move = request.underlying_price * iv * math.sqrt(request.days_to_expiration / 365)
        return {
            "case": label,
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "quantity": quantity,
            "risk_dollars": risk,
            "reward_dollars": reward,
            "risk_reward": reward / risk if reward is not None and risk else None,
            "allocation_percent": entry * quantity / request.portfolio_value * 100 if request.portfolio_value else None,
            "volatility_percent": volatility,
            "expected_move": expected_move,
        }

    base = row("base", request.entry_price, request.stop_price, request.target_price, request.quantity, request.volatility_percent)
    scenarios = [base]
    scenarios.extend(row("entry", value, request.stop_price, request.target_price, request.quantity, request.volatility_percent) for value in request.entry_prices)
    scenarios.extend(row("stop", request.entry_price, value, request.target_price, request.quantity, request.volatility_percent) for value in request.stop_prices)
    scenarios.extend(row("target", request.entry_price, request.stop_price, value, request.quantity, request.volatility_percent) for value in request.target_prices)
    scenarios.extend(row("quantity", request.entry_price, request.stop_price, request.target_price, value, request.volatility_percent) for value in request.quantities)
    scenarios.extend(row("volatility", request.entry_price, request.stop_price, request.target_price, request.quantity, value) for value in request.volatility_percentages)
    drivers = []
    for field in ("risk_dollars", "reward_dollars", "risk_reward", "allocation_percent", "expected_move"):
        values = [item[field] for item in scenarios[1:] if item[field] is not None]
        base_value = base[field]
        if values and base_value not in (None, 0):
            drivers.append({"metric": field, "absolute_change": round(max(abs(value - base_value) for value in values), 8)})
    drivers.sort(key=lambda item: item["absolute_change"], reverse=True)
    return _Payload(
        available=True,
        base=base,
        scenarios=scenarios[1:],
        most_sensitive=drivers[:3],
        assumptions=["Each scenario varies one input at a time; outputs are conditional calculations, not forecasts.", "Fees, slippage, taxes, and execution effects are excluded."],
        provider="MarketLens calculator",
        source_timestamp=_database_timestamp(),
        conclusion={"status": "verified_sensitivity", "message": "Sensitivity shows how supplied assumptions change the calculated outputs."},
    )


def _timeline_timestamp(value: Any) -> tuple[str | None, datetime | None]:
    """Normalize provider/date values to explicit America/New_York ISO."""
    from backend.utils.timezone import NY, format_edt_iso

    if value is None:
        return None, None
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value)
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=NY)
        else:
            parsed = parsed.astimezone(NY)
        return format_edt_iso(parsed), parsed
    except (TypeError, ValueError):
        return str(value), None


def market_event_timeline_tool(request: MarketEventTimelineRequest) -> BaseModel:
    """Combine normalized price, session, signal, alert, and auxiliary events."""
    symbol = request.symbol.upper()
    events: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    try:
        bars_payload = get_bars_tool(BarsRequest(symbol=symbol, timeframe=request.timeframe, range=request.range, limit=500, session=request.session)).model_dump(mode="json")
        previous_session = None
        for bar in bars_payload.get("bars", []):
            timestamp, _ = _timeline_timestamp(bar.get("timestamp"))
            session = bar.get("session") or "unknown"
            events.append({"type": "price_bar", "timestamp": timestamp, "symbol": symbol, "session": session, "data": {key: bar.get(key) for key in ("open", "high", "low", "close", "volume")}, "provider": bars_payload.get("provider"), "source_timestamp": bar.get("timestamp")})
            if session != previous_session:
                events.append({"type": "session_transition", "timestamp": timestamp, "symbol": symbol, "session": session, "data": {"from": previous_session, "to": session}, "provider": bars_payload.get("provider"), "source_timestamp": bar.get("timestamp")})
                previous_session = session
        sources.append({"name": "bars", "provider": bars_payload.get("provider"), "timestamp": bars_payload.get("source_timestamp")})
    except Exception as exc:
        unknowns.append({"type": "price_bars", "reason": str(exc)})

    loaders: tuple[tuple[str, Any, str], ...] = (
        ("signals", lambda: get_confluence_tool(ConfluenceRequest(symbol=symbol)), "timestamp"),
        ("alerts", lambda: get_alerts_tool(AlertsRequest(symbol=symbol, include_recent_triggers=True)), "source_timestamp"),
        ("news", lambda: get_news_tool(NewsRequest(symbol=symbol, limit=50)), "source_timestamp"),
        ("calendar", lambda: get_calendar_tool(CalendarRequest(symbol=symbol)), "source_timestamp"),
        ("fundamentals", lambda: get_fundamentals_tool(FundamentalsRequest(symbol=symbol)), "source_timestamp"),
        ("options", lambda: get_options_tool(OptionsRequest(symbol=symbol)), "source_timestamp"),
    )
    for name, loader, timestamp_key in loaders:
        try:
            payload = loader().model_dump(mode="json")
            sources.append({"name": name, "provider": payload.get("provider"), "timestamp": payload.get(timestamp_key) or payload.get("timestamp")})
            if name == "signals" and payload.get("timestamp"):
                timestamp, _ = _timeline_timestamp(payload.get("timestamp"))
                events.append({"type": "signal", "timestamp": timestamp, "symbol": symbol, "session": request.session, "data": {key: payload.get(key) for key in ("direction", "strength", "alignment_score", "conflicting", "preset")}, "provider": payload.get("provider"), "source_timestamp": payload.get("timestamp")})
            elif name == "alerts":
                for alert in payload.get("alerts", []):
                    for trigger in alert.get("recent_triggers", []):
                        timestamp, _ = _timeline_timestamp(trigger.get("triggered_at"))
                        events.append({"type": "alert", "timestamp": timestamp, "symbol": symbol, "session": request.session, "data": {"alert": alert.get("name"), **trigger}, "provider": payload.get("provider"), "source_timestamp": trigger.get("triggered_at")})
            elif name == "news":
                for item in payload.get("items", []):
                    timestamp, _ = _timeline_timestamp(item.get("timestamp"))
                    events.append({"type": "news", "timestamp": timestamp, "symbol": symbol, "session": request.session, "data": item, "provider": payload.get("provider"), "source_timestamp": item.get("timestamp")})
            elif name == "calendar":
                for item in payload.get("events", []):
                    timestamp, _ = _timeline_timestamp(item.get("date"))
                    events.append({"type": item.get("event_type", "calendar"), "timestamp": timestamp, "symbol": symbol, "session": "all", "data": item, "provider": payload.get("provider", "yfinance"), "source_timestamp": item.get("date")})
            elif name == "fundamentals":
                data = payload.get("data") or {}
                timestamp, _ = _timeline_timestamp(payload.get("source_timestamp"))
                if data.get("recommendation") is not None or data.get("analyst_target") is not None:
                    events.append({"type": "analyst_snapshot", "timestamp": timestamp, "symbol": symbol, "session": "all", "data": {key: data.get(key) for key in ("recommendation", "analyst_target")}, "provider": payload.get("provider"), "source_timestamp": payload.get("source_timestamp")})
                if data.get("insider_ownership") is not None:
                    events.append({"type": "insider_snapshot", "timestamp": timestamp, "symbol": symbol, "session": "all", "data": {"insider_ownership": data.get("insider_ownership")}, "provider": payload.get("provider"), "source_timestamp": payload.get("source_timestamp")})
            elif name == "options":
                for chain in payload.get("chains", []):
                    timestamp, _ = _timeline_timestamp(chain.get("expiration"))
                    events.append({"type": "options_snapshot", "timestamp": timestamp, "symbol": symbol, "session": "all", "data": {key: chain.get(key) for key in ("expiration", "put_call_ratio", "total_call_volume", "total_put_volume", "unusual_activity")}, "provider": payload.get("provider"), "source_timestamp": payload.get("source_timestamp")})
        except Exception as exc:
            unknowns.append({"type": name, "reason": str(exc)})

    start_dt = _timeline_timestamp(request.start)[1] if request.start else None
    end_dt = _timeline_timestamp(request.end)[1] if request.end else None
    filtered: list[dict[str, Any]] = []
    for event in events:
        event_dt = _timeline_timestamp(event.get("timestamp"))[1]
        if start_dt and (event_dt is None or event_dt < start_dt):
            continue
        if end_dt and (event_dt is None or event_dt > end_dt):
            continue
        filtered.append(event)
    filtered.sort(key=lambda event: event.get("timestamp") or "")
    return _Payload(symbol=symbol, timeframe=request.timeframe, session=request.session, start=request.start, end=request.end, events=filtered[-request.limit :], event_count=len(filtered[-request.limit :]), total_event_count=len(filtered), unknowns=unknowns, sources=sources, provider="MarketLens timeline", conclusion={"status": "verified_timeline" if filtered else "insufficient_events", "message": "Events retain source/provider metadata and are normalized to America/New_York."})


def anomaly_analysis_tool(request: AnomalyAnalysisRequest) -> BaseModel:
    """Detect unusual observations against explicit local baselines."""
    symbol = request.symbol.upper()
    anomalies: list[dict[str, Any]] = []
    corroborating: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    baseline: dict[str, Any] = {"window_bars": request.baseline_bars, "z_threshold": request.z_threshold}
    closes: list[float] = []
    try:
        bars_payload = get_bars_tool(BarsRequest(symbol=symbol, timeframe=request.timeframe, range=request.range, limit=2_000)).model_dump(mode="json")
        bars = bars_payload.get("bars", [])
        closes = [float(bar["close"]) for bar in bars if bar.get("close") is not None]
        volumes = [float(bar.get("volume", 0) or 0) for bar in bars]
        if len(closes) < request.baseline_bars + 2:
            unknowns.append({"type": "price_volume", "reason": "insufficient bars for requested baseline"})
        else:
            returns = [(closes[index] - closes[index - 1]) / abs(closes[index - 1]) * 100 for index in range(1, len(closes)) if closes[index - 1]]
            baseline_returns = returns[-request.baseline_bars - 1 : -1]
            latest_return = returns[-1]
            mean_return = sum(baseline_returns) / len(baseline_returns)
            return_std = stdev(baseline_returns) if len(baseline_returns) > 1 else 0.0
            return_z = (latest_return - mean_return) / return_std if return_std else 0.0
            baseline["return_percent"] = {"mean": mean_return, "stddev": return_std, "latest": latest_return, "z_score": return_z}
            if abs(return_z) >= request.z_threshold:
                anomalies.append({"type": "price_return", "severity": "high" if abs(return_z) >= request.z_threshold * 1.5 else "elevated", "value": latest_return, "z_score": return_z, "baseline": {"mean": mean_return, "stddev": return_std}, "direction": "up" if latest_return > 0 else "down"})
            baseline_volumes = volumes[-request.baseline_bars - 1 : -1]
            mean_volume = sum(baseline_volumes) / len(baseline_volumes)
            volume_std = stdev(baseline_volumes) if len(baseline_volumes) > 1 else 0.0
            latest_volume = volumes[-1]
            volume_z = (latest_volume - mean_volume) / volume_std if volume_std else 0.0
            baseline["volume"] = {"mean": mean_volume, "stddev": volume_std, "latest": latest_volume, "z_score": volume_z}
            if abs(volume_z) >= request.z_threshold:
                anomalies.append({"type": "volume", "severity": "high" if volume_z >= request.z_threshold * 1.5 else "elevated", "value": latest_volume, "z_score": volume_z, "baseline": {"mean": mean_volume, "stddev": volume_std}})
        sources.append({"name": "bars", "provider": bars_payload.get("provider"), "timestamp": bars_payload.get("source_timestamp")})
    except Exception as exc:
        unknowns.append({"type": "price_volume", "reason": str(exc)})

    try:
        quote = get_quote_tool(SymbolRequest(symbol=symbol)).model_dump(mode="json")
        bid, ask, price = quote.get("bid"), quote.get("ask"), quote.get("price")
        spread_bps = (float(ask) - float(bid)) / ((float(ask) + float(bid)) / 2) * 10_000 if bid is not None and ask is not None and float(bid) > 0 and float(ask) >= float(bid) else None
        baseline["spread_bps"] = spread_bps
        if spread_bps is not None and spread_bps >= request.spread_threshold_bps:
            anomalies.append({"type": "spread", "severity": "elevated", "value_bps": spread_bps, "threshold_bps": request.spread_threshold_bps, "price": price})
        sources.append({"name": "quote", "provider": quote.get("provider"), "timestamp": quote.get("timestamp")})
    except Exception as exc:
        unknowns.append({"type": "spread", "reason": str(exc)})

    if request.include_tape:
        try:
            tape = get_tape_state_tool(TapeRequest(symbol=symbol)).model_dump(mode="json")
            snapshot = tape.get("snapshot") or {}
            large_prints = snapshot.get("large_prints") or snapshot.get("large_trades")
            if large_prints:
                anomalies.append({"type": "large_prints", "severity": "elevated", "value": large_prints, "source": "tape"})
            imbalance = snapshot.get("imbalance") or snapshot.get("bid_ask_imbalance")
            if isinstance(imbalance, (int, float)) and abs(imbalance) >= 0.7:
                anomalies.append({"type": "tape_imbalance", "severity": "elevated", "value": imbalance, "source": "tape"})
            pressure = snapshot.get("pressure") or snapshot.get("tape_pressure")
            if pressure:
                corroborating.append({"type": "tape_pressure", "value": pressure})
            sources.append({"name": "tape", "provider": tape.get("provider"), "timestamp": tape.get("source_timestamp")})
        except Exception as exc:
            unknowns.append({"type": "tape", "reason": str(exc)})

    if request.include_options:
        try:
            options = get_options_tool(OptionsRequest(symbol=symbol)).model_dump(mode="json")
            unusual = [chain for chain in options.get("chains", []) if str(chain.get("unusual_activity", "normal")).lower() not in {"normal", "none"}]
            if unusual:
                anomalies.append({"type": "options_activity", "severity": "elevated", "value": unusual, "source": "options"})
            sources.append({"name": "options", "provider": options.get("provider"), "timestamp": options.get("source_timestamp")})
        except Exception as exc:
            unknowns.append({"type": "options", "reason": str(exc)})

    if request.benchmark_symbol:
        benchmark = request.benchmark_symbol.upper()
        try:
            benchmark_payload = get_bars_tool(BarsRequest(symbol=benchmark, timeframe=request.timeframe, range=request.range, limit=2_000)).model_dump(mode="json")
            benchmark_closes = [float(bar["close"]) for bar in benchmark_payload.get("bars", []) if bar.get("close") is not None]
            symbol_returns = [(closes[index] / closes[index - 1]) - 1 for index in range(1, len(closes))]
            benchmark_returns = [(benchmark_closes[index] / benchmark_closes[index - 1]) - 1 for index in range(1, len(benchmark_closes))]
            length = min(len(symbol_returns), len(benchmark_returns), request.baseline_bars)
            left, right = symbol_returns[-length:], benchmark_returns[-length:]
            if length >= 3:
                left_mean, right_mean = sum(left) / length, sum(right) / length
                covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
                left_dev = math.sqrt(sum((a - left_mean) ** 2 for a in left))
                right_dev = math.sqrt(sum((b - right_mean) ** 2 for b in right))
                correlation = covariance / (left_dev * right_dev) if left_dev and right_dev else None
                baseline["correlation"] = {"benchmark": benchmark, "value": correlation, "sample_size": length}
                if correlation is not None and abs(correlation) < 0.2:
                    anomalies.append({"type": "correlation_break", "severity": "elevated", "value": correlation, "benchmark": benchmark, "reason": "recent returns have weak correlation to the selected benchmark"})
            sources.append({"name": "benchmark", "symbol": benchmark, "provider": benchmark_payload.get("provider"), "timestamp": benchmark_payload.get("source_timestamp")})
        except Exception as exc:
            unknowns.append({"type": "correlation", "reason": str(exc)})

    if request.positions:
        gross = sum((position.current_price or position.entry_price) * position.quantity for position in request.positions)
        for position in request.positions:
            value = (position.current_price or position.entry_price) * position.quantity
            weight = value / gross * 100 if gross else 0.0
            if weight >= request.portfolio_concentration_threshold:
                anomalies.append({"type": "portfolio_concentration", "severity": "elevated", "symbol": position.symbol.upper(), "weight_percent": weight, "threshold_percent": request.portfolio_concentration_threshold})
        baseline["portfolio_gross_exposure"] = gross
    else:
        unknowns.append({"type": "portfolio_risk", "reason": "no position snapshot supplied"})

    return _Payload(symbol=symbol, anomalies=anomalies, anomaly_count=len(anomalies), baseline=baseline, corroborating_evidence=corroborating, unknowns=unknowns, sources=sources, provider="MarketLens anomaly analysis", conclusion={"status": "verified_anomalies" if anomalies else "no_anomaly_detected", "message": "Anomalies are deviations from the supplied baseline, not predictions."})


def _assumption_values_differ(expected: Any, observed: Any) -> bool:
    if expected is None or observed is None:
        return False
    if isinstance(expected, (int, float)) and isinstance(observed, (int, float)):
        return not math.isclose(float(expected), float(observed), rel_tol=0.05, abs_tol=1e-9)
    return str(expected).strip().casefold() != str(observed).strip().casefold()


def _assumption_age_hours(created_at: str, now: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (now - parsed.astimezone(UTC)).total_seconds() / 3_600)
    except (TypeError, ValueError):
        return None


def assumption_tracking_tool(request: AssumptionTrackingRequest) -> BaseModel:
    """Save and verify user-owned research assumptions without rewriting history.

    Persistence is intentionally delegated to the chat session's structured
    planner state.  The tool itself accepts an explicit snapshot, making it
    safe to call through the registry, easy to test, and independent of a
    second database table.  A later review can only change status metadata
    (active/stale/broken); ``original_*`` fields remain immutable.
    """
    now = datetime.now(UTC)
    records = [dict(item) for item in request.existing_assumptions]
    by_id = {str(item.get("id")): item for item in records if item.get("id")}
    saved_ids: list[str] = []

    if request.operation == "save":
        if not request.assumptions:
            raise ValueError("At least one assumption is required when operation is save")
        for item in request.assumptions:
            symbol = (item.symbol or request.symbol or "").upper() or None
            duplicate = next(
                (
                    existing
                    for existing in records
                    if str(existing.get("symbol") or "").upper() == (symbol or "")
                    and existing.get("category") == item.category
                    and str(existing.get("statement", "")).strip().casefold() == item.statement.strip().casefold()
                    and existing.get("status") not in {"broken"}
                ),
                None,
            )
            if duplicate is not None:
                saved_ids.append(str(duplicate.get("id")))
                continue
            record_id = item.id or f"assumption-{len(records) + 1:04d}"
            # Avoid an accidental collision with a caller-supplied id while
            # retaining deterministic ids for reproducible API responses.
            while record_id in by_id:
                record_id = f"{record_id}-copy"
            created = now.isoformat()
            record = {
                "id": record_id,
                "symbol": symbol,
                "category": item.category,
                "statement": item.statement,
                "expected_value": item.expected_value,
                "unit": item.unit,
                "source": item.source,
                "created_at": created,
                "status": "active",
                "status_reason": "User-approved assumption has not been contradicted.",
                "stale_after_hours": item.stale_after_hours,
                "original_statement": item.statement,
                "original_value": item.expected_value,
                "original_source": item.source,
                "original_created_at": created,
                "last_verified_at": None,
                "last_evidence": None,
            }
            records.append(record)
            by_id[record_id] = record
            saved_ids.append(record_id)

    changed: list[dict[str, Any]] = []
    evidence_by_id = {item.assumption_id: item for item in request.evidence}
    for record in records:
        record_id = str(record.get("id") or "")
        evidence = evidence_by_id.get(record_id)
        previous_status = str(record.get("status") or "active")
        if evidence is not None:
            observed_at = evidence.observed_at or now.isoformat()
            record["last_verified_at"] = observed_at
            record["last_evidence"] = {
                "observed_value": evidence.observed_value,
                "source": evidence.source,
                "observed_at": observed_at,
                "note": evidence.note,
            }
            contradicted = evidence.contradicts or _assumption_values_differ(
                record.get("original_value"), evidence.observed_value
            )
            record["status"] = "broken" if contradicted else "active"
            record["status_reason"] = (
                evidence.note or f"Verified evidence from {evidence.source} changed the assumption."
                if contradicted
                else f"Verified against {evidence.source}; no contradiction found."
            )
        elif previous_status == "active":
            age_hours = _assumption_age_hours(str(record.get("created_at") or ""), now)
            stale_after = float(record.get("stale_after_hours") or 24.0)
            if age_hours is not None and age_hours >= stale_after:
                record["status"] = "stale"
                record["status_reason"] = "No newer verified evidence is attached to this assumption."
        if record.get("status") != previous_status:
            changed.append({"id": record_id, "from": previous_status, "to": record.get("status"), "reason": record.get("status_reason")})

    stale_count = sum(1 for item in records if item.get("status") == "stale")
    broken_count = sum(1 for item in records if item.get("status") == "broken")
    return _Payload(
        operation=request.operation,
        symbol=(request.symbol or "").upper() or None,
        assumptions=records,
        saved_ids=saved_ids,
        changed=changed,
        active_count=sum(1 for item in records if item.get("status") == "active"),
        stale_count=stale_count,
        broken_count=broken_count,
        provider="MarketLens assumption ledger",
        source_timestamp=now.isoformat(),
        conclusion={
            "status": "assumptions_saved" if request.operation == "save" else "assumptions_reviewed",
            "message": "Original assumptions are preserved; only verification status and evidence metadata may change.",
        },
    )


def _database_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def get_watchlist_tool(request: WatchlistRequest) -> BaseModel:
    """Read a named or identified watchlist from the application database."""
    from backend.database import SessionLocal
    from backend.repositories.watchlist_repository import WatchlistRepository

    db = SessionLocal()
    try:
        repository = WatchlistRepository(db)
        if request.watchlist_id is not None:
            watchlist = repository.get_watchlist(request.watchlist_id)
        elif request.name:
            watchlist = repository.get_watchlist_by_name(request.name.strip())
        else:
            watchlists = repository.get_watchlists(active_only=True)
            return _Payload(
                watchlists=[{"id": item.id, "name": item.name, "description": item.description} for item in watchlists],
                provider="MarketLens database",
                source_timestamp=_database_timestamp(),
            )
        if watchlist is None or not watchlist.is_active:
            raise ValueError("Watchlist was not found or is inactive")
        symbols = repository.get_watchlist_symbols(
            watchlist.id,
            enabled_only=not request.include_disabled,
        )
        return _Payload(
            id=watchlist.id,
            name=watchlist.name,
            description=watchlist.description,
            symbols=[
                {"symbol": item.symbol, "position": item.position, "is_enabled": item.is_enabled,
                 "notes": item.notes, "entity_type": item.entity_type}
                for item in symbols
            ],
            provider="MarketLens database",
            source_timestamp=_database_timestamp(),
        )
    finally:
        db.close()


def get_alerts_tool(request: AlertsRequest) -> BaseModel:
    """Read alert rules (and optionally their recent triggers) from the application database."""
    from backend.database import SessionLocal
    from backend.repositories.alert_repository import AlertRepository
    from backend.utils.timezone import format_edt_iso

    db = SessionLocal()
    try:
        repository = AlertRepository(db)
        if request.symbol:
            alerts = (
                repository.get_enabled_for_symbol(request.symbol)
                if request.enabled_only
                else repository.get_for_symbol(request.symbol)
            )
        else:
            alerts = repository.get_all_enabled() if request.enabled_only else repository.get_all()
        rows = []
        for alert in alerts:
            row = {
                "id": alert.id,
                "name": alert.name,
                "symbol": alert.symbol,
                "condition_type": alert.condition_type,
                "parameter": alert.parameter,
                "is_enabled": alert.is_enabled,
                "created_at": format_edt_iso(alert.created_at),
                "updated_at": format_edt_iso(alert.updated_at),
            }
            if request.include_recent_triggers:
                row["recent_triggers"] = [
                    {
                        "triggered_at": format_edt_iso(trigger.triggered_at),
                        "observed_value": trigger.observed_value,
                        "message": trigger.message,
                    }
                    for trigger in repository.get_triggers(alert.id, limit=request.trigger_limit)
                ]
            rows.append(row)
        return _Payload(
            alerts=rows,
            provider="MarketLens database",
            source_timestamp=_database_timestamp(),
        )
    finally:
        db.close()


def get_risk_dashboard_tool(request: RiskDashboardRequest) -> BaseModel:
    """Summarize an explicitly supplied manual-position snapshot.

    The UI stores positions in browser localStorage, which the backend cannot
    access. Requiring the snapshot in the request keeps answers honest and
    makes all arithmetic deterministic.
    """
    if not request.positions:
        return _Payload(
            available=False,
            reason="No server-side positions are configured. The Risk Dashboard stores manual positions in this browser; pass a position snapshot to calculate risk.",
            positions=[],
            provider="MarketLens local dashboard",
            source_timestamp=_database_timestamp(),
        )
    gross = 0.0
    net = 0.0
    stop_risk = 0.0
    rows = []
    sectors: dict[str, float] = {}
    for position in request.positions:
        price = position.current_price or position.entry_price
        value = price * position.quantity
        signed = value if position.side == "long" else -value
        risk = None
        if position.stop_price is not None:
            risk = max(0.0, (position.entry_price - position.stop_price) * position.quantity * (1 if position.side == "long" else -1))
            stop_risk += risk
        gross += value
        net += signed
        sector = position.sector or "Unknown"
        sectors[sector] = sectors.get(sector, 0.0) + value
        rows.append({"symbol": position.symbol.upper(), "side": position.side, "quantity": position.quantity,
                     "current_price": price, "market_value": value, "stop_risk": risk,
                     "pnl": (price - position.entry_price) * position.quantity * (1 if position.side == "long" else -1),
                     "sector": sector})
    for row in rows:
        row["weight_percent"] = round(row["market_value"] / gross * 100, 6) if gross else 0.0
    return _Payload(
        available=True,
        positions=rows,
        gross_exposure=round(gross, 8),
        net_exposure=round(net, 8),
        stop_loss_risk=round(stop_risk, 8),
        sector_exposure=[{"sector": key, "market_value": round(value, 8), "weight_percent": round(value / gross * 100, 6) if gross else 0.0} for key, value in sorted(sectors.items(), key=lambda item: item[1], reverse=True)],
        provider="MarketLens calculator",
        source_timestamp=_database_timestamp(),
    )


def get_trade_journal_tool(request: TradeJournalRequest) -> BaseModel:
    """Summarize an explicitly supplied browser-local journal snapshot."""
    entries = request.entries
    if request.symbol:
        entries = [entry for entry in entries if str(entry.get("symbol", "")).upper() == request.symbol.upper()]
    if request.status:
        entries = [entry for entry in entries if entry.get("status") == request.status]
    closed = [entry for entry in entries if entry.get("status") == "closed"]
    return _Payload(
        available=bool(request.entries),
        reason=None if request.entries else "Trade Journal entries are stored in this browser. Pass the local journal snapshot to search or summarize it.",
        entries=entries,
        total_entries=len(entries),
        closed_entries=len(closed),
        provider="MarketLens local journal",
        source_timestamp=_database_timestamp(),
    )


_CSV_MAX_ROWS = 500
_CSV_MAX_COLUMNS = 40
_CSV_MAX_CELL_LENGTH = 500

_CSV_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "positions": ("symbol", "quantity", "entry_price"),
    "watchlist": ("symbol",),
    "trade_journal": ("symbol",),
}


def _parse_csv_rows(csv_content: str, *, has_header: bool) -> tuple[list[str], list[dict[str, str]], list[str]]:
    """Parse CSV text into header + row dicts, all values as plain strings.

    Uses the stdlib ``csv`` module only — every cell is returned as inert
    text, never evaluated. There is no code path here (or anywhere else in
    this function) that opens the content in a spreadsheet engine, so a
    leading ``=``/``+``/``-``/``@`` (the classic CSV-formula-injection
    trigger for tools like Excel) is just a string prefix, not something
    this parser or any caller can execute.
    """
    import csv
    import io

    reader = csv.reader(io.StringIO(csv_content))
    raw_rows = list(reader)
    errors: list[str] = []

    if not raw_rows:
        return [], [], ["CSV content is empty"]

    if has_header:
        header = [cell.strip() for cell in raw_rows[0]]
        data_rows = raw_rows[1:]
    else:
        header = [f"column_{i + 1}" for i in range(len(raw_rows[0]))]
        data_rows = raw_rows

    if len(header) > _CSV_MAX_COLUMNS:
        raise ValueError(f"CSV has {len(header)} columns; at most {_CSV_MAX_COLUMNS} are allowed")
    if len(data_rows) > _CSV_MAX_ROWS:
        raise ValueError(f"CSV has {len(data_rows)} data rows; at most {_CSV_MAX_ROWS} are allowed")

    rows: list[dict[str, str]] = []
    for line_number, raw_row in enumerate(data_rows, start=2 if has_header else 1):
        if len(raw_row) != len(header):
            errors.append(f"Row {line_number}: expected {len(header)} columns, found {len(raw_row)} — skipped")
            continue
        row = {}
        for key, value in zip(header, raw_row, strict=True):
            value = value.strip()
            if len(value) > _CSV_MAX_CELL_LENGTH:
                errors.append(f"Row {line_number}, column '{key}': value exceeds {_CSV_MAX_CELL_LENGTH} characters — truncated")
                value = value[:_CSV_MAX_CELL_LENGTH]
            row[key] = value
        rows.append(row)
    return header, rows, errors


def import_csv_tool(request: CsvImportRequest) -> BaseModel:
    """Parse and validate local CSV text into the same structured shape
    get_risk_dashboard/get_watchlist-add/get_trade_journal already accept.

    Nothing is persisted here — this only parses and validates. The plan's
    "keep imported data local" requirement means MarketLens never uploads
    this content anywhere; it does not mean this tool silently writes to
    the database or a watchlist. A caller that wants the parsed positions
    on the Risk Dashboard, or the parsed symbols on a real watchlist, takes
    the validated rows this returns and passes them to the tool (or the
    existing app UI) that actually does that, the same explicit-snapshot
    pattern get_risk_dashboard_tool already uses.
    """
    provider = "MarketLens local parser"
    header, rows, errors = _parse_csv_rows(request.csv_content, has_header=request.has_header)
    if not rows and not errors:
        return _Payload(import_type=request.import_type, header=header, rows=[], row_count=0, errors=["No data rows found"], provider=provider)

    required = _CSV_REQUIRED_COLUMNS[request.import_type]
    missing = [column for column in required if header and column not in header]
    if missing:
        return _Payload(
            import_type=request.import_type,
            header=header,
            rows=[],
            row_count=0,
            errors=[f"Missing required column(s): {', '.join(missing)}"] + errors,
            provider=provider,
        )

    if request.import_type == "positions":
        parsed: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            try:
                position = PositionInput(
                    symbol=row["symbol"],
                    side=(row.get("side") or "long").strip().lower(),
                    quantity=float(row["quantity"]),
                    entry_price=float(row["entry_price"]),
                    stop_price=float(row["stop_price"]) if row.get("stop_price") else None,
                    current_price=float(row["current_price"]) if row.get("current_price") else None,
                    sector=row.get("sector") or None,
                )
                parsed.append(position.model_dump())
            except (ValueError, KeyError) as exc:
                errors.append(f"Row {index}: {exc}")
    elif request.import_type == "watchlist":
        parsed = []
        seen: set[str] = set()
        for index, row in enumerate(rows, start=1):
            symbol = row.get("symbol", "").upper()
            if not symbol:
                errors.append(f"Row {index}: empty symbol")
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            parsed.append({"symbol": symbol})
    else:  # trade_journal
        parsed = [dict(row) for row in rows]

    return _Payload(
        import_type=request.import_type,
        header=header,
        rows=parsed,
        row_count=len(parsed),
        errors=errors,
        provider=provider,
    )


# Search topics and required-navigation-state have no frontend counterpart
# to generate from — they're this tool's own domain (keyword matching,
# what Chat needs to pass along), not duplicated routing data, so there's
# no drift risk in keeping them hand-maintained here.
_APPLICATION_PAGE_METADATA: dict[str, dict] = {
    "dashboard": {"required_state": (), "topics": ("overview", "market", "movers", "latest prices")},
    "scanner": {"required_state": (), "topics": ("scan", "filters", "breakout", "oversold", "volume")},
    "symbol": {"required_state": ("symbol",), "topics": ("chart", "quote", "options", "catalyst", "signal", "support resistance")},
    "watchlist": {"required_state": (), "topics": ("watchlist", "symbols", "session prices")},
    "hub": {"required_state": (), "topics": ("chat", "assistant", "ai", "questions")},
    "calendar": {"required_state": (), "topics": ("earnings", "calendar", "events", "dividend")},
    "risk": {"required_state": (), "topics": ("risk", "positions", "exposure", "drawdown", "correlation", "stop")},
    "journal": {"required_state": (), "topics": ("journal", "thesis", "trade", "review", "screenshot")},
    "alerts": {"required_state": (), "topics": ("alert", "notifications", "price", "volume", "news")},
    "backtest": {"required_state": (), "topics": ("backtest", "historical", "performance", "replay")},
    "signals": {"required_state": ("symbol",), "topics": ("replay", "historical", "signals", "candles")},
    "health": {"required_state": (), "topics": ("health", "redis", "provider", "database", "status")},
}

# Safety net only — used when the frontend source files can't be read or
# parsed (e.g. a backend-only deployment without frontend/ checked out).
# This is exactly the pair of route/title mistakes a hand-maintained table
# already produced in practice: "signals" pointed at the legacy
# #historical-replay alias instead of the canonical #signals hash, and
# "Historical Replay" instead of App.tsx's actual title, "Historical
# Signals". Kept here as a last resort, not the source of truth.
_FALLBACK_ROUTES: dict[str, str] = {
    "dashboard": "#dashboard", "scanner": "#scanner", "symbol": "#symbol",
    "watchlist": "#watchlist", "hub": "#ai-hub", "calendar": "#calendar",
    "risk": "#risk", "journal": "#journal", "alerts": "#alerts",
    "backtest": "#backtest", "signals": "#signals", "health": "#system-health",
}
_FALLBACK_TITLES: dict[str, str] = {
    "dashboard": "Dashboard", "scanner": "Scanner", "symbol": "Symbol",
    "watchlist": "Watchlist", "hub": "AI Hub", "calendar": "Earnings & Events",
    "risk": "Risk Dashboard", "journal": "Trade Journal", "alerts": "Alerts",
    "backtest": "Backtest", "signals": "Historical Signals", "health": "System Health",
}


def _frontend_src_root() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "src"


def _parse_frontend_hash_by_page() -> dict[str, str] | None:
    """Parse HASH_BY_PAGE straight out of appNavigation.ts — the frontend's
    actual routing source of truth (what hashForPage(page) returns, i.e.
    what window.location.hash is set to on navigation). Python can't
    import TypeScript, so this reads the file as text; returns None (never
    a partial/empty result) if the file is missing or the shape changed
    enough that the regex no longer matches, so callers can fall back
    instead of silently using an empty route table.
    """
    try:
        text = (_frontend_src_root() / "utils" / "appNavigation.ts").read_text()
    except OSError:
        return None
    block = re.search(r"const HASH_BY_PAGE:.*?=\s*\{(.*?)\};", text, re.DOTALL)
    if not block:
        return None
    pairs = re.findall(r"(\w+):\s*'([^']+)'", block.group(1))
    return dict(pairs) if pairs else None


def _parse_frontend_page_titles() -> dict[str, str] | None:
    """Parse each page's display title straight out of App.tsx's render
    switch — the `case 'X': return ...pageName="Y"...` pairs are the
    frontend's own canonical title per page (used for error-boundary
    labeling), not a value this tool invents independently.
    """
    try:
        text = (_frontend_src_root() / "App.tsx").read_text()
    except OSError:
        return None
    pairs = re.findall(r"case '(\w+)':\s*\n\s*return.*?pageName=\"([^\"]+)\"", text)
    return dict(pairs) if pairs else None


def _application_pages() -> tuple[dict, ...]:
    hash_by_page = _parse_frontend_hash_by_page()
    titles = _parse_frontend_page_titles()
    used_fallback_routes = hash_by_page is None
    used_fallback_titles = titles is None
    hash_by_page = hash_by_page or _FALLBACK_ROUTES
    titles = titles or _FALLBACK_TITLES

    pages = []
    for page_key, meta in _APPLICATION_PAGE_METADATA.items():
        route = hash_by_page.get(page_key)
        title = titles.get(page_key)
        if route is None or title is None:
            # The frontend no longer has this page (or renamed its key) —
            # don't fabricate a route/title for something that may not
            # exist any more.
            continue
        pages.append({
            "page": page_key,
            "title": title,
            "route": route,
            "required_state": meta["required_state"],
            "topics": meta["topics"],
            "source": "fallback_snapshot" if (used_fallback_routes or used_fallback_titles) else "frontend_parsed",
        })
    return tuple(pages)


def get_application_help_tool(request: ApplicationHelpRequest) -> BaseModel:
    application_pages = _application_pages()
    query = (request.query or "").strip().lower()
    if query:
        terms = {term for term in query.replace("?", " ").split() if len(term) > 1}
        ranked = sorted(
            application_pages,
            key=lambda page: sum(term in page["title"].lower() or any(term in topic for topic in page["topics"]) for term in terms),
            reverse=True,
        )
        matches = [page for page in ranked if any(term in page["title"].lower() or any(term in topic for topic in page["topics"]) for term in terms)]
    else:
        matches = list(application_pages)
    warnings = []
    if matches and matches[0]["source"] == "fallback_snapshot":
        warnings.append("Frontend route source unavailable; using a fallback route/title snapshot that may be stale.")
    return _Payload(
        query=request.query,
        matches=[
            {
                "page": page["page"],
                "title": page["title"],
                "route": page["route"],
                "required_state": list(page["required_state"]),
                "topics": list(page["topics"]),
            }
            for page in matches[: request.limit]
        ],
        provider="MarketLens application metadata",
        source_timestamp=_database_timestamp(),
        warnings=warnings,
    )
