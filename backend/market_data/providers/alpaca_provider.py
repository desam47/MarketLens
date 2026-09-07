"""
Alpaca market data provider (v3.6).

Implements the ``MarketDataProvider`` ABC using the official ``alpaca-py``
SDK (REST + WebSocket). REST methods route through
``alpaca.data.historical.StockHistoricalDataClient``; the market clock is
fetched from ``alpaca.trading.TradingClient`` (clock is a trading-API
endpoint, not a data-API endpoint). Real-time bar streaming uses
``alpaca.data.live.StockDataStream``.

**v3.6 scope:**
  - REST market data (quotes, bars, market status, batch quotes)
  - WebSocket streaming for live bars (integrated with ws_router broadcast)
  - Paper/live endpoint switching (paper: paper-api/paper-data; live: api/data)

**Provider chain position:** Registered as ``alpaca``; falls back after
yfinance and webull in the default chain. Enable via ``ALPACA_ENABLED=true``
with valid ``ALPACA_API_KEY`` and ``ALPACA_SECRET_KEY``.

**Rate limits:**
  - Free tier (IEX): 200 req/min REST
  - Paid tier (SIP): higher limits
  - WebSocket: max 200 symbols per connection, max 5 connections
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient

from backend.config.settings import settings as _settings
from backend.models.market_data import (
    Bar,
    DataStatus,
    MarketStatus,
    ProviderCapabilities,
    ProviderStatus,
    Quote,
)
from backend.utils.timezone import NY, UTC, to_ny, ny_to_utc

from ..provider import BaseMarketDataProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timeframe / range mapping
# ---------------------------------------------------------------------------

# Our timeframe → SDK ``TimeFrame`` object. 4h/2m fall back to nearest.
_TF_MAP: dict[str, TimeFrame] = {
    "1m": TimeFrame(1, TimeFrameUnit.Minute),
    "2m": TimeFrame(1, TimeFrameUnit.Minute),  # nearest
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "30m": TimeFrame(30, TimeFrameUnit.Minute),
    "60m": TimeFrame(1, TimeFrameUnit.Hour),
    "1h": TimeFrame(1, TimeFrameUnit.Hour),
    "4h": TimeFrame(1, TimeFrameUnit.Hour),  # nearest
    "1d": TimeFrame(1, TimeFrameUnit.Day),
    "1wk": TimeFrame(1, TimeFrameUnit.Week),
}

# Range string → seconds. Used by ``get_historical_bars`` to derive ``start``.
_RANGE_SECONDS: dict[str, int] = {
    "1d": 86400,
    "5d": 432000,
    "15d": 1296000,  # Phase 3.9: 15 trading days
    "1mo": 2592000,
    "3mo": 7776000,
    "6mo": 15552000,
    "1y": 31536000,
    "2y": 63072000,
    "5y": 157680000,
    "15m": 2700,    # Phase 3.8: 45 min — covers 15-min lookback + buffer
    "3h":  18000,   # Phase 3.8: 5 hours — covers 3h lookback + buffer
}


def _resolve_tf(timeframe: str) -> TimeFrame:
    tf = _TF_MAP.get(timeframe)
    if tf is None:
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}; supported: {sorted(_TF_MAP)}"
        )
    return tf


def _resolve_feed(data_tier: str) -> DataFeed:
    """Map our ``ALPACA_DATA_TIER`` setting to the SDK ``DataFeed`` enum."""
    t = (data_tier or "iex").lower()
    if t == "sip":
        return DataFeed.SIP
    if t == "delayed_sip":
        return DataFeed.DELAYED_SIP
    return DataFeed.IEX


def _ts_to_ny(dt: datetime) -> datetime:
    """Return ``dt`` as a naive America/New_York datetime (EDT/EST).

    All timestamps are stored in NY local time. Naive inputs are assumed UTC
    (our historical convention). Aware inputs are converted via ZoneInfo.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(NY).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# AlpacaWebSocket client (wraps StockDataStream for our lifecycle)
# ---------------------------------------------------------------------------


class AlpacaWebSocketClient:
    """Lifecycle wrapper around ``alpaca.data.live.StockDataStream``.

    The SDK's ``StockDataStream`` is an async context manager: ``run()`` is
    a coroutine that opens the WebSocket, dispatches subscribed handlers,
    and only returns when ``stop()`` is called. This wrapper exposes a
    synchronous, thread-safe ``subscribe/unsubscribe/close`` API that the
    provider (``AlpacaProvider``) can use without owning the event loop.

    The stream runs in a background ``asyncio`` thread; bar events are
    forwarded to the ``on_bar`` callback on the calling thread (via
    ``call_soon_threadsafe``).
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        data_tier: str,
        paper: bool,
        on_bar: Any,
        on_status_change: Any,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._data_tier = data_tier
        self._paper = paper
        self._on_bar = on_bar
        self._on_status_change = on_status_change

        self._lock = threading.Lock()
        self._closed = False
        self._subscribed: set[tuple[str, str]] = set()  # {(symbol, timeframe)}
        self._ws_connected = False
        self._thread: threading.Thread | None = None
        self._stream: StockDataStream | None = None

    # ---- public state -----------------------------------------------------

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._ws_connected

    @property
    def _subs(self) -> set[tuple[str, str]]:
        with self._lock:
            return set(self._subscribed)

    # ---- SDK stream factory ----------------------------------------------

    def _build_stream(self) -> StockDataStream:
        feed = _resolve_feed(self._data_tier)
        # StockDataStream derives the right URL from (api_key, secret_key, feed)
        # when the keys match a paper account. We just pass the feed through.
        stream = StockDataStream(
            api_key=self._api_key,
            secret_key=self._secret_key,
            feed=feed,
        )
        # Register the bar handler. The SDK calls this for every bar update.
        stream.subscribe_bars(self._handle_bar_event)
        return stream

    # ---- bar handler (runs in SDK's asyncio thread) ----------------------

    async def _handle_bar_event(self, bar: Any) -> None:
        """Forward a SDK ``Bar`` to the synchronous ``on_bar`` callback.

        ``bar`` is an ``alpaca.data.models.Bar`` instance. We translate
        it to our internal ``Bar`` model and invoke the user callback.
        """
        try:
            our_bar = Bar(
                symbol=str(bar.symbol).upper(),
                timestamp=_ts_to_ny(bar.timestamp),
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=int(bar.volume or 0),
                timeframe="1min",  # WS bars are 1-minute
                provider="alpaca",
                data_status=DataStatus.DELAYED,
            )
            self._on_bar(our_bar.symbol, our_bar)
        except Exception:
            logger.exception("Error handling Alpaca WS bar")

    # ---- background thread management ------------------------------------

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._closed = False
            self._stream = self._build_stream()
            self._thread = threading.Thread(
                target=self._run_stream_in_thread,
                name="alpaca-ws",
                daemon=True,
            )
            self._thread.start()

    def _run_stream_in_thread(self) -> None:
        """Run the SDK stream's event loop until ``close()`` is called.

        The SDK's ``StockDataStream.run()`` is a *blocking* method (it calls
        ``asyncio.run`` internally), so we just call it directly on the
        background thread. The SDK's ``stop()`` is also synchronous — it
        signals the event loop to exit.
        """
        try:
            with self._lock:
                self._ws_connected = True
            self._on_status_change("connected")
            self._stream.run()
        except Exception:
            logger.exception("Alpaca WS stream terminated")
        finally:
            with self._lock:
                self._ws_connected = False
            try:
                self._on_status_change("disconnected")
            except Exception:
                pass

    # ---- public subscribe / unsubscribe / close -------------------------

    def subscribe(self, symbol: str, timeframe: str = "1m") -> None:
        key = (symbol.upper(), timeframe)
        with self._lock:
            if key in self._subscribed:
                return
            self._subscribed.add(key)

        if not self._api_key or not self._secret_key:
            return  # Not configured; skip.

        # Lazy-start the stream on first subscription.
        self._ensure_thread()

    def unsubscribe(self, symbol: str, timeframe: str = "1m") -> None:
        key = (symbol.upper(), timeframe)
        with self._lock:
            self._subscribed.discard(key)

        # The SDK has no per-symbol unsubscribe on the streaming side without
        # rebuilding the handler. For our purposes (provider-level
        # subscribe/unsubscribe) we keep the stream open and just remove the
        # symbol from our internal set; the bar handler is a no-op for
        # symbols that aren't in the ws_router's active set.
        stream = self._stream
        if stream is not None:
            try:
                stream.unsubscribe_bars(key[0])
            except Exception:
                # unsubscribing a non-subscribed symbol is a no-op in the SDK.
                pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        stream = self._stream
        if stream is not None:
            try:
                # ``stop`` is a synchronous method on the SDK stream that
                # signals its internal event loop to exit ``run()``.
                stream.stop()
            except Exception:
                logger.exception("Error stopping Alpaca WS stream")
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        with self._lock:
            self._ws_connected = False
        try:
            self._on_status_change("disconnected")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AlpacaProvider
# ---------------------------------------------------------------------------


class AlpacaProvider(BaseMarketDataProvider):
    """Alpaca REST + WebSocket market data provider (backed by ``alpaca-py``).

    REST methods delegate to the SDK's ``StockHistoricalDataClient`` (data
    API) and ``TradingClient`` (clock). WebSocket streaming is delegated to
    the SDK's ``StockDataStream`` via :class:`AlpacaWebSocketClient`.
    """

    def __init__(self) -> None:
        super().__init__("alpaca")
        self._cfg = _settings.alpaca
        self._api_key = self._cfg.api_key
        self._secret_key = self._cfg.secret_key
        self._paper = self._cfg.paper
        self._data_tier = self._cfg.data_tier
        self._timeout = self._cfg.request_timeout

        # SDK clients (lazy so unit tests can mock them).
        self._data_client: StockHistoricalDataClient | None = None
        self._trading_client: TradingClient | None = None
        self._ws_client: AlpacaWebSocketClient | None = None
        self._ws_status: str = "disconnected"  # "connected" | "disconnected" | "error"
        self._ws_lock = threading.Lock()

    # ---------------------------------------------------------------- SDK clients

    def _get_data_client(self) -> StockHistoricalDataClient:
        if self._data_client is None:
            self._data_client = StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
        return self._data_client

    def _get_trading_client(self) -> TradingClient:
        if self._trading_client is None:
            self._trading_client = TradingClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
                paper=self._paper,
            )
        return self._trading_client

    def _get_ws_client(self) -> AlpacaWebSocketClient:
        with self._ws_lock:
            if self._ws_client is None:
                self._ws_client = AlpacaWebSocketClient(
                    api_key=self._api_key,
                    secret_key=self._secret_key,
                    data_tier=self._data_tier,
                    paper=self._paper,
                    on_bar=self._on_ws_bar,
                    on_status_change=self._on_ws_status_change,
                )
            return self._ws_client

    # ---------------------------------------------------------------- ws hooks

    def _on_ws_bar(self, symbol: str, bar: Bar) -> None:
        """Called by the WebSocket client when a live bar arrives."""
        try:
            from backend.api.realtime.ws_router import _broadcast_bar

            _broadcast_bar(bar, symbol, "1min")
        except Exception:
            # ws_router may not be imported yet; skip broadcast silently.
            pass

    def _on_ws_status_change(self, status: str) -> None:
        with self._ws_lock:
            self._ws_status = status
        logger.info("Alpaca WebSocket status: %s", status)

    # ---------------------------------------------------------------- conversion

    def _bar_from_sdk(self, symbol: str, bar: Any, timeframe: str) -> Bar:
        """Convert an ``alpaca.data.models.Bar`` to our internal ``Bar`` model."""
        return Bar(
            symbol=symbol.upper(),
            timestamp=_ts_to_ny(bar.timestamp),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=int(bar.volume or 0),
            timeframe=timeframe,
            provider=self.name,
            data_status=DataStatus.HISTORICAL,
        )

    # ---------------------------------------------------------------- quote

    def get_quote(self, symbol: str) -> Quote:
        """Get latest quote via the SDK's ``StockHistoricalDataClient``."""
        try:
            client = self._get_data_client()
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol.upper())
            quotes = client.get_stock_latest_quote(req)
            sdk_quote = quotes.get(symbol.upper())
            if sdk_quote is None:
                raise ValueError(f"No quote data for {symbol}")

            # One side (bid or ask) is often 0 in off-hours quotes; prefer the
            # non-zero side as the "last" price.
            bp = float(sdk_quote.bid_price or 0.0)
            ap = float(sdk_quote.ask_price or 0.0)
            if ap > 0:
                price = ap
            elif bp > 0:
                price = bp
            else:
                raise ValueError(f"No quote data for {symbol}")

            quote = Quote(
                symbol=symbol.upper(),
                price=price,
                timestamp=_ts_to_ny(sdk_quote.timestamp),
                provider=self.name,
                data_status=DataStatus.DELAYED,
                bid=bp,
                ask=ap,
                volume=None,
            )
            self._reset_error_state()
            return quote

        except Exception as exc:
            self._handle_error(exc, f"Failed to get quote for {symbol}")
            raise

    # ---------------------------------------------------------------- bars

    def get_bar(self, symbol: str, timeframe: str, timestamp: datetime) -> Bar:
        """Get historical bar closest to ``timestamp`` via the SDK."""
        try:
            tf = _resolve_tf(timeframe)
            target = _ts_to_ny(timestamp)
            start = target.timestamp() - 86400  # ±1 day window
            end = target.timestamp() + 86400

            # Cap ``end`` at "now - 15 min" ONLY when the requested window
            # overlaps the recent-SIP-only zone. Historical lookups (e.g.
            # yesterday's bar) pass through unmodified.
            cap = datetime.now(timezone.utc).timestamp() - 15 * 60
            end_capped = min(end, cap) if end > cap else end

            req = StockBarsRequest(
                symbol_or_symbols=symbol.upper(),
                timeframe=tf,
                start=datetime.fromtimestamp(start, tz=timezone.utc),
                end=datetime.fromtimestamp(end_capped, tz=timezone.utc),
                limit=100,
            )
            client = self._get_data_client()
            barset = client.get_stock_bars(req)
            bars_list = barset.data.get(symbol.upper(), [])
            if not bars_list:
                raise ValueError(f"No bar data for {symbol} at {timestamp}")

            # Find bar closest to target timestamp.
            target_ts = target.timestamp()
            closest = min(
                bars_list,
                key=lambda b: abs(_ts_to_ny(b.timestamp).timestamp() - target_ts),
            )
            return self._bar_from_sdk(symbol, closest, timeframe)

        except Exception as exc:
            self._handle_error(exc, f"Failed to get bar for {symbol}")
            raise

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar:
        """Get the most recent bar for ``symbol`` via the SDK.

        v3.6.1 fix: pass ``start`` (today's midnight NY) and ``end`` (now − 15min
        to dodge the IEX-free-tier "recent SIP" 403). Without ``start``, the SDK
        on a default ``limit=5`` returns the wrong 5 bars (overnight pre-market
        data), which then lands in the DB as a stale 04:00 timestamp while real
        trading is happening at 15:50.
        """
        try:
            tf = _resolve_tf(timeframe)
            # Today midnight in NY → convert to UTC for the SDK.
            from backend.utils.timezone import now_ny as _now_ny
            today_ny = _now_ny().replace(hour=0, minute=0, second=0, microsecond=0)
            start_utc = NY.localize(today_ny) if hasattr(NY, "localize") else \
                today_ny.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
            # Cap end at now-15min to avoid the IEX 403 on recent SIP data.
            end_utc = datetime.now(timezone.utc) - timedelta(minutes=15)
            req = StockBarsRequest(
                symbol_or_symbols=symbol.upper(),
                timeframe=tf,
                start=start_utc,
                end=end_utc,
                limit=1000,
            )
            client = self._get_data_client()
            barset = client.get_stock_bars(req)
            bars_list = barset.data.get(symbol.upper(), [])
            if not bars_list:
                raise ValueError(f"No bar data for {symbol}")

            # Last item in the list is the most recent.
            return self._bar_from_sdk(symbol, bars_list[-1], timeframe)

        except Exception as exc:
            self._handle_error(exc, f"Failed to get latest bar for {symbol}")
            raise

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        range_: str = "3mo",
    ) -> list[Bar]:
        """Fetch a series of OHLCV bars via the SDK.

        v3.6.x fix: pass ``feed=DataFeed.IEX`` (free-tier requirement) and
        ``end=now-15min`` (free-tier recent-data rule). The SDK auto-paginates
        up to 10,000 bars per page, so a 30-day 1m request returns the full
        set in one call.

        Without ``feed=IEX`` the SIP-default request returns 403 on the free
        tier. Without ``end``, the SDK returns the OLDEST 10k bars from
        ``start`` forward (not what callers expect for a "30d" range).
        """
        try:
            tf = _resolve_tf(timeframe)
            duration = _RANGE_SECONDS.get(range_, 7776000)
            end_ts = datetime.now(timezone.utc) - timedelta(minutes=15)
            start_ts = end_ts - timedelta(seconds=duration)

            req = StockBarsRequest(
                symbol_or_symbols=symbol.upper(),
                timeframe=tf,
                start=start_ts,
                end=end_ts,
                feed=_resolve_feed(self._data_tier),
                # NOTE: no ``limit=`` here. The SDK auto-paginates through all
                # pages, so omitting ``limit`` returns the full window (up to ~50k
                # rows). The Alpaca API enforces its own page size of 10k rows;
                # the SDK's get_stock_bars() handles page tokens transparently.
            )
            client = self._get_data_client()
            barset = client.get_stock_bars(req)
            bars_raw = barset.data.get(symbol.upper(), [])
            bars: list[Bar] = [
                self._bar_from_sdk(symbol, item, timeframe) for item in bars_raw
            ]
            self._reset_error_state()
            return bars

        except Exception as exc:
            self._handle_error(exc, f"Failed to get historical bars for {symbol}")
            raise

    # ---------------------------------------------------------------- batch

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Get quotes for multiple symbols.

        Uses the SDK's batch ``get_stock_latest_quote`` endpoint; failed
        symbols return an ERROR quote rather than raising.
        """
        try:
            upper = [s.upper() for s in symbols]
            req = StockLatestQuoteRequest(symbol_or_symbols=upper)
            client = self._get_data_client()
            quotes = client.get_stock_latest_quote(req)

            results: dict[str, Quote] = {}
            for sym in upper:
                sdk_q = quotes.get(sym)
                if sdk_q is None:
                    results[sym] = Quote(
                        symbol=sym,
                        price=0.0,
                        timestamp=datetime.now(timezone.utc),
                        provider=self.name,
                        data_status=DataStatus.ERROR,
                    )
                    continue
                bp = float(sdk_q.bid_price or 0.0)
                ap = float(sdk_q.ask_price or 0.0)
                if ap > 0:
                    price = ap
                elif bp > 0:
                    price = bp
                else:
                    results[sym] = Quote(
                        symbol=sym,
                        price=0.0,
                        timestamp=_ts_to_ny(sdk_q.timestamp),
                        provider=self.name,
                        data_status=DataStatus.ERROR,
                    )
                    continue
                results[sym] = Quote(
                    symbol=sym,
                    price=price,
                    timestamp=_ts_to_ny(sdk_q.timestamp),
                    provider=self.name,
                    data_status=DataStatus.DELAYED,
                    bid=bp,
                    ask=ap,
                    volume=None,
                )
            self._reset_error_state()
            return results
        except Exception as exc:
            self._handle_error(exc, "Failed to get batch quotes")
            # On total failure, return ERROR quotes for all symbols.
            return {
                s.upper(): Quote(
                    symbol=s.upper(),
                    price=0.0,
                    timestamp=datetime.now(timezone.utc),
                    provider=self.name,
                    data_status=DataStatus.ERROR,
                )
                for s in symbols
            }

    # ---------------------------------------------------------------- market status

    def get_market_status(self, symbol: str) -> MarketStatus:
        """Get US equity market open/closed status via the SDK's clock."""
        try:
            tc = self._get_trading_client()
            clock = tc.get_clock()
            now = datetime.now(timezone.utc)

            status = MarketStatus(
                symbol=symbol.upper(),
                is_open=bool(clock.is_open),
                next_open=(
                    _ts_to_ny(clock.next_open) if clock.next_open is not None else None
                ),
                next_close=(
                    _ts_to_ny(clock.next_close)
                    if clock.next_close is not None
                    else None
                ),
                timezone="America/New_York",
                provider=self.name,
                timestamp=now,
            )
            self._reset_error_state()
            return status

        except Exception as exc:
            self._handle_error(exc, f"Failed to get market status for {symbol}")
            raise

    # ---------------------------------------------------------------- capabilities

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name=self.name,
            supports_historical_bars=True,
            supports_latest_quote=True,
            supports_latest_bar=True,
            supports_batch_quotes=True,
            supports_market_status=True,
            min_timeframe="1m",
            max_timeframe="1day",
            max_history_range="5y",
        )

    def is_available(self) -> bool:
        try:
            q = self.get_quote("AAPL")
            return q.price > 0
        except Exception:
            return False

    # ---------------------------------------------------------------- ws integration

    def subscribe(self, symbol: str, timeframe: str = "1m") -> None:
        """Subscribe to real-time bars for ``symbol`` via the Alpaca SDK stream."""
        if not self._api_key or not self._secret_key:
            return  # Not configured; skip.
        self._get_ws_client().subscribe(symbol.upper(), timeframe)

    def unsubscribe(self, symbol: str, timeframe: str = "1m") -> None:
        """Unsubscribe from real-time bars for ``symbol``."""
        client = self._ws_client
        if client is not None:
            client.unsubscribe(symbol.upper(), timeframe)

    def close(self) -> None:
        """Close the Alpaca WebSocket connection."""
        client = self._ws_client
        if client is not None:
            client.close()

    def get_provider_status(self) -> ProviderStatus:
        """Return provider status with WebSocket health appended to error_message."""
        base = super().get_provider_status()
        with self._ws_lock:
            ws_status = self._ws_status
        extra = f" [WS: {ws_status}]"
        return ProviderStatus(
            provider_name=base.provider_name,
            is_healthy=base.is_healthy,
            latency_ms=base.latency_ms,
            rate_limit_remaining=base.rate_limit_remaining,
            last_success=base.last_success,
            error_message=(base.error_message or "") + extra,
            timestamp=_ts_to_ny(datetime.now(timezone.utc)),
            circuit_breaker_state=base.circuit_breaker_state,
            consecutive_failures=base.consecutive_failures,
            total_successes=base.total_successes,
            total_failures=base.total_failures,
            error_count=base.error_count,
            last_error=base.last_error,
        )
