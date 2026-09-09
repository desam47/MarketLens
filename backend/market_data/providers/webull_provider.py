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

Timestamp convention
-------------------
All naive datetime values stored in the DB are NY local (EDT/EST).
``_epoch_ms_to_ny()`` converts UTC timestamps to naive NY via ``to_ny()``
before they reach the data layer. This matches the Alpaca provider's
``_ts_to_ny()`` and the ``backend.utils.timezone`` convention.
"""
from __future__ import annotations

import logging
from pathlib import Path

# Redirect the webull SDK's default log file from the CWD-relative
# ``webull_trade_sdk.log`` to an absolute path inside the project tree.
# The SDK's ``set_file_logger`` is called at runtime (not at import time),
# so patching it here — before the SDK classes are ever instantiated —
# reliably redirects all SDK logging to the project without touching site-packages.
_WEBULL_LOG = Path(__file__).resolve().parents[3] / "logs" / "webull_trade_sdk.log"
_WEBULL_LOG.parent.mkdir(exist_ok=True)

import webull.core.client as _wb_client  # noqa: E402

_orig_set_file_logger = _wb_client.ApiClient.set_file_logger

def _patched_set_file_logger(
    self,
    path,
    log_level=logging.DEBUG,
    logger_name="webull.core",
    format_string=None,
    when="H",
    interval=1,
    backup_count=72,
):
    # Replace a bare filename with the project-local absolute path so the
    # log file is always created inside the project, regardless of CWD.
    abs_path = str(_WEBULL_LOG) if Path(path).name == path else path
    return _orig_set_file_logger(
        self, abs_path, log_level, logger_name, format_string, when, interval, backup_count
    )


_wb_client.ApiClient.set_file_logger = _patched_set_file_logger

import logging
from datetime import datetime, timedelta, timezone
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
from backend.engines.market_calendar import classify_bar_session as _classify_bar_session
from backend.utils.timezone import to_ny, NY as _NY_TZ

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
    "4h":  "M60",   # Webull's 60-minute bar, combined from 4×15m
    "1d":  "D",
    "1wk": "W",
    "1mo": "D",
}

# Phase 3.1: per-timeframe bar counts per trading day. Used to compute
# how many bars to request for a given (range_, timeframe) pair so
# 1m fetches pull enough rows. Webull only serves ~30 days of 1m
# history, so callers should not request 1m beyond 1mo.
_BARS_PER_DAY: dict[str, int] = {
    "1m":   390,    # 6.5h × 60
    "2m":   195,    # 6.5h × 30
    "3m":   130,    # 6.5h × 20
    "5m":   78,     # 6.5h × 12
    "15m":  26,     # 6.5h × 4
    "30m":  13,     # 6.5h × 2
    "60m":  7,      # 6.5h / 1h
    "1h":   7,
    "4h":   2,      # 6.5h / 4h
    "1d":   1,
    "1wk":  1,      # only one weekly bar per day
    "1mo":  1,
}

# Trading days per range_ string.
_RANGE_DAYS: dict[str, int] = {
    "1d":   1,
    "5d":   5,
    "15d":  15,     # Phase 3.9: 15 trading days ≈ 4 pages of M1
    "1mo":  22,     # ~22 trading days per month
    "3mo":  65,
    "6mo":  130,
    "1y":   252,
    "2y":   504,
    "5y":   1260,
    "15m":  1,      # Phase 3.8: live ingestion recent-window fetch
    "3h":   1,      # Phase 3.8: 1h recent-window fetch (1 trading day)
}

# Map our ``range_`` to an approximate bar count so the SDK's count param
# covers the requested window.  These are conservative (more bars than needed
# is fine; fewer is not).
_RANGE_TO_COUNT: dict[str, int] = {
    "1d":  1,
    "5d":  5,
    "15d": 15,
    "1mo": 22,
    "3mo": 65,
    "6mo": 130,
    "1y":  252,
    "2y":  504,
    "5y":  1260,
    "15m": 30,      # Phase 3.8: 30 × 1m bars (15-min lookback, doubled for safety)
    "3h":  30,      # Phase 3.8: 30 × 1h bars (3-hour lookback, conservative)
}


def _epoch_ms_to_ny(ms: int | str | float | None) -> datetime:
    """Convert a Webull timestamp to a naive NY datetime.

    The Webull SDK returns timestamps as either:
      * an integer / float of epoch milliseconds (older code paths), or
      * an ISO 8601 string like ``"2026-09-01T18:46:00.000+0000"`` (current).

    Both shapes must be handled; otherwise every bar in a batch ends up
    stamped with ``datetime.now(...)`` and downstream median-gap detection
    sees zero-second gaps (3.1.22 fails to catch the resulting M1→tick
    downgrade).

    Convention: naive datetimes are always NY local (EDT/EST). This matches
    ``backend.utils.timezone`` and the Alpaca provider's ``_ts_to_ny()``.
    """
    if ms is None:
        return to_ny(datetime.now(timezone.utc))

    # Numeric path: epoch milliseconds.
    if isinstance(ms, (int, float)):
        try:
            return to_ny(
                datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)
            )
        except (TypeError, ValueError, OSError):
            return to_ny(datetime.now(timezone.utc))

    # String path: ISO 8601 with trailing ``+0000`` or ``+00:00`` (Webull
    # omits the colon in the UTC offset). ``fromisoformat`` pre-3.11 does
    # not understand the ``+0000`` form, so normalize first.
    text = str(ms).strip()
    if text.endswith("+0000"):
        text = text[:-5] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return to_ny(datetime.now(timezone.utc))

    # Normalise to UTC-aware, then convert to naive NY.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return to_ny(dt.astimezone(timezone.utc))


def _extended_hours_quote_fields(field: dict) -> dict:
    """Extract Webull's ``extend_hour_*`` snapshot fields into Quote kwargs.

    Confirmed live 2026-09-09 via ``get_snapshot(..., extend_hour_required=True)``:
    field names are ``extend_hour_last_price/high/low/volume/change/
    change_ratio/last_trade_time``. All are absent (not just null) when
    there's no premarket/after-hours activity to report — ``.get()``
    returns None for every key in that case, which is the correct value
    for Quote's ``extended_hours_*`` fields (all Optional, default None).
    """
    last_price = field.get("extend_hour_last_price")
    return {
        "extended_hours_price": float(last_price) if last_price not in (None, "") else None,
        "extended_hours_change": (
            float(field["extend_hour_change"]) if field.get("extend_hour_change") not in (None, "") else None
        ),
        "extended_hours_change_ratio": (
            float(field["extend_hour_change_ratio"])
            if field.get("extend_hour_change_ratio") not in (None, "") else None
        ),
        "extended_hours_high": (
            float(field["extend_hour_high"]) if field.get("extend_hour_high") not in (None, "") else None
        ),
        "extended_hours_low": (
            float(field["extend_hour_low"]) if field.get("extend_hour_low") not in (None, "") else None
        ),
        "extended_hours_volume": (
            int(float(field["extend_hour_volume"])) if field.get("extend_hour_volume") not in (None, "") else None
        ),
        "extended_hours_timestamp": (
            _epoch_ms_to_ny(field["extend_hour_last_trade_time"])
            if field.get("extend_hour_last_trade_time") not in (None, "") else None
        ),
    }


def _classify_session(ts: datetime) -> str:
    """Classify a naive-NY bar timestamp — see market_calendar.classify_bar_session.

    Kept as a thin local alias (rather than inlining the import at every
    call site in this file) since this module already imports
    ``us_market_calendar``/``SessionType`` for other reasons. NOTE: this
    is now only a convenience wrapper — bar_repository.upsert_bars
    unconditionally recomputes session for 1m bars at write time via the
    same shared function, so what this returns is a best-effort initial
    value, not the final authority (see upsert_bars' docstring for why:
    not every provider that can produce a 1m bar tags it correctly).
    """
    return _classify_bar_session(ts)


# Reverse map: Webull SDK timespan → our canonical timeframe label.
# Used to detect when Webull returns a coarser resolution than requested
# (free-tier accounts may downsample 1m → 5m regardless of M1 request).
_TIMESPAN_TO_TIMEFRAME: dict[str, str] = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "M60": "1h", "D": "1d", "W": "1wk",
}


def _infer_actual_timeframe(timespan: str, bars: list) -> str:
    """Infer the actual resolution Webull returned, falling back to the
    requested timespan when the data is consistent with it.

    Webull's free tier sometimes silently downgrades M1 to M5. We detect
    that by computing the median gap between consecutive bar timestamps
    and snapping to the nearest standard timeframe.

    Returns a canonical timeframe string ("1m", "5m", ...).
    """
    if len(bars) < 2:
        return _TIMESPAN_TO_TIMEFRAME.get(timespan, timespan)

    timestamps = sorted(bars)  # in-place sort just in case
    gaps = [
        (timestamps[i + 1] - timestamps[i]).total_seconds()
        for i in range(len(timestamps) - 1)
    ]
    # Use the median to avoid being skewed by intra-day session gaps.
    gaps_sorted = sorted(gaps)
    median_gap = gaps_sorted[len(gaps_sorted) // 2]

    # Snap to the nearest standard bar duration. 1m is preferred up to 90s,
    # 5m up to 6 min, etc. (Allows some jitter from session boundaries.)
    if median_gap <= 90:
        return "1m"
    if median_gap <= 360:
        return "5m"
    if median_gap <= 1080:
        return "15m"
    if median_gap <= 2160:
        return "30m"
    if median_gap <= 4500:
        return "1h"
    if median_gap <= 90000:
        return "1d"
    return "1wk"


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
        # The ApiClient is already imported at module load (to install our
        # log-path patch); only the trade/data clients are imported lazily.
        from webull.trade.trade_client import TradeClient as _TradeClient
        from webull.data.data_client import DataClient as _DataClient

        # Determine sandbox vs production.
        use_sandbox = _settings.webull.use_sandbox
        region = "us"  # sandbox/production are both under the US region endpoint

        self._api_client = _wb_client.ApiClient(app_key, app_secret, region)
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
            # extend_hour_required=True is always safe to request — confirmed
            # live 2026-09-09: no extra permission needed (unlike
            # overnight_required, which 403s without a separate Webull
            # subscription). Adds extend_hour_* fields when there's
            # premarket/after-hours activity; absent otherwise.
            resp = self._data_client.market_data.get_snapshot(
                sym, "US_STOCK", extend_hour_required=True
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Webull snapshot HTTP {resp.status_code}")
            data = resp.json()
            if not isinstance(data, list) or not data:
                raise RuntimeError(f"Webull returned empty snapshot for {sym}")
            field = data[0]

            ts = _epoch_ms_to_ny(field.get("quote_time") or field.get("last_trade_time"))

            quote = Quote(
                symbol=sym,
                price=float(field.get("price") or 0),
                timestamp=ts,
                provider=self.name,
                data_status=DataStatus.DELAYED,
                bid=float(field["bid"]) if field.get("bid") else None,
                ask=float(field["ask"]) if field.get("ask") else None,
                volume=int(field.get("volume") or 0) if field.get("volume") else None,
                **_extended_hours_quote_fields(field),
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
            resp = self._data_client.market_data.get_snapshot(
                sym_str, "US_STOCK", extend_hour_required=True
            )
            results: dict[str, Quote] = {}
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for field in data:
                        sym = field.get("symbol", "").upper()
                        ts = _epoch_ms_to_ny(
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
                            **_extended_hours_quote_fields(field),
                        )
            # Return empty entries for any missing symbols.
            for sym in symbols:
                sym_upper = sym.upper()
                if sym_upper not in results:
                    results[sym_upper] = Quote(
                        symbol=sym_upper,
                        price=0.0,
                        timestamp=to_ny(datetime.now(timezone.utc)),
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
                    timestamp=to_ny(datetime.now(timezone.utc)),
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
        include_extended_hours: bool = False,
        _start_ts: datetime | None = None,
        _end_ts: datetime | None = None,
    ) -> list[Bar]:
        """Fetch historical bars for ``symbol``.

        Supports optional date-window via ``_start_ts`` / ``_end_ts`` (both naive
        UTC datetimes). When ``_end_ts`` is set, the request uses
        ``start_time``/``end_time`` params so Webull returns bars within that
        window instead of the most-recent N bars.

        For 1m bars, when the requested range exceeds Webull's 1,200-bar cap
        (~3 trading days), multiple pages are fetched automatically (newest→oldest)
        and merged. For example, ``range_="15d"`` → ~5,850 bars → 5 pages.

        ``include_extended_hours``: when True and ``timeframe == "1m"``, also
        request pre-market and after-hours bars via the SDK's
        ``trading_sessions`` param (confirmed live 2026-09-09: without this,
        Webull's default is regular-trading-hours-only — passing
        ``trading_sessions=["PRE","RTH","ATH"]`` correctly returns all three
        sessions merged, chronologically ordered). Ignored for any other
        timeframe — extended-hours coverage is only meaningful at 1m
        resolution in this pipeline. Each returned ``Bar.session`` reflects
        its real classification either way (see ``_parse_bars``).
        """
        sym = symbol.upper()
        try:
            timespan = _TIMEFRAME_TO_TIMESPAN.get(timeframe, "D")
            # Phase 3.1: for 1m, compute count from _BARS_PER_DAY × days in range.
            # This ensures "1mo" fetches ~22 trading days × 390 bars = ~8580 bars.
            ext_hours = include_extended_hours and timeframe == "1m"
            if timeframe == "1m":
                days_per_range = _RANGE_DAYS.get(range_, 65)
                count = days_per_range * _BARS_PER_DAY["1m"]
                if ext_hours:
                    # PRE (5.5h) + RTH (6.5h) + ATH (4h) = 16h vs RTH-only's
                    # 6.5h — scale the per-day bar budget accordingly so a
                    # multi-day range still requests enough bars to cover
                    # all three sessions instead of being capped mid-day.
                    count = count * 16 // 7
                count = min(count, 1200)  # Webull M1 API limit (official cap)
            else:
                count = _RANGE_TO_COUNT.get(range_, 200)
                count = min(count, 1200)  # Webull D/H API limit

            trading_sessions = ["PRE", "RTH", "ATH"] if ext_hours else None

            # For 1m with date-window or multi-page range, use pagination.
            # 3 trading days × 390 bars/day = 1,170 < 1,200 cap → 4 days triggers paginator.
            if timeframe == "1m" and (_end_ts is not None or days_per_range > 3 or ext_hours):
                return self._fetch_1m_paginated(
                    sym, timespan, count, range_, _start_ts, _end_ts,
                    trading_sessions=trading_sessions,
                )

            resp = self._data_client.market_data.get_history_bar(
                sym, "US_STOCK", timespan, count=str(count),
                trading_sessions=trading_sessions,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Webull bars HTTP {resp.status_code}")
            data = resp.json()
            if not isinstance(data, list):
                self._reset_error_state()
                return []

            bars = self._parse_bars(data, sym, timeframe)

            return bars
        except Exception as e:
            self._handle_error(e, f"get_historical_bars({sym})")
            raise

    def _fetch_1m_paginated(
        self,
        sym: str,
        timespan: str,
        count: int,
        range_: str,
        _start_ts: datetime | None,
        _end_ts: datetime | None,
        trading_sessions: list[str] | None = None,
    ) -> list[Bar]:
        """Fetch 1m bars using multiple pages when the window exceeds 1,200 bars.

        Webull's M1 endpoint returns at most 1,200 bars per call (per the
        official SDK docstring: "the maximum limit is 1200"). For windows
        requiring more bars (e.g. 15 trading days ≈ 5,850 bars), we fetch
        backward in time: each page returns the most-recent 1,200 bars up to
        the current ``end_time``, then we move ``end_time`` to 1 ms before
        the oldest bar's timestamp and repeat.

        NOTE: Webull's M1 endpoint does NOT respond correctly to the
        ``start_time`` query parameter (it returns 0 bars whenever start_time
        is set). We therefore use ``end_time`` ONLY and rely on the oldest
        bar in each response as the new end_time anchor for the next page.

        Deduplication by timestamp handles the 1-bar overlap between pages.

        Each page costs 1 API call and is subject to the 100 req/min rate
        limit. 10 pages = ~16 trading days.
        """
        now = datetime.now(timezone.utc)
        end_ts = _end_ts if _end_ts is not None else now
        start_ts = _start_ts  # informational only; not passed to Webull

        # How many total bars do we need?
        days_per_range = _RANGE_DAYS.get(range_, 65)
        target_bars = days_per_range * _BARS_PER_DAY["1m"]
        if trading_sessions:
            # PRE+RTH+ATH = 16h/day vs RTH-only's 6.5h — same scaling as
            # get_historical_bars so pagination requests enough bars to
            # actually cover all three sessions per day.
            target_bars = target_bars * 16 // 7
        # Each page returns up to 1,200 bars (≈ 3 trading days).
        BARS_PER_PAGE_CAP = 1200
        bars_per_page = min(target_bars, BARS_PER_PAGE_CAP)
        num_pages = max(1, (target_bars + bars_per_page - 1) // bars_per_page)
        # Cap at 10 pages to avoid runaway loops (~16 trading days max).
        num_pages = min(num_pages, 10)

        logger.info(
            f"Webull 1m pagination: {sym} range_={range_} → {target_bars} target bars, "
            f"{num_pages} pages × {bars_per_page}"
        )

        all_raw: list[dict] = []

        for page_idx in range(num_pages):
            if end_ts is None or (start_ts is not None and end_ts <= start_ts):
                break

            # end_ts is naive NY (from _epoch_ms_to_ny). `.astimezone()` on a
            # naive datetime assumes the *system* local timezone, not NY — stamp
            # it NY-aware first (matching ny_to_utc()'s convention) before
            # converting to UTC.
            end_ts_aware = end_ts.replace(tzinfo=_NY_TZ) if end_ts.tzinfo is None else end_ts
            page_end_ms = int(end_ts_aware.astimezone(timezone.utc).timestamp() * 1000)

            # NOTE: do NOT set start_time — Webull's M1 endpoint returns 0 bars
            # whenever start_time is present in the query string.
            resp = self._data_client.market_data.get_history_bar(
                sym, "US_STOCK", timespan,
                count=str(bars_per_page),
                end_time=str(page_end_ms),
                trading_sessions=trading_sessions,
            )
            if resp.status_code != 200:
                logger.warning(
                    f"Webull 1m pagination: page {page_idx+1}/{num_pages} failed for {sym}: "
                    f"HTTP {resp.status_code}"
                )
                break

            data = resp.json()
            if not isinstance(data, list) or not data:
                logger.warning(
                    f"Webull 1m pagination: page {page_idx+1}/{num_pages} returned no data for {sym} "
                    f"— stopping"
                )
                break

            logger.info(
                f"Webull 1m pagination: page {page_idx+1}/{num_pages} for {sym} "
                f"returned {len(data)} bars (oldest={data[-1].get('time')})"
            )
            all_raw.extend(data)

            # Move the window: set end_ts to 1 ms before the oldest bar.
            oldest = data[-1].get("time")
            if oldest:
                oldest_ts = _epoch_ms_to_ny(oldest)
                end_ts = oldest_ts - timedelta(milliseconds=1)
            else:
                break

            self._reset_error_state()

        if not all_raw:
            return []

        # Deduplicate by timestamp.
        seen: set[int] = set()
        unique_raw: list[dict] = []
        for row in all_raw:
            ts = row.get("time")
            if ts and ts not in seen:
                seen.add(ts)
                unique_raw.append(row)

        bars = self._parse_bars(unique_raw, sym, "1m")
        return bars

    def _parse_bars(self, data: list[dict], sym: str, timeframe: str) -> list[Bar]:
        """Parse Webull JSON rows into Bar objects, newest-first → chronological."""
        bars: list[Bar] = []
        for row in data:
            ts = _epoch_ms_to_ny(row.get("time"))
            bars.append(Bar(
                symbol=sym,
                timestamp=ts,
                open=float(row.get("open") or 0),
                high=float(row.get("high") or 0),
                low=float(row.get("low") or 0),
                close=float(row.get("close") or 0),
                volume=int(row.get("volume") or 0) if row.get("volume") else 0,
                timeframe=timeframe,
                provider=self.name,
                data_status=DataStatus.HISTORICAL,
                session=_classify_session(ts),
            ))
        # Webull returns bars newest-first; sort chronologically (oldest→newest)
        # so callers (bar_repository, chart display) get predictable ordering.
        bars.sort(key=lambda b: b.timestamp)
        self._reset_error_state()

        # Webull's free tier silently downgrades M1 → M5. Detect that
        # from the actual inter-bar gaps and re-stamp each bar's
        # ``timeframe`` field so storage + signal recording reflect
        # the true resolution.
        timespan = _TIMEFRAME_TO_TIMESPAN.get(timeframe, "D")
        if bars and len(bars) >= 2:
            actual_tf = _infer_actual_timeframe(
                timespan,
                sorted(b.timestamp for b in bars),  # chronological
            )
            if actual_tf != timeframe:
                logger.debug(
                    f"Webull downgraded {sym} {timeframe} → {actual_tf} "
                    f"for {len(bars)} bars (free-tier behavior)"
                )
                for bar in bars:
                    bar.timeframe = actual_tf

        return bars

    def get_historical_bars_batch(
        self,
        symbols: list[str],
        timeframe: str = "1d",
        range_: str = "3mo",
        include_extended_hours: bool = False,
    ) -> dict[str, list["Bar"]]:
        """Fetch historical bars for multiple symbols in a single API call.

        Uses POST /market-data/stock/batch-bars which shares the same 60/min
        rate limit as the single-symbol GET — but batches up to 100 symbols
        per call, making it vastly more efficient for watchlist ingestion.
        Returns a dict mapping symbol → list of bars (oldest→newest).

        ``include_extended_hours``: see ``get_historical_bars`` — same
        PRE/RTH/ATH ``trading_sessions`` request, only meaningful for 1m.
        """
        if not symbols:
            return {}

        try:
            timespan = _TIMEFRAME_TO_TIMESPAN.get(timeframe, "D")
            ext_hours = include_extended_hours and timeframe == "1m"
            if timeframe == "1m":
                days_per_range = _RANGE_DAYS.get(range_, 65)
                count = days_per_range * _BARS_PER_DAY["1m"]
                if ext_hours:
                    count = count * 16 // 7
                # Webull M1 API limit (official cap) — matches get_historical_bars.
                count = min(count, 1200)
            else:
                count = min(_RANGE_TO_COUNT.get(range_, 200), 1200)

            trading_sessions = ["PRE", "RTH", "ATH"] if ext_hours else None

            sym_list = [s.upper() for s in symbols]
            resp = self._data_client.market_data.get_batch_history_bar(
                sym_list, "US_STOCK", timespan, count=str(count),
                trading_sessions=trading_sessions,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Webull batch bars HTTP {resp.status_code}")

            # Response shape (verified 2026-09-07):
            #   {"result": [{"symbol": "AAPL", "result": [{time,open,...}, ...]},
            #               {"symbol": "NVDA", "result": [{time,open,...}, ...]}]}
            # The SDK returns a list under the "result" key with per-symbol
            # entries. We index by symbol for stable ordering.
            data = resp.json()
            rows_by_symbol: dict[str, list[dict]] = {}
            if isinstance(data, dict) and isinstance(data.get("result"), list):
                for entry in data["result"]:
                    if not isinstance(entry, dict):
                        continue
                    sym = str(entry.get("symbol") or "").upper()
                    if not sym:
                        continue
                    rows_by_symbol[sym] = entry.get("result") or []
            elif isinstance(data, dict):
                # Older / alternative shape: {"AAPL": [...], "NVDA": [...]}
                rows_by_symbol = {k: v or [] for k, v in data.items() if isinstance(v, list)}
            else:
                self._reset_error_state()
                return {}

            result: dict[str, list["Bar"]] = {}
            for sym in sym_list:
                rows = rows_by_symbol.get(sym, [])
                bars: list["Bar"] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    row_ts = _epoch_ms_to_ny(row.get("time"))
                    bars.append(Bar(
                        symbol=sym,
                        timestamp=row_ts,
                        open=float(row.get("open") or 0),
                        high=float(row.get("high") or 0),
                        low=float(row.get("low") or 0),
                        close=float(row.get("close") or 0),
                        volume=int(row.get("volume") or 0) if row.get("volume") else 0,
                        timeframe=timeframe,
                        provider=self.name,
                        data_status=DataStatus.HISTORICAL,
                        session=_classify_session(row_ts),
                    ))
                bars.sort(key=lambda b: b.timestamp)
                if bars and len(bars) >= 2:
                    actual_tf = _infer_actual_timeframe(
                        timespan,
                        [b.timestamp for b in bars],
                    )
                    if actual_tf != timeframe:
                        for bar in bars:
                            bar.timeframe = actual_tf
                result[sym] = bars

            self._reset_error_state()
            return result
        except Exception as e:
            self._handle_error(e, f"get_historical_bars_batch({symbols})")
            raise

    def get_latest_bar(self, symbol: str, timeframe: str = "1d") -> Bar:
        """Fetch the single most-recent bar.

        Phase 3.1: for ``timeframe == "1m"`` we must request a window
        wide enough to capture the most recent 1m bar (Webull returns
        bars oldest→newest; the last bar is the latest 1m). The
        ``range_="1d"`` argument fetches one day of 1m bars, which is
        sufficient to find the latest 1m bar. For higher timeframes the
        existing single-bar window still applies.
        """
        sym = symbol.upper()
        # 1m: pull 1 day of 1m bars; the last one is the latest.
        # 1d/1wk: one bar is enough.
        range_ = "1d"
        bars = self.get_historical_bars(sym, timeframe=timeframe, range_=range_)
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
                timestamp=to_ny(datetime.now(timezone.utc)),
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
                timestamp=to_ny(datetime.now(timezone.utc)),
            )
        except Exception as e:
            return ProviderStatus(
                provider_name=self.name,
                is_healthy=False,
                error_message=str(e),
                timestamp=to_ny(datetime.now(timezone.utc)),
            )


class WebullAuthError(Exception):
    """Raised when Webull SDK fails to bootstrap or credentials are invalid."""
