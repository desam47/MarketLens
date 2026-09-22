"""Read-only market-data tools for grounded AI answers."""

from __future__ import annotations

from typing import Literal

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


def _manager():
    from backend.market_data.services.manager import market_data_manager

    return market_data_manager


def get_quote_tool(request: SymbolRequest) -> BaseModel:
    from backend.ai.tool_registry import normalize_session

    quote = _manager().get_quote(request.symbol.upper())
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
    from backend.api.regime.router import get_engine

    signal = get_engine(request.symbol.upper()).get_current_regime()
    if signal is None:
        raise ValueError(f"Market regime is not warmed for {request.symbol.upper()}")
    return _Payload(symbol=request.symbol.upper(), **signal.model_dump(mode="json"))


def get_market_context_tool(_: BaseModel) -> BaseModel:
    from backend.api.market_context.router import _engine

    if _engine is None:
        raise ValueError("Market context is not warmed")
    signal = _engine.get_current_context()
    if signal is None:
        raise ValueError("Market context is not available")
    return _Payload(**signal.model_dump(mode="json"))
