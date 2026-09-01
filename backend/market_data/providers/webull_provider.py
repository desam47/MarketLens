"""
Webull Open API market data provider using the official webull-openapi-python-sdk.

Authentication, HMAC-SHA256 signing, and token management are handled entirely
by the official SDK. We use:
  - ``TradeClient``  to bootstrap the access token (one call at init)
  - ``DataClient``   for all market data queries (quotes, bars)

Environment: test/sandbox by default (``api.sandbox.webull.com``). Set
``WEBULL_USE_SANDBOX=false`` in .env to switch to production (``api.webull.com``).

Credentials are sourced from ``settings.webull``:
  - WEBULL_ENABLED    — must be true to activate this provider
  - WEBULL_APP_KEY    — app key from the Webull Open Platform developer portal
  - WEBULL_APP_SECRET — app secret from the Webull Open Platform developer portal
  - WEBULL_USE_SANDBOX — "true" (default) for sandbox, "false" for production

Security constraints
-------------------
- ``app_key`` and ``app_secret`` are never logged.
- Tokens are held in-process-memory only by the SDK; they are never persisted
  to disk.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.config.settings import settings as _settings
from backend.models.market_data import (
    Bar,
    DataStatus,
    MarketStatus,
    ProviderCapabilities,
    ProviderStatus,
    Quote,
)

from ..provider import BaseMarketDataProvider

logger = logging.getLogger(__name__)

# Our timeframe → Webull SDK timespan.  The SDK uses m1/m5/m15/m30/m60/d/w.
# 2m/3m fall back to m1 (Webull's closest equivalent); 1mo → d (daily approx).
_TIMEFRAME_TO_TIMESPAN: dict[str, str] = {
    "1m":  "M1",
    "2m":  "M1",
    "3m":  "M1",
    "5m":  "M5",
    "15m": "M15",
    "30m": "M30",
    "60m": "M60",
    "1h":  "M60",
    "1d":  "D",
    "1wk": "W",
    "1mo": "D",
}

# Map our ``range_`` to an approximate bar count so the SDK's count param
# covers the requested window.  These are conservative (more bars than needed
# is fine; fewer is not).
_RANGE_TO_COUNT: dict[str, int] = {
    "1d":  1,
    "5d":  5,
    "1mo": 22,
    "3mo": 65,
    "6mo": 130,
    "1y":  252,
    "2y":  504,
    "5y":  1260,
}


def _epoch_ms_to_utc(ms: int | str | float | None) -> datetime:
    """Convert a Webull epoch-millisecond timestamp to a tz-aware UTC datetime."""
    if ms is None:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


class WebullProvider(BaseMarketDataProvider):
    """Webull Market Data provider backed by the official Python SDK.

    Activates when ``settings.webull.enabled`` is true and both
    ``app_key`` / ``app_secret`` are non-empty.
    """

    def __init__(self) -> None:
        super().__init__("webull")
        self._settings = _settings.webull

        app_key = self._settings.app_key
        app_secret = self._settings.app_secret

        if not app_key or not app_secret:
            raise WebullAuthError(
                "WEBULL_APP_KEY and WEBULL_APP_SECRET must be set. "
                "Obtain credentials at https://developer.webull.com"
            )

        # The official SDK handles HMAC signing and token refresh internally.
        # Import lazily so a missing SDK only breaks this provider.
        from webull.core.client import ApiClient as _ApiClient
        from webull.trade.trade_client import TradeClient as _TradeClient
        from webull.data.data_client import DataClient as _DataClient

        # Determine sandbox vs production.
        use_sandbox = _settings.webull.use_sandbox
        region = "us"  # sandbox/production are both under the US region endpoint

        self._api_client = _ApiClient(app_key, app_secret, region)
        endpoint = "api.sandbox.webull.com" if use_sandbox else "api.webull.com"
        self._api_client.add_endpoint(region, endpoint)
        logger.info("Webull SDK configured for %s", endpoint)

        # Bootstrap: one TradeClient call acquires the initial access token.
        # Without this, DataClient init fails because it also needs a token.
        _TradeClient(self._api_client).account_v2.get_account_list()
        logger.info("Webull token bootstrap succeeded")

        self._data_client = _DataClient(self._api_client)
        self._reset_error_state()

    # ---------------------------------------------------------------- quote
    def get_quote(self, symbol: str) -> Quote:
        sym = symbol.upper()
        try:
            resp = self._data_client.market_data.get_snapshot(sym, "US_STOCK")
            if resp.status_code != 200:
                raise RuntimeError(f"Webull snapshot HTTP {resp.status_code}")
            data = resp.json()
            if not isinstance(data, list) or not data:
                raise RuntimeError(f"Webull returned empty snapshot for {sym}")
            field = data[0]

            ts = _epoch_ms_to_utc(field.get("quote_time") or field.get("last_trade_time"))

            quote = Quote(
                symbol=sym,
                price=float(field.get("price") or 0),
                timestamp=ts,
                provider=self.name,
                data_status=DataStatus.DELAYED,
                bid=float(field["bid"]) if field.get("bid") else None,
                ask=float(field["ask"]) if field.get("ask") else None,
                volume=int(field.get("volume") or 0) if field.get("volume") else None,
            )
            self._reset_error_state()
            return quote
        except Exception as e:
            self._handle_error(e, f"get_quote({sym})")
            raise

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Batch snapshot via comma-separated symbol string (SDK supports this)."""
        if not symbols:
            return {}
        sym_str = ",".join(s.upper() for s in symbols)
        try:
            resp = self._data_client.market_data.get_snapshot(sym_str, "US_STOCK")
            results: dict[str, Quote] = {}
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for field in data:
                        sym = field.get("symbol", "").upper()
                        ts = _epoch_ms_to_utc(
                            field.get("quote_time") or field.get("last_trade_time")
                        )
                        results[sym] = Quote(
                            symbol=sym,
                            price=float(field.get("price") or 0),
                            timestamp=ts,
                            provider=self.name,
                            data_status=DataStatus.DELAYED,
                            bid=float(field["bid"]) if field.get("bid") else None,
                            ask=float(field["ask"]) if field.get("ask") else None,
                            volume=int(field.get("volume") or 0)
                            if field.get("volume") else None,
                        )
            # Return empty entries for any missing symbols.
            for sym in symbols:
                sym_upper = sym.upper()
                if sym_upper not in results:
                    results[sym_upper] = Quote(
                        symbol=sym_upper,
                        price=0.0,
                        timestamp=datetime.now(timezone.utc),
                        provider=self.name,
                        data_status=DataStatus.ERROR,
                    )
            self._reset_error_state()
            return results
        except Exception as e:
            self._handle_error(e, f"get_batch_quotes({symbols})")
            # Return error quotes for all on failure.
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

    # ---------------------------------------------------------------- bars
    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        range_: str = "3mo",
    ) -> list[Bar]:
        sym = symbol.upper()
        try:
            timespan = _TIMEFRAME_TO_TIMESPAN.get(timeframe, "D")
            count = _RANGE_TO_COUNT.get(range_, 200)

            resp = self._data_client.market_data.get_history_bar(
                sym, "US_STOCK", timespan, count=str(count)
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Webull bars HTTP {resp.status_code}")
            data = resp.json()
            if not isinstance(data, list):
                self._reset_error_state()
                return []

            bars: list[Bar] = []
            for row in data:
                bars.append(Bar(
                    symbol=sym,
                    timestamp=_epoch_ms_to_utc(row.get("time")),
                    open=float(row.get("open") or 0),
                    high=float(row.get("high") or 0),
                    low=float(row.get("low") or 0),
                    close=float(row.get("close") or 0),
                    volume=int(row.get("volume") or 0) if row.get("volume") else 0,
                    timeframe=timeframe,
                    provider=self.name,
                    data_status=DataStatus.HISTORICAL,
                ))
            self._reset_error_state()
            return bars
        except Exception as e:
            self._handle_error(e, f"get_historical_bars({sym})")
            raise

    def get_latest_bar(self, symbol: str, timeframe: str = "1d") -> Bar:
        """Fetch the single most-recent bar."""
        sym = symbol.upper()
        bars = self.get_historical_bars(sym, timeframe=timeframe, range_="1d")
        if not bars:
            raise ValueError(f"No {timeframe} bar data for {sym}")
        return bars[-1]

    def get_bar(self, symbol: str, timeframe: str, timestamp: datetime) -> Bar:
        """Return the bar closest to ``timestamp``."""
        sym = symbol.upper()
        bars = self.get_historical_bars(sym, timeframe=timeframe, range_="1y")
        if not bars:
            raise ValueError(f"No {timeframe} bar data for {sym}")
        target = timestamp.timestamp()
        closest = min(bars, key=lambda b: abs(b.timestamp.timestamp() - target))
        return closest

    # ---------------------------------------------------------------- market status
    def get_market_status(self, symbol: str) -> MarketStatus:
        sym = symbol.upper()
        try:
            resp = self._data_client.market_data.get_snapshot(sym, "US_STOCK")
            if resp.status_code != 200:
                raise RuntimeError(f"Webull status HTTP {resp.status_code}")
            field = (resp.json() or [{}])[0]
            state = str(field.get("marketStatus") or field.get("marketState") or "")
            is_open = state.upper() in ("OPEN", "REGULAR", "PRE", "POST", "TRUE")
            status = MarketStatus(
                symbol=sym,
                is_open=is_open,
                next_open=None,   # not available in snapshot
                next_close=None,  # not available in snapshot
                timezone="America/New_York",
                provider=self.name,
                timestamp=datetime.now(timezone.utc),
            )
            self._reset_error_state()
            return status
        except Exception as e:
            self._handle_error(e, f"get_market_status({sym})")
            raise

    # ---------------------------------------------------------------- capabilities
    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name=self.name,
            supports_historical_bars=True,
            supports_latest_quote=True,
            supports_latest_bar=True,
            supports_batch_quotes=True,   # comma-separated snapshot is supported
            supports_market_status=True,
            min_timeframe="1m",
            max_timeframe="1y",
        )

    def is_available(self) -> bool:
        """True when SDK bootstrapping succeeded and data client is ready."""
        return self._data_client is not None

    def get_provider_status(self) -> ProviderStatus:
        try:
            return ProviderStatus(
                provider_name=self.name,
                is_healthy=True,
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return ProviderStatus(
                provider_name=self.name,
                is_healthy=False,
                error_message=str(e),
                timestamp=datetime.now(timezone.utc),
            )


class WebullAuthError(Exception):
    """Raised when Webull SDK fails to bootstrap or credentials are invalid."""
