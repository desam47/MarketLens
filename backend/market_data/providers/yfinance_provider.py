"""
Yahoo Finance market data provider implementation.

Uses the public chart endpoint (query1.finance.yahoo.com/v8/finance/chart) directly
because the yfinance library's ticker.info (quoteSummary) endpoint is heavily
rate-limited as of 2024-2025. The chart endpoint returns historical bars and
the most recent price/quote metadata in one request.

We use curl_cffi instead of the standard `requests` or `urllib` libraries because
Yahoo Finance performs TLS-layer browser fingerprinting. Standard Python HTTP clients
receive HTTP 429 (Too Many Requests) due to differences in TLS cipher suites and
ALPN protocols. curl_cffi uses libcurl under the hood and correctly impersonates a
real browser (Chrome 120), bypassing the anti-bot protection.
"""
import asyncio
import logging
import threading
import time
from datetime import UTC, datetime

from curl_cffi import requests as curl_requests

from backend.models.market_data import (
    Bar,
    DataStatus,
    MarketStatus,
    ProviderCapabilities,
    Quote,
)
from backend.utils.timezone import to_ny

from ..provider import BaseMarketDataProvider, safe_json

logger = logging.getLogger(__name__)

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# Yahoo's v7 batch-quote endpoint rejects anonymous calls with HTTP 401 ("User is unable
# to access this feature"): it needs a session cookie plus a "crumb" token. The cookie
# comes from fc.yahoo.com (which answers 404 but sets it), the crumb from getcrumb.
_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
_COOKIE_URL = "https://fc.yahoo.com"
_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
_CRUMB_TTL_SECONDS = 3600.0
_INTERVAL_MAP = {
    "1m": "1m", "2m": "2m", "5m": "5m", "15m": "15m", "30m": "30m",
    "60m": "60m", "90m": "90m", "1h": "60m",
    "4h": "60m",   # yfinance has no 4h; fetch 1h bars and resample at read time
    "1d": "1d", "5d": "5d", "1wk": "1wk", "1mo": "1mo", "3mo": "3mo",
}
_RANGE_MAP = {
    "1m": "1d", "5m": "5d", "15m": "5d", "30m": "5d",
    "1h": "1y", "4h": "1y", "1d": "6mo", "1wk": "2y", "1mo": "5y",
}


class YFinanceProvider(BaseMarketDataProvider):
    """Yahoo Finance implementation of MarketDataProvider using the chart API via curl_cffi."""

    def __init__(self):
        super().__init__("yahoo_finance")
        # Cached Yahoo session credentials for the batch-quote endpoint. Only the
        # cookies + crumb are shared (each request is otherwise stateless): this
        # provider is called from several threads and a curl_cffi Session is not
        # guaranteed thread-safe.
        self._auth_lock = threading.Lock()
        self._cookies: dict[str, str] = {}
        self._crumb: str | None = None
        self._crumb_at = 0.0

    def _get_auth(self, *, force: bool = False) -> tuple[dict[str, str], str]:
        """Return ``(cookies, crumb)`` for the v7 quote endpoint, fetching when stale.

        Refreshed hourly, or immediately with ``force`` (after a 401/403). The lock
        makes concurrent callers share ONE fetch instead of each hitting Yahoo.
        """
        with self._auth_lock:
            fresh = self._crumb is not None and (time.monotonic() - self._crumb_at) < _CRUMB_TTL_SECONDS
            if force or not fresh:
                session = curl_requests.Session(impersonate="chrome120")
                session.get(_COOKIE_URL, timeout=10)  # 404 is expected; only the cookie matters
                r = session.get(_CRUMB_URL, timeout=10)
                crumb = (r.text or "").strip()
                if r.status_code != 200 or not crumb or "<" in crumb:  # "<" = an HTML error page
                    raise RuntimeError(
                        f"Yahoo Finance crumb fetch failed: HTTP {r.status_code} {crumb[:80]!r}"
                    )
                self._cookies = dict(session.cookies.items())
                self._crumb, self._crumb_at = crumb, time.monotonic()
            return dict(self._cookies), self._crumb

    def _batch_quote_response(self, symbols: list[str]):
        """GET the batch quote, refreshing the cookie/crumb once if Yahoo rejects them."""
        for attempt in (0, 1):
            cookies, crumb = self._get_auth(force=attempt == 1)
            r = curl_requests.get(
                _QUOTE_URL,
                # ``params`` so symbols like ^VIX / BRK-B are URL-encoded correctly.
                params={"symbols": ",".join(symbols), "crumb": crumb},
                cookies=cookies,
                impersonate="chrome120",
                timeout=15,
            )
            if r.status_code in (401, 403) and attempt == 0:
                continue  # stale crumb/cookie: refresh once and retry
            return r
        return r

    # ---------------------------------------------------------------- helpers
    def _fetch_chart(self, symbol: str, interval: str, range_: str) -> dict:
        url = f"{_CHART_URL.format(symbol=symbol)}?interval={interval}&range={range_}&includePrePost=false&events=div%2Csplits"
        r = curl_requests.get(
            url,
            impersonate="chrome120",
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Yahoo Finance HTTP {r.status_code} for {symbol}: {r.text[:200]}"
            )
        data = safe_json(r.text, url=url)
        result = (data.get("chart") or {}).get("result")
        if not result:
            err = (data.get("chart") or {}).get("error")
            raise ValueError(f"No chart data for {symbol}: {err}")
        return result[0] if isinstance(result, list) and result else {}


    @staticmethod
    def _meta(chart: dict) -> dict:
        return chart.get("meta", {}) or {}

    @staticmethod
    def _rows(chart: dict) -> tuple:
        ts = chart.get("timestamp", []) or []
        ind = chart.get("indicators", {}).get("quote", [{}])[0] or {}
        opens = ind.get("open", []) or []
        highs = ind.get("high", []) or []
        lows = ind.get("low", []) or []
        closes = ind.get("close", []) or []
        volumes = ind.get("volume", []) or []
        return ts, opens, highs, lows, closes, volumes

    @staticmethod
    def _bar_from_chart_data(
        symbol: str,
        index: int,
        ts_arr: list,
        opens: list,
        highs: list,
        lows: list,
        closes: list,
        volumes: list,
        timeframe: str,
        data_status: DataStatus,
        provider: str,
    ) -> Bar:
        """Build a Bar from a single row in the chart response.

        Pulled out of get_bar / get_latest_bar so all of the
        ``None → 0.0`` coercion and timestamp conversion lives in one
        place. ``get_historical_bars`` reuses this for the full series.
        """
        return Bar(
            symbol=symbol.upper(),
            timestamp=to_ny(datetime.fromtimestamp(int(ts_arr[index]), tz=UTC)),
            open=float(opens[index]) if opens[index] is not None else 0.0,
            high=float(highs[index]) if highs[index] is not None else 0.0,
            low=float(lows[index]) if lows[index] is not None else 0.0,
            close=float(closes[index]) if closes[index] is not None else 0.0,
            volume=int(volumes[index]) if volumes[index] is not None else 0,
            timeframe=timeframe,
            provider=provider,
            data_status=data_status,
        )

    @staticmethod
    def _resolve_interval(timeframe: str) -> str:
        if timeframe not in _INTERVAL_MAP:
            raise ValueError(
                f"Unsupported timeframe {timeframe!r}; "
                f"supported: {sorted(_INTERVAL_MAP)}"
            )
        return _INTERVAL_MAP[timeframe]

    @staticmethod
    def _resolve_range(range_: str) -> str:
        # Allow callers to pass either a Yahoo range string ("3mo", "1y",
        # "5y", ...) directly or to use the per-timeframe default mapping.
        # If it isn't in the map, treat the input as already-resolved and
        # pass it through; Yahoo accepts a known set of range strings
        # (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max).
        if range_ in _RANGE_MAP:
            return _RANGE_MAP[range_]
        return range_

    # ---------------------------------------------------------------- public
    def get_quote(self, symbol: str) -> Quote:
        """Get latest quote via 1d / 5d chart request."""
        try:
            chart = self._fetch_chart(symbol, interval="1d", range_="5d")
            meta = self._meta(chart)
            price = (
                meta.get("regularMarketPrice")
                or meta.get("previousClose")
                or 0.0
            )
            ts_epoch = (
                meta.get("regularMarketTime")
                or meta.get("chartPreviousClose")
                or 0
            )
            try:
                ts = datetime.fromtimestamp(int(ts_epoch), tz=UTC) if ts_epoch else datetime.now(UTC)
            except (TypeError, ValueError, OSError):
                ts = datetime.now(UTC)

            quote = Quote(
                symbol=symbol.upper(),
                price=float(price),
                timestamp=to_ny(ts),
                provider=self.name,
                data_status=DataStatus.DELAYED,
                bid=meta.get("bid"),
                ask=meta.get("ask"),
                volume=meta.get("regularMarketVolume"),
            )
            self._reset_error_state()
            return quote
        except Exception as e:
            self._handle_error(e, f"Failed to get quote for {symbol}")
            raise

    def get_bar(self, symbol: str, timeframe: str, timestamp: datetime) -> Bar:
        """Get historical bar closest to timestamp."""
        try:
            interval = self._resolve_interval(timeframe)
            range_ = self._resolve_range(timeframe)
            chart = self._fetch_chart(symbol, interval=interval, range_=range_)
            ts_arr, opens, highs, lows, closes, volumes = self._rows(chart)
            if not ts_arr:
                raise ValueError(f"No historical data found for {symbol}")
            target = timestamp.timestamp()
            idx = min(range(len(ts_arr)), key=lambda i: abs(ts_arr[i] - target))
            bar = self._bar_from_chart_data(
                symbol, idx, ts_arr, opens, highs, lows, closes, volumes,
                timeframe, DataStatus.HISTORICAL, self.name,
            )
            self._reset_error_state()
            return bar
        except Exception as e:
            self._handle_error(e, f"Failed to get bar for {symbol} at {timestamp}")
            raise

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar:
        try:
            interval = self._resolve_interval(timeframe)
            range_ = self._resolve_range(timeframe)
            chart = self._fetch_chart(symbol, interval=interval, range_=range_)
            ts_arr, opens, highs, lows, closes, volumes = self._rows(chart)
            if not ts_arr:
                raise ValueError(f"No historical data found for {symbol}")
            i = -1
            while abs(i) <= len(ts_arr) and (i == -1 or closes[i] is None):
                i -= 1
            bar = self._bar_from_chart_data(
                symbol, i, ts_arr, opens, highs, lows, closes, volumes,
                timeframe, DataStatus.DELAYED, self.name,
            )
            self._reset_error_state()
            return bar
        except Exception as e:
            self._handle_error(e, f"Failed to get latest bar for {symbol} ({timeframe})")
            raise

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        range_: str = "3mo",
        include_extended_hours: bool = False,
    ) -> list[Bar]:
        """Fetch a full series of bars from the chart endpoint.

        ``include_extended_hours`` is accepted for interface compatibility
        with WebullProvider but currently ignored — this provider always
        returns RTH-only (see the docstring note below: intraday bars
        outside market hours are explicitly filtered out already).

        Returns bars in the order Yahoo provides them (oldest → newest).
        Skips any rows where close is None (those are intraday bars
        outside market hours) so the caller doesn't have to filter.
        """
        try:
            interval = self._resolve_interval(timeframe)
            resolved_range = self._resolve_range(range_)
            chart = self._fetch_chart(symbol, interval=interval, range_=resolved_range)
            ts_arr, opens, highs, lows, closes, volumes = self._rows(chart)
            if not ts_arr:
                self._reset_error_state()
                return []

            bars: list[Bar] = []
            for i in range(len(ts_arr)):
                # Yahoo sometimes returns None for the most recent
                # intraday bar (the in-progress candle) — drop those.
                if closes[i] is None:
                    continue
                # Yahoo's LAST row for an INTRADAY interval is often not a
                # settled candle even when close isn't None — it's a live
                # snapshot of the still-forming bucket: open == high == low
                # == close (a single tick, not an aggregate), volume == 0,
                # and a timestamp at the live-tick's epoch second rather
                # than the clean interval boundary (e.g. 15:15:47 instead
                # of 15:15:00). Every settled Yahoo candle — and every
                # other provider's — lands exactly on the interval
                # boundary, so a non-zero seconds component reliably
                # identifies this synthetic row. Without this, gap-fill
                # ingestion (which re-fetches this same live snapshot on
                # every cycle, each time with an advancing timestamp) piles
                # up one 0-volume "duplicate" bar per cycle next to the
                # real bar for that minute.
                #
                # A SECOND variant of the same phenomenon (2026-09-09 fix,
                # found live via a "why do 4h bars only cover 08:00/12:00"
                # question): at the very close of a session, Yahoo can
                # return this same synthetic snapshot already landed
                # exactly ON the interval boundary (e.g. a "60m" bar
                # stamped 16:00:00 with open==high==low==close, volume==0)
                # — the seconds-based check above doesn't catch this since
                # the timestamp looks clean. The values themselves still
                # carry the same signature the docstring above already
                # describes, so check those too: a single-tick snapshot
                # has zero range AND zero reported volume, which a genuine
                # settled candle — even a real quiet one — essentially
                # never does simultaneously (observed live: DVLT's
                # yfinance-sourced 16:00 1h bar for 2026-09-08).
                #
                # Scoped to intraday intervals ("1m".."90m") — daily+
                # intervals ("1d"/"5d"/"1wk"/"1mo"/"3mo") don't have a
                # live-candle concept and their epoch timestamps aren't
                # meaningfully "boundary-aligned" the same way, so this
                # check would misfire on real daily bars.
                if interval.endswith("m") and i == len(ts_arr) - 1:
                    ts = datetime.fromtimestamp(int(ts_arr[i]), tz=UTC)
                    is_flat_zero_volume = (
                        opens[i] == highs[i] == lows[i] == closes[i]
                        and (volumes[i] or 0) == 0
                    )
                    if ts.second != 0 or is_flat_zero_volume:
                        continue
                bars.append(self._bar_from_chart_data(
                    symbol, i, ts_arr, opens, highs, lows, closes, volumes,
                    timeframe, DataStatus.HISTORICAL, self.name,
                ))
            self._reset_error_state()
            return bars
        except Exception as e:
            self._handle_error(e, f"Failed to get historical bars for {symbol} ({timeframe}, {range_})")
            raise

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        # Use the quote endpoint for batch (needs a cookie + crumb — see _get_auth).
        url = _QUOTE_URL
        try:
            r = self._batch_quote_response(symbols)
            if r.status_code != 200:
                raise RuntimeError(
                    f"Yahoo Finance HTTP {r.status_code} for batch quote: {r.text[:200]}"
                )
            data = safe_json(r.text, url=url)
            quote_data = data.get("quoteResponse", {}).get("result", [])
            # Build a map from symbol to quote data
            quote_map = {item["symbol"]: item for item in quote_data}
            results = {}
            for symbol in symbols:
                symbol_upper = symbol.upper()
                if symbol_upper in quote_map:
                    item = quote_map[symbol_upper]
                    # Build Quote object
                    quote = Quote(
                        symbol=symbol_upper,
                        price=float(item.get("regularMarketPrice", 0.0)),
                        timestamp=to_ny(datetime.fromtimestamp(item.get("regularMarketTime", 0), UTC)),
                        provider=self.name,
                        data_status=DataStatus.DELAYED,
                        bid=item.get("bid"),
                        ask=item.get("ask"),
                        volume=item.get("regularMarketVolume"),
                    )
                    results[symbol] = quote
                else:
                    # Symbol not found in response
                    results[symbol] = Quote(
                        symbol=symbol_upper,
                        price=0.0,
                        timestamp=to_ny(datetime.now(UTC)),
                        provider=self.name,
                        data_status=DataStatus.ERROR,
                    )
            self._reset_error_state()
            return results
        except Exception as e:
            self._handle_error(e, f"Failed to get batch quotes for {symbols}")
            raise

    async def get_batch_historical_bars(
        self,
        symbols: list[str],
        timeframe: str = "1d",
        range_: str = "3mo",
    ) -> dict[str, list[Bar]]:
        """Fetch historical bars for multiple symbols in parallel.
        Returns a dict mapping symbol to list of bars (oldest -> newest).
        """
        if not symbols:
            return {}

        # Concurrency cap restored: Yahoo rate-limits aggressive
        # parallel chart fetches (the old sync path used a 20-thread
        # pool for exactly this reason) — an uncapped gather over the
        # whole universe gets us 429s.
        sem = asyncio.Semaphore(20)

        async def _fetch_one(symbol: str) -> tuple[str, list[Bar]]:
            try:
                async with sem:
                    bars = await asyncio.to_thread(
                        self.get_historical_bars, symbol, timeframe=timeframe, range_=range_
                    )
                return (symbol, bars)
            except Exception:
                return (symbol, [])

        tasks = [_fetch_one(s) for s in symbols]
        results_list = await asyncio.gather(*tasks)

        return dict(results_list)

    def get_market_status(self, symbol: str) -> MarketStatus:
        try:
            chart = self._fetch_chart(symbol, interval="1d", range_="1d")
            meta = self._meta(chart)
            now = datetime.now(UTC)
            is_open = bool(meta.get("marketState") in ("REGULAR", "PRE", "POST"))
            status = MarketStatus(
                symbol=symbol.upper(),
                is_open=is_open,
                next_open=None,
                next_close=None,
                timezone=meta.get("exchangeTimezoneShortName", "UTC"),
                provider=self.name,
                timestamp=to_ny(now),
            )
            self._reset_error_state()
            return status
        except Exception as e:
            self._handle_error(e, f"Failed to get market status for {symbol}")
            raise

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name=self.name,
            supports_historical_bars=True,
            supports_latest_quote=True,
            supports_latest_bar=True,
            supports_batch_quotes=True,
            supports_market_status=True,
            min_timeframe="1m",
            max_timeframe="3mo",
        )

    def is_available(self) -> bool:
        try:
            q = self.get_quote("AAPL")
            return q.price > 0
        except Exception:
            return False
