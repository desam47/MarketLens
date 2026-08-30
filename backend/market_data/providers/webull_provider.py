"""
Webull Open API v3 market data provider.

Implements ``MarketDataProvider`` using the Webull Open Platform API.
OAuth 2.0 device flow is used to obtain and refresh access tokens.
Tokens are held in process-memory only and refreshed automatically.

Authentication credentials are sourced from ``settings.webull``:
  - WEBULL_ENABLED          — must be true to activate this provider
  - WEBULL_APP_KEY          — client ID from the Webull Open Platform
  - WEBULL_APP_SECRET       — client secret from the Webull Open Platform

Security constraints
-------------------
- APP_KEY and APP_SECRET are never logged (checked before any log call).
- Tokens are never persisted to disk.
- Auth errors return a generic message to callers; full detail goes to logs.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

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

# Webull Open API v3 base URL
_BASE_URL = "https://openapi.webull.com/api/v3"

# Timeframe → Webull interval string
_INTERVAL_MAP: dict[str, str] = {
    "1m": "m1",
    "2m": "m2",
    "5m": "m5",
    "15m": "m15",
    "30m": "m30",
    "60m": "m60",
    "1h": "m60",
    "1d": "d1",
    "1wk": "w1",
    "1mo": "m1",  # Webull doesn't have native monthly; approximate with daily
}

# Range string mapping
_RANGE_MAP: dict[str, str] = {
    "1d": "1d",
    "5d": "5d",
    "1mo": "1mo",
    "3mo": "3mo",
    "6mo": "6mo",
    "1y": "1y",
    "2y": "2y",
    "5y": "5y",
}


class _TokenStore:
    """Thread-safe in-memory token store with auto-refresh.

    Tokens are refreshed when ``expires_at`` is within ``refresh_buffer``
    seconds of the current time.
    """

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        expires_at: float,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self._lock = threading.Lock()

    def is_expired(self, buffer: float = 60.0) -> bool:
        """True when the token expires within ``buffer`` seconds."""
        with self._lock:
            return time.time() >= (self.expires_at - buffer)

    def update(
        self,
        access_token: str,
        refresh_token: str,
        expires_in: int,
    ) -> None:
        """Replace stored tokens after a refresh."""
        with self._lock:
            self.access_token = access_token
            self.refresh_token = refresh_token
            self.expires_at = time.time() + expires_in


class WebullAuthError(Exception):
    """Raised when Webull authentication fails."""


class WebullProvider(BaseMarketDataProvider):
    """Webull Open API v3 provider.

    Activates only when ``settings.webull.enabled`` is true and
    ``settings.webull.app_key`` / ``app_secret`` are non-empty.
    """

    def __init__(self) -> None:
        super().__init__("webull")
        self._settings = _settings.webull
        self._token: _TokenStore | None = None
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        # Lazily attempt authentication; the manager will mark the provider
        # unavailable if the first call raises.
        self._authenticate()

    # ---------------------------------------------------------------- auth
    def _authenticate(self) -> None:
        """Run the OAuth 2.0 device flow to obtain an access token."""
        app_key = self._settings.app_key
        app_secret = self._settings.app_secret

        if not app_key or not app_secret:
            raise WebullAuthError(
                "Webull app_key or app_secret is not configured. "
                "Set WEBULL_ENABLED=true, WEBULL_APP_KEY, and WEBULL_APP_SECRET."
            )

        try:
            # Step 1: request device code
            device_resp = self._session.post(
                f"{_BASE_URL}/oauth2/device/code",
                json={"appKey": app_key, "appSecret": app_secret},
                timeout=self._settings.request_timeout,
            )
            if device_resp.status_code != 200:
                raise WebullAuthError(
                    f"Webull device code request failed: HTTP {device_resp.status_code}"
                )
            device_data = device_resp.json()

            device_code = device_data["deviceCode"]
            user_code = device_data.get("userCode", "")
            interval = device_data.get("interval", 5)
            # expires_in is the polling window; token itself may last longer
            poll_timeout = device_data.get("expiresIn", 1800)
            verification_uri = device_data.get("verificationUriComplete", "")

            logger.info(
                "Webull device flow initiated — please authorize at: %s (code: %s)",
                verification_uri or device_data.get("verificationUri", "N/A"),
                user_code,
            )

            # Step 2: poll until user approves
            deadline = time.time() + poll_timeout
            while time.time() < deadline:
                token_resp = self._session.post(
                    f"{_BASE_URL}/oauth2/device/token",
                    json={
                        "appKey": app_key,
                        "appSecret": app_secret,
                        "deviceCode": device_code,
                        "grantType": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    timeout=self._settings.request_timeout,
                )
                token_data = token_resp.json()

                if token_resp.status_code == 200:
                    self._token = _TokenStore(
                        access_token=token_data["accessToken"],
                        refresh_token=token_data["refreshToken"],
                        expires_at=time.time() + token_data.get("expiresIn", 3600),
                    )
                    logger.info("Webull OAuth2 authentication succeeded")
                    return

                error_code = token_data.get("error", "")
                if error_code == "authorization_pending":
                    time.sleep(interval)
                    continue
                elif error_code == "slow_down":
                    time.sleep(interval * 2)
                    continue
                else:
                    raise WebullAuthError(
                        f"Webull token request failed: error={error_code}, "
                        f"description={token_data.get('error_description', 'N/A')}"
                    )

            raise WebullAuthError("Webull authorization timed out — user did not approve in time.")

        except requests.RequestException as e:
            raise WebullAuthError(f"Webull authentication network error: {e}") from e

    def _ensure_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        if self._token is None:
            self._authenticate()
        if self._token is None:
            raise WebullAuthError("Webull token store is not initialized.")
        if self._token.is_expired():
            self._refresh_token()
        # SAFETY: never log the raw token value.
        return self._token.access_token

    def _refresh_token(self) -> None:
        """Exchange the refresh token for a new access token."""
        app_key = self._settings.app_key
        app_secret = self._settings.app_secret
        assert self._token is not None

        try:
            resp = self._session.post(
                f"{_BASE_URL}/oauth2/refresh/token",
                json={
                    "appKey": app_key,
                    "appSecret": app_secret,
                    "refreshToken": self._token.refresh_token,
                    "grantType": "refresh_token",
                },
                timeout=self._settings.request_timeout,
            )
            if resp.status_code != 200:
                logger.warning("Webull token refresh failed: HTTP %d — re-authenticating", resp.status_code)
                self._token = None
                self._authenticate()
                return

            data = resp.json()
            self._token.update(
                access_token=data["accessToken"],
                refresh_token=data["refreshToken"],
                expires_in=data.get("expiresIn", 3600),
            )
            logger.info("Webull token refreshed successfully")
        except requests.RequestException as e:
            logger.warning("Webull token refresh network error: %s — re-authenticating", e)
            self._token = None
            self._authenticate()

    def _headers(self, require_auth: bool = False) -> dict[str, str]:
        """Build request headers, optionally including an access token."""
        headers = dict(self._session.headers)
        if require_auth:
            token = self._ensure_token()
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ---------------------------------------------------------------- http helpers
    def _get(self, path: str, require_auth: bool = False, **kwargs: Any) -> dict:
        """GET from the Webull API, returning parsed JSON."""
        kwargs.setdefault("timeout", self._settings.request_timeout)
        resp = self._session.get(
            f"{_BASE_URL}{path}",
            headers=self._headers(require_auth),
            **kwargs,
        )
        if resp.status_code == 401:
            logger.warning("Webull 401 — attempting token refresh")
            self._refresh_token()
            resp = self._session.get(
                f"{_BASE_URL}{path}",
                headers=self._headers(require_auth),
                **kwargs,
            )
        if resp.status_code == 429:
            # Rate limited — raise RuntimeError so the circuit breaker counts it
            raise RuntimeError(f"Webull HTTP 429 rate limited")
        if resp.status_code != 200:
            # SECURITY: never log raw response body — it could contain credentials
            logger.warning("Webull API error: HTTP %s on %s", resp.status_code, path)
            raise RuntimeError(f"Webull API HTTP {resp.status_code} on {path}")
        return resp.json()

    # ---------------------------------------------------------------- public interface
    def get_quote(self, symbol: str) -> Quote:
        """Get the latest quote for a symbol.

        Webull quote endpoint requires no auth for basic market data.
        """
        try:
            data = self._get(f"/quote/{symbol.upper()}", require_auth=False)
            field = data.get("quote", data)
            price = float(field.get("close") or field.get("last", 0.0))
            ts_epoch = field.get("timestamp") or field.get("tradeTime") or 0
            try:
                ts = datetime.fromtimestamp(int(ts_epoch) / 1000, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                ts = datetime.now(timezone.utc)

            quote = Quote(
                symbol=symbol.upper(),
                price=price,
                timestamp=ts,
                provider=self.name,
                data_status=DataStatus.DELAYED,
                bid=field.get("bid"),
                ask=field.get("ask"),
                volume=field.get("volume"),
            )
            self._reset_error_state()
            return quote
        except Exception as e:
            self._handle_error(e, f"get_quote({symbol})")
            raise

    def get_bar(self, symbol: str, timeframe: str, timestamp: datetime) -> Bar:
        """Get the historical bar closest to the given timestamp."""
        bars = self.get_historical_bars(symbol, timeframe=timeframe, range_="3mo")
        if not bars:
            raise ValueError(f"No bar data for {symbol} at {timeframe}")
        target = timestamp.timestamp()
        closest = min(bars, key=lambda b: abs(b.timestamp.timestamp() - target))
        return closest

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar:
        """Get the most recent bar for a symbol."""
        bars = self.get_historical_bars(symbol, timeframe=timeframe, range_="1mo")
        if not bars:
            raise ValueError(f"No bar data for {symbol} at {timeframe}")
        return bars[-1]

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        range_: str = "3mo",
    ) -> list[Bar]:
        """Get a series of historical bars."""
        try:
            interval = _INTERVAL_MAP.get(timeframe, "d1")
            r = _RANGE_MAP.get(range_, "3mo")

            data = self._get(
                f"/bars/{symbol.upper()}",
                require_auth=False,
                params={"interval": interval, "extend": "0", "needRange": "1"},
            )
            rows = data.get("bars", []) or data.get("data", []) or []
            if not rows:
                self._reset_error_state()
                return []

            bars: list[Bar] = []
            for row in rows:
                try:
                    ts = datetime.fromtimestamp(int(row["t"]) / 1000, tz=timezone.utc)
                except (KeyError, TypeError, ValueError):
                    continue
                bars.append(Bar(
                    symbol=symbol.upper(),
                    timestamp=ts,
                    open=float(row.get("o", 0.0)),
                    high=float(row.get("h", 0.0)),
                    low=float(row.get("l", 0.0)),
                    close=float(row.get("c", 0.0)),
                    volume=int(row.get("v", 0)),
                    timeframe=timeframe,
                    provider=self.name,
                    data_status=DataStatus.HISTORICAL,
                ))
            self._reset_error_state()
            return bars
        except Exception as e:
            self._handle_error(e, f"get_historical_bars({symbol})")
            raise

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Batch quotes are not supported by Webull — fall back to individual calls."""
        results: dict[str, Quote] = {}
        for sym in symbols:
            try:
                results[sym] = self.get_quote(sym)
            except Exception:
                results[sym] = Quote(
                    symbol=sym.upper(),
                    price=0.0,
                    timestamp=datetime.now(timezone.utc),
                    provider=self.name,
                    data_status=DataStatus.ERROR,
                )
        return results

    def get_market_status(self, symbol: str) -> MarketStatus:
        """Return market status from the quote endpoint."""
        try:
            data = self._get(f"/quote/{symbol.upper()}", require_auth=False)
            field = data.get("quote", data)
            # Webull sends isOpen True/False or a state string.
            state = field.get("marketStatus", field.get("marketState", ""))
            is_open = str(state).upper() in ("OPEN", "REGULAR", "PRE", "POST", "TRUE")
            tz_name = str(field.get("timezone", "America/New_York"))
            try:
                now = datetime.now(timezone.utc)
                next_open = now.replace(hour=9, minute=30, second=0)  # rough estimate
                next_close = now.replace(hour=16, minute=0, second=0)
            except Exception:
                next_open = None
                next_close = None

            status = MarketStatus(
                symbol=symbol.upper(),
                is_open=is_open,
                next_open=next_open,
                next_close=next_close,
                timezone=tz_name,
                provider=self.name,
                timestamp=datetime.now(timezone.utc),
            )
            self._reset_error_state()
            return status
        except Exception as e:
            self._handle_error(e, f"get_market_status({symbol})")
            raise

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name=self.name,
            supports_historical_bars=True,
            supports_latest_quote=True,
            supports_latest_bar=True,
            supports_batch_quotes=False,
            supports_market_status=True,
            min_timeframe="1m",
            max_timeframe="2y",
        )

    def is_available(self) -> bool:
        """True when authentication succeeded and the token is valid."""
        try:
            # Quick check: token exists and is not expired
            if self._token is None:
                return False
            if self._token.is_expired(buffer=10.0):
                self._refresh_token()
            # Ping with a lightweight request
            self._get("/quote/AAPL", require_auth=False)
            return True
        except Exception as e:
            logger.debug("Webull availability check failed: %s", e)
            return False
