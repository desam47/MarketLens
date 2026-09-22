"""
Finnhub market data provider (v2.2).

Implements the ``MarketDataProvider`` ABC, consuming the Finnhub REST API
for quotes, OHLCV candles, and market status.

**Free tier:** 30 req/sec rate limit (IP-based, no API key required).
Providing an API key upgrades to 60 req/sec and unlocks higher rate limits.

**Provider chain position:** Primary by default (``MARKET_DATA_PRIMARY_PROVIDER=finnhub``).
Falls back to Yahoo Finance, then Webull.

**Endpoints consumed:**
  - Quote:        GET /quote?symbol={sym}
  - Candles:      GET /stock/candle?symbol={sym}&resolution={res}&from={ts}&to={ts}
  - Market status: GET /market-status?exchange=US

**Rate limit handling:** HTTP 429 → RuntimeError so the circuit breaker
correctly tracks the failure and opens the circuit.
"""

import logging
from datetime import UTC, datetime

import requests

from backend.config.settings import settings as _settings
from backend.models.market_data import (
    Bar,
    DataStatus,
    MarketStatus,
    ProviderCapabilities,
    Quote,
)

from ..provider import BaseMarketDataProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"

# Map our timeframe strings to Finnhub candle resolution codes.
# Finnhub resolution: 1, 5, 15, 30, 60, D, W, M
_FINNHUB_RESOLUTION_MAP: dict[str, str] = {
    "1m": "1",
    "2m": "1",  # No 2m on Finnhub, fall back to 1m
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1h": "60",  # Finnhub uses "60" for hourly
    "4h": "60",  # Finnhub has no 4h; fetch 60-min bars and let the
    # caller resample to 4h at read time
    "90m": "60",  # No 90m, approximate with 60
    "1d": "D",
    "5d": "D",  # No 5d, approximate with daily
    "1wk": "W",
    "1mo": "M",
}

# Range string → approximate duration in seconds.
_RANGE_SECONDS: dict[str, int] = {
    "1d": 86400,
    "5d": 432000,
    "1mo": 2592000,
    "3mo": 7776000,
    "6mo": 15552000,
    "1y": 31536000,
    "2y": 63072000,
    "5y": 157680000,
    "10y": 315360000,
    "ytd": 86400,  # approximated
    "max": 315360000,  # ~10 years
}


def _resolve_resolution(timeframe: str) -> str:
    """Map a timeframe string to a Finnhub resolution code."""
    res = _FINNHUB_RESOLUTION_MAP.get(timeframe)
    if res is None:
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}; supported: {sorted(_FINNHUB_RESOLUTION_MAP)}"
        )
    return res


def _range_to_seconds(range_: str) -> int:
    """Return approximate duration in seconds for a range string."""
    return _RANGE_SECONDS.get(range_, 7776000)  # default 3 months


class FinnhubProvider(BaseMarketDataProvider):
    """Finnhub REST API implementation of ``MarketDataProvider``."""

    def __init__(self) -> None:
        super().__init__("finnhub")
        self._api_key = _settings.finnhub.api_key
        self._timeout = _settings.finnhub.request_timeout

    # ---------------------------------------------------------------- helpers

    def _headers(self) -> dict:
        """Auth header for Finnhub requests.

        The key goes in a header, not a query param: ``requests``/``urllib3``
        embed the full request URL — including its query string — in
        connection/timeout exception messages, and those exceptions get
        logged verbatim (see ``_get`` below). A header never appears in
        that text, so a network blip can't leak the key into the logs.
        """
        return {"X-Finnhub-Token": self._api_key} if self._api_key else {}

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        """Make an authenticated GET request to the Finnhub API.

        HTTP 429 → RuntimeError so the circuit breaker tracks it.
        HTTP 4xx/5xx → RuntimeError.
        Empty body → ValueError.
        """
        url = f"{_BASE_URL}/{endpoint}"
        all_params: dict = params.copy() if params else {}

        try:
            r = requests.get(url, params=all_params, headers=self._headers(), timeout=self._timeout)
        except requests.RequestException as e:
            raise RuntimeError(f"Finnhub request failed: {e}") from e

        if r.status_code == 429:
            raise RuntimeError("Finnhub rate limited (HTTP 429)")
        if r.status_code == 403:
            raise RuntimeError("Finnhub forbidden — check API key (HTTP 403)")
        if not r.ok:
            raise RuntimeError(f"Finnhub HTTP {r.status_code} for {endpoint}: {r.text[:200]}")

        # Free tier quirk: some endpoints (e.g. /market-status) return
        # 200 OK with an HTML body instead of JSON. Detect via Content-Type
        # and raise cleanly so the provider chain can fall through.
        ctype = r.headers.get("Content-Type", "")
        if "json" not in ctype.lower():
            raise RuntimeError(
                f"Finnhub {endpoint} returned non-JSON response "
                f"(Content-Type={ctype!r}) — likely a tier restriction"
            )

        data = r.json()
        if data is None or data == {}:
            raise ValueError(f"Finnhub returned empty response for {endpoint}")
        return data

    def _get_symbol_params(self, symbol: str) -> dict:
        """Return the base params dict with symbol. Auth goes via ``_headers()``."""
        return {"symbol": symbol.upper()}

    # ---------------------------------------------------------------- quote

    def get_quote(self, symbol: str) -> Quote:
        """Get latest quote via Finnhub /quote endpoint.

        Returns current price, open/high/low/close, previous close,
        and timestamp.
        """
        try:
            data = self._get("quote", self._get_symbol_params(symbol))

            # Finnhub returns {c, d, dp, h, l, o, pc, t}
            # c=current price, o=open, h=high, l=low, pc=previous close,
            # t=timestamp (Unix), d=change, dp=change%
            price = data.get("c", 0.0)
            if price == 0.0 and data.get("pc", 0.0) == 0.0:
                raise ValueError(f"No quote data for {symbol}")

            ts = datetime.fromtimestamp(data.get("t", 0), tz=UTC)
            if ts.year < 1970:
                ts = datetime.now(UTC)

            quote = Quote(
                symbol=symbol.upper(),
                price=float(price),
                timestamp=ts,
                provider=self.name,
                data_status=DataStatus.DELAYED,  # Finnhub quotes are ~15min delayed
                bid=data.get("b"),
                ask=data.get("a"),
                volume=None,  # Finnhub /quote doesn't include volume
            )
            self._reset_error_state()
            return quote

        except Exception as e:
            self._handle_error(e, f"Failed to get quote for {symbol}")
            raise

    # ---------------------------------------------------------------- bars

    def get_bar(self, symbol: str, timeframe: str, timestamp: datetime) -> Bar:
        """Get historical bar closest to the given timestamp."""
        try:
            resolution = _resolve_resolution(timeframe)
            from_ts = int(timestamp.timestamp())
            to_ts = from_ts + _range_to_seconds("1mo")  # ~1 month window

            data = self._get(
                "stock/candle",
                {
                    **self._get_symbol_params(symbol),
                    "resolution": resolution,
                    "from": from_ts,
                    "to": to_ts,
                },
            )

            if data.get("s") == "no_data":
                raise ValueError(f"No historical data for {symbol} at {timestamp}")

            timestamps = data.get("t", [])
            closes = data.get("c", [])
            if not timestamps or not closes:
                raise ValueError(f"No candle data for {symbol}")

            # Find bar closest to requested timestamp
            target = timestamp.timestamp()
            idx = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - target))

            return Bar(
                symbol=symbol.upper(),
                timestamp=datetime.fromtimestamp(timestamps[idx], tz=UTC),
                open=float(data["o"][idx]) if data.get("o") else 0.0,
                high=float(data["h"][idx]) if data.get("h") else 0.0,
                low=float(data["l"][idx]) if data.get("l") else 0.0,
                close=float(closes[idx]),
                volume=int(data["v"][idx]) if data.get("v") else 0,
                timeframe=timeframe,
                provider=self.name,
                data_status=DataStatus.HISTORICAL,
            )

        except Exception as e:
            self._handle_error(e, f"Failed to get bar for {symbol} at {timestamp}")
            raise

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar:
        """Get the most recent bar for a symbol and timeframe.

        Phase 3.1: when ``timeframe == "1m"`` the candle endpoint with
        resolution "1" returns bars up to ~1 month old on Finnhub's free
        tier. The ``datetime.now()`` target ensures the returned bar is the
        most recent 1m bar available.
        """
        return self.get_bar(symbol, timeframe, datetime.now(UTC))

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        range_: str = "3mo",
        include_extended_hours: bool = False,
    ) -> list[Bar]:
        """Fetch a series of OHLCV bars from the Finnhub candle endpoint.

        ``include_extended_hours`` is accepted for interface compatibility
        with WebullProvider but currently ignored.

        Returns bars in oldest → newest order.
        """
        try:
            resolution = _resolve_resolution(timeframe)
            now_ts = int(datetime.now(UTC).timestamp())
            duration = _range_to_seconds(range_)
            from_ts = max(0, now_ts - duration)

            data = self._get(
                "stock/candle",
                {
                    **self._get_symbol_params(symbol),
                    "resolution": resolution,
                    "from": from_ts,
                    "to": now_ts,
                },
            )

            if data.get("s") == "no_data":
                self._reset_error_state()
                return []

            timestamps = data.get("t", [])
            opens = data.get("o", [])
            highs = data.get("h", [])
            lows = data.get("l", [])
            closes = data.get("c", [])
            volumes = data.get("v", [])

            if not timestamps:
                self._reset_error_state()
                return []

            bars: list[Bar] = []
            for i in range(len(timestamps)):
                bars.append(
                    Bar(
                        symbol=symbol.upper(),
                        timestamp=datetime.fromtimestamp(timestamps[i], tz=UTC),
                        open=float(opens[i]) if opens and opens[i] is not None else 0.0,
                        high=float(highs[i]) if highs and highs[i] is not None else 0.0,
                        low=float(lows[i]) if lows and lows[i] is not None else 0.0,
                        close=float(closes[i]) if closes and closes[i] is not None else 0.0,
                        volume=int(volumes[i]) if volumes and volumes[i] is not None else 0,
                        timeframe=timeframe,
                        provider=self.name,
                        data_status=DataStatus.HISTORICAL,
                    )
                )

            self._reset_error_state()
            return bars

        except Exception as e:
            self._handle_error(e, f"Failed to get historical bars for {symbol}")
            raise

    # ---------------------------------------------------------------- batch

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Get quotes for multiple symbols.

        Finnhub has no batch endpoint, so we loop and collect. Each call
        is gated by the per-provider rate limiter and the circuit breaker
        so a burst of N symbols can't cascade into 429s / OPEN state.
        Symbols that fail return an ERROR quote rather than raising.
        """
        if not symbols:
            return {}

        # Lazy imports to avoid circular dependency: services/ → providers/.
        from backend.market_data.services.providers import (
            _get_breaker,
            _get_per_provider_rate_limit,
            _rate_limiter,
        )

        provider_name = self.name
        breaker = _get_breaker(provider_name)
        limit = _get_per_provider_rate_limit(provider_name)

        results: dict[str, Quote] = {}
        for symbol in symbols:
            try:
                # Honour the per-provider rate limit before the call so
                # bursts of N symbols are throttled to ``limit``/min.
                _rate_limiter.acquire(provider_name, limit)
                # Route through the circuit breaker so a single failure
                # doesn't cascade into 429s across the whole batch.
                results[symbol.upper()] = breaker.call(self.get_quote, symbol)
            except Exception:
                results[symbol.upper()] = Quote(
                    symbol=symbol.upper(),
                    price=0.0,
                    timestamp=datetime.now(UTC),
                    provider=self.name,
                    data_status=DataStatus.ERROR,
                )
        return results

    # ---------------------------------------------------------------- market status

    def get_market_status(self, symbol: str) -> MarketStatus:
        """Get US market open/closed status."""
        try:
            data = self._get("market-status", {"exchange": "US"})
            is_open = data.get("session") == "regular"
            now = datetime.now(UTC)

            status = MarketStatus(
                symbol=symbol.upper(),
                is_open=is_open,
                next_open=None,  # Finnhub /market-status doesn't provide next open/close
                next_close=None,
                timezone="America/New_York",
                provider=self.name,
                timestamp=now,
            )
            self._reset_error_state()
            return status

        except Exception as e:
            self._handle_error(e, f"Failed to get market status for {symbol}")
            raise

    # ---------------------------------------------------------------- capabilities

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name=self.name,
            supports_historical_bars=True,
            supports_latest_quote=True,
            supports_latest_bar=True,
            supports_batch_quotes=True,  # Loops get_quote per symbol
            supports_market_status=True,
            min_timeframe="1m",
            max_timeframe="1mo",
        )

    def is_available(self) -> bool:
        try:
            q = self.get_quote("AAPL")
            return q.price > 0
        except Exception:
            return False
