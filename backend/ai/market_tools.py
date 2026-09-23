"""Read-only market-data tools for grounded AI answers."""

from __future__ import annotations

from datetime import UTC, datetime
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


class TapeRequest(BaseModel):
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


class TradeJournalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str | None = Field(default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    status: Literal["planned", "open", "closed"] | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list, max_length=1_000)


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
    return _Payload(
        symbol=request.symbol.upper(),
        timeframe=timeframe,
        session=normalize_session(request.session),
        bars=[bar.model_dump(mode="json") for bar in selected],
        provider=selected[-1].provider,
        source_timestamp=selected[-1].timestamp,
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


def get_market_regime_tool(request: SymbolRequest) -> BaseModel:
    from backend.api.regime.router import _data_age_seconds, _freshness, _to_dashboard_tz, get_engine

    symbol = request.symbol.upper()
    signal = get_engine(symbol).get_current_regime()
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
    )


def get_sector_data_tool(request: SymbolRequest) -> BaseModel:
    """Get the symbol's sector alignment vs. its sector ETF and SPY."""
    from backend.api.regime.router import _get_sector_engine

    engine = _get_sector_engine(request.symbol.upper())
    signal = engine.get_current_signal()
    return _Payload(**signal.to_dict())


def get_trend_tool(request: TrendRequest) -> BaseModel:
    """Get the current trend for a symbol on one timeframe.

    Reuses backend.api.trend.router's own payload builder (the same one
    GET /api/trend/{symbol}/current/{timeframe} uses) rather than
    re-deriving the signal shape here — see the get_market_regime_tool/
    get_market_context_tool incident for why that separation matters.
    """
    from backend.api.trend.registry import get_engine
    from backend.api.trend.router import _build_trend_payload
    from backend.ai.tool_registry import normalize_timeframe
    from backend.engines.timeframe import Timeframe

    symbol = request.symbol.upper()
    timeframe = normalize_timeframe(request.timeframe)
    tf = Timeframe(timeframe)
    engine = get_engine(symbol)
    payload = _build_trend_payload(engine, symbol, timeframe, tf)
    return _Payload(**payload)


def get_confluence_tool(request: ConfluenceRequest) -> BaseModel:
    """Get multi-timeframe confluence for a symbol under a trading-style preset."""
    from backend.api.multitimeframe.router import build_confluence_payload, get_engine

    symbol = request.symbol.upper()
    engine = get_engine(symbol, preset=request.preset)
    payload = build_confluence_payload(engine, symbol)
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
    return _Payload(symbol=symbol, snapshot=snapshot, source_timestamp=format_edt_iso(now_ny()))


def get_market_context_tool(_: BaseModel) -> BaseModel:
    from backend.api.market_context.router import _to_dashboard_tz, get_engine

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
        )
    payload = signal.to_dict()
    payload["timestamp"] = _to_dashboard_tz(signal.timestamp)
    return _Payload(**payload)


def _aux_manager():
    from backend.aux_data.services.manager import aux_data_manager

    return aux_data_manager


def get_news_tool(request: NewsRequest) -> BaseModel:
    response = _aux_manager().get_news(request.symbol.upper(), limit=request.limit)
    return _Payload(
        symbol=response.symbol,
        items=[item.model_dump(mode="json") for item in response.items],
        provider=response.provider,
        source_timestamp=response.timestamp,
    )


def get_fundamentals_tool(request: FundamentalsRequest) -> BaseModel:
    response = _aux_manager().get_fundamentals(request.symbol.upper())
    return _Payload(
        symbol=response.symbol,
        data=response.data.model_dump(mode="json"),
        provider=response.provider,
        source_timestamp=response.timestamp,
    )


def get_options_tool(request: OptionsRequest) -> BaseModel:
    response = _aux_manager().get_options(request.symbol.upper(), expiration=request.expiration)
    return _Payload(
        symbol=response.symbol,
        chains=[chain.model_dump(mode="json") for chain in response.chains],
        expirations=response.expirations,
        near_term_iv=response.near_term_iv,
        iv_rank=response.iv_rank,
        provider=response.provider,
        source_timestamp=response.timestamp,
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


# Kept in sync by hand with frontend/src/utils/appNavigation.ts's AppPage
# union and HASH_BY_PAGE map — that file is the actual routing source of
# truth (window.location.hash), and Python can't import TypeScript. "route"
# here must always be the value hashForPage(page) would produce there, not
# any legacy/alias hash the frontend's PAGE_BY_HASH also still accepts for
# incoming links (e.g. "signals" accepts both #signals and
# #historical-replay on the way in, but only ever navigates *to* #signals —
# that alias drift is exactly the kind of bug this table can silently grow
# without the ai/tests/ai/test_market_tools.py cross-check).
#
# "required_state" documents what the caller must additionally supply
# beyond the bare route — e.g. the Symbol page needs a symbol, since the
# frontend carries it as React state, not a URL query param. No app
# navigation action consumes this yet (that's Phase 5.7.7); today it is
# purely so a grounded Chat answer states requirements honestly instead of
# implying the bare hash alone is enough.
_APPLICATION_PAGES = (
    {"page": "dashboard", "title": "Dashboard", "route": "#dashboard", "required_state": (), "topics": ("overview", "market", "movers", "latest prices")},
    {"page": "scanner", "title": "Scanner", "route": "#scanner", "required_state": (), "topics": ("scan", "filters", "breakout", "oversold", "volume")},
    {"page": "symbol", "title": "Symbol", "route": "#symbol", "required_state": ("symbol",), "topics": ("chart", "quote", "options", "catalyst", "signal", "support resistance")},
    {"page": "watchlist", "title": "Watchlist", "route": "#watchlist", "required_state": (), "topics": ("watchlist", "symbols", "session prices")},
    {"page": "hub", "title": "AI Hub", "route": "#ai-hub", "required_state": (), "topics": ("chat", "assistant", "ai", "questions")},
    {"page": "calendar", "title": "Earnings & Events", "route": "#calendar", "required_state": (), "topics": ("earnings", "calendar", "events", "dividend")},
    {"page": "risk", "title": "Risk Dashboard", "route": "#risk", "required_state": (), "topics": ("risk", "positions", "exposure", "drawdown", "correlation", "stop")},
    {"page": "journal", "title": "Trade Journal", "route": "#journal", "required_state": (), "topics": ("journal", "thesis", "trade", "review", "screenshot")},
    {"page": "alerts", "title": "Alerts", "route": "#alerts", "required_state": (), "topics": ("alert", "notifications", "price", "volume", "news")},
    {"page": "backtest", "title": "Backtest", "route": "#backtest", "required_state": (), "topics": ("backtest", "historical", "performance", "replay")},
    {"page": "signals", "title": "Historical Replay", "route": "#signals", "required_state": ("symbol",), "topics": ("replay", "historical", "signals", "candles")},
    {"page": "health", "title": "System Health", "route": "#system-health", "required_state": (), "topics": ("health", "redis", "provider", "database", "status")},
)


def get_application_help_tool(request: ApplicationHelpRequest) -> BaseModel:
    query = (request.query or "").strip().lower()
    if query:
        terms = {term for term in query.replace("?", " ").split() if len(term) > 1}
        ranked = sorted(
            _APPLICATION_PAGES,
            key=lambda page: sum(term in page["title"].lower() or any(term in topic for topic in page["topics"]) for term in terms),
            reverse=True,
        )
        matches = [page for page in ranked if any(term in page["title"].lower() or any(term in topic for topic in page["topics"]) for term in terms)]
    else:
        matches = list(_APPLICATION_PAGES)
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
    )
