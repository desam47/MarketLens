"""
MarketDataManager - handles provider selection, fallback, caching, etc.
"""
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.config.settings import settings as _settings
from backend.models.market_data import (
    Bar,
    DataStatus,
    MarketStatus,
    ProviderStatus,
    Quote,
)

from ..provider import MarketDataProvider
from ..providers.yfinance_provider import YFinanceProvider

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Timeframes whose bars go stale within seconds rather than minutes/hours.
# Daily and weekly bars are inherently "old" at close of market — stale
# detection on them is deferred to a future enhancement.
_INTRADAY_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h"}


# Provider class registry: maps the symbolic name used in settings
# (MARKET_DATA_PRIMARY_PROVIDER / FALLBACK_PROVIDERS) to a concrete class.
# Adding a new provider is a one-line entry here; the manager picks it
# up from config on next init. Unknown names log a warning and are
# skipped — better than crashing the whole market-data subsystem.
_PROVIDER_CLASSES: dict[str, type[MarketDataProvider]] = {
    "yahoo_finance": YFinanceProvider,
    # Future providers go here, e.g.:
    # "alpha_vantage": AlphaVantageProvider,
    # "polygon": PolygonProvider,
}


class _PerProviderRateLimiter:
    """Sliding-window rate limiter keyed by provider name.

    Tracks call timestamps in a per-provider deque. Each call to
    ``acquire(provider_name)`` blocks until the provider is allowed
    another call under ``settings.market_data.rate_limit_per_minute``.

    Why a custom limiter instead of a library: this needs to be
    synchronous, cheap (no asyncio overhead — the manager is called
    from sync contexts as well), and per-provider. A 3rd-party
    library would add a dependency for what is essentially a deque
    plus a sleep loop.

    Thread-safe via a single lock; rate limiting is on the slow path
    so contention is irrelevant.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        # Stats for observability
        self._throttled_count: dict[str, int] = defaultdict(int)

    def acquire(self, provider_name: str, max_per_minute: int) -> None:
        """Block until one more call to ``provider_name`` is allowed.

        On the hot path (under the limit) this is a constant-time
        deque check. On the cold path (over the limit) it sleeps
        just long enough for the oldest in-window call to age out.
        """
        if max_per_minute <= 0:
            return  # disabled

        with self._lock:
            now = time.monotonic()
            window_start = now - 60.0
            calls = self._calls[provider_name]
            # Drop calls that have aged out of the 60s window.
            while calls and calls[0] < window_start:
                calls.popleft()
            if len(calls) < max_per_minute:
                calls.append(now)
                return
            # Over the limit — compute how long to wait.
            wait_seconds = (calls[0] + 60.0) - now
            self._throttled_count[provider_name] += 1

        # Sleep outside the lock so other providers aren't blocked
        # behind the slowest one. Worst case: we wake up and find the
        # window shifted; loop to re-check.
        if wait_seconds > 0:
            logger.debug(
                f"Rate-limited on {provider_name}: sleeping {wait_seconds:.2f}s "
                f"(limit {max_per_minute}/min)"
            )
            time.sleep(wait_seconds)
        # Re-acquire (recursive, but Python's recursion limit is far
        # above the loop count we'd realistically hit).
        self.acquire(provider_name, max_per_minute)

    def stats(self) -> dict[str, int]:
        """Snapshot of throttled-call counts per provider (for health endpoint)."""
        with self._lock:
            return dict(self._throttled_count)


# Singleton limiter shared across manager calls.
_rate_limiter = _PerProviderRateLimiter()


def _provider_retry():
    """Tenacity retry policy for a single provider call.

    Three attempts with exponential backoff (0.5s → 1s → 2s, capped at 8s).
    Any exception is treated as transient — if a provider really has a
    fatal issue, three quick attempts are enough to surface it and the
    manager falls through to the next provider.
    """
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        reraise=True,
        retry=retry_if_exception_type(Exception),
    )


@_provider_retry()
def _call_provider(provider: MarketDataProvider, method_name: str, *args, **kwargs) -> Any:
    """Invoke a provider method with retry/backoff.

    The retry is applied here (per-call) rather than at the manager level so
    each provider in the priority list gets its own three-attempt budget
    before the manager falls through to the next one.

    Rate limiting (per ``settings.market_data.rate_limit_per_minute``) is
    checked before every call so bursts are throttled even if individual
    calls succeed quickly.
    """
    limit = _settings.market_data.rate_limit_per_minute
    _rate_limiter.acquire(provider.name, limit)
    method: Callable = getattr(provider, method_name)
    return method(*args, **kwargs)


def _newest_bar_age_seconds(bars: list[Bar]) -> float | None:
    """Age of the newest bar in seconds, or None if no bars."""
    if not bars:
        return None
    newest = max(bar.timestamp for bar in bars)
    return (datetime.now() - newest).total_seconds()


# Approximate bar counts to expect for a given (timeframe, range) tuple.
# Used to decide whether the cache is "good enough" to return without
# hitting a provider. Values are deliberately generous; if a cache returns
# at least 80% of the expected count, treat it as sufficient.
_EXPECTED_BAR_COUNTS: dict[tuple[str, str], int] = {
    ("1d", "1mo"): 22,
    ("1d", "3mo"): 65,
    ("1d", "6mo"): 130,
    ("1d", "1y"): 252,
    ("1d", "2y"): 504,
    ("1d", "5y"): 1260,
    ("1h", "1mo"): 200,
    ("1h", "3mo"): 600,
    ("1h", "1y"): 1900,
    ("5m", "1d"): 80,
    ("5m", "5d"): 400,
    ("15m", "5d"): 130,
    ("30m", "5d"): 65,
    ("1m", "1d"): 390,
    ("1m", "5d"): 1950,
    ("1wk", "2y"): 104,
    ("1wk", "5y"): 260,
    ("1mo", "5y"): 60,
}


class MarketDataManager:
    """Manages market data providers with fallback and caching"""

    def __init__(self):
        self.providers: dict[str, MarketDataProvider] = {}
        self.provider_priority: list[str] = []  # List of provider names in priority order
        self.provider_priorities: dict[str, int] = {}  # Map of provider_name -> priority
        self._initialize_providers()

    def _initialize_providers(self):
        """Initialize providers from settings (primary + fallbacks).

        Reads ``settings.market_data.primary_provider`` and
        ``settings.market_data.fallback_providers`` and instantiates each
        one via ``_PROVIDER_CLASSES``. Unknown names log a warning and are
        skipped so a misconfigured ``MARKET_DATA_PRIMARY_PROVIDER=foo``
        doesn't crash the whole manager.

        Priority is the index in the [primary, *fallbacks] list — primary
        is priority 0, the first fallback is priority 1, etc. The
        settings are the single source of truth; this method no longer
        hardcodes yfinance.
        """
        primary = _settings.market_data.primary_provider
        fallbacks = list(_settings.market_data.fallback_providers)
        ordered = [primary] + [f for f in fallbacks if f != primary]
        for priority, name in enumerate(ordered):
            cls = _PROVIDER_CLASSES.get(name)
            if cls is None:
                logger.warning(
                    f"Provider '{name}' is configured but not registered in "
                    f"_PROVIDER_CLASSES — skipping. Known: {list(_PROVIDER_CLASSES)}"
                )
                continue
            try:
                self.add_provider(cls(), priority=priority)
            except Exception as e:
                logger.warning(f"Failed to instantiate provider '{name}': {e}")

    def add_provider(self, provider: MarketDataProvider, priority: int = 0):
        """Add a provider to the manager"""
        self.providers[provider.name] = provider
        # Insert provider into priority list based on priority value
        self.provider_priorities[provider.name] = priority
        # Re-sort provider_priority based on priorities
        self.provider_priority = sorted(self.provider_priorities.keys(), key=lambda x: self.provider_priorities[x])

        logger.info(f"Added provider '{provider.name}' with priority {priority}")

    def remove_provider(self, provider_name: str):
        """Remove a provider from the manager"""
        if provider_name in self.providers:
            del self.providers[provider_name]
            if provider_name in self.provider_priorities:
                del self.provider_priorities[provider_name]
                # Re-sort the provider_priority list
                self.provider_priority = sorted(self.provider_priorities.keys(), key=lambda x: self.provider_priorities[x])
            logger.info(f"Removed provider '{provider_name}'")

    def _get_available_providers(self) -> list[str]:
        """Get list of available providers in priority order"""
        available = []
        for provider_name in self.provider_priority:
            provider = self.providers.get(provider_name)
            if provider and provider.is_available():
                available.append(provider_name)
        return available

    def get_quote(self, symbol: str) -> Quote:
        """Get quote for a symbol with fallback.

        Each provider gets up to 3 attempts (with exponential backoff) before
        the manager falls through to the next provider in priority order.
        """
        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                quote = _call_provider(provider, "get_quote", symbol)
                logger.debug(f"Got quote for {symbol} from {provider_name}")
                return quote
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get quote for {symbol} from {provider_name}: {e}")
                continue

        # If all providers failed, raise the last error
        if last_error:
            raise last_error
        else:
            raise RuntimeError("No available providers")

    def get_bar(self, symbol: str, timeframe: str, timestamp: datetime) -> Bar:
        """Get historical bar for a symbol with fallback."""
        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                bar = _call_provider(provider, "get_bar", symbol, timeframe, timestamp)
                logger.debug(f"Got bar for {symbol} from {provider_name}")
                return bar
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get bar for {symbol} from {provider_name}: {e}")
                continue

        if last_error:
            raise last_error
        else:
            raise RuntimeError("No available providers")

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar:
        """Get latest bar for a symbol with fallback."""
        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                bar = _call_provider(provider, "get_latest_bar", symbol, timeframe)
                logger.debug(f"Got latest bar for {symbol} from {provider_name}")
                return bar
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get latest bar for {symbol} from {provider_name}: {e}")
                continue

        if last_error:
            raise last_error
        else:
            raise RuntimeError("No available providers")

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        range_: str = "3mo",
        use_cache: bool = True,
        db: "Session | None" = None,
    ) -> list[Bar]:
        """Get a series of historical bars for a symbol, with optional caching.

        Cache validity is determined by **two** independent checks, both of
        which must pass for the cache to be used:

        1. **Count threshold** — the cache must hold at least 80% of the
           expected bar count for ``(timeframe, range_)`` (or any positive
           count if no expectation is recorded).
        2. **Age threshold** — for intraday timeframes the newest cached
           bar must be no older than ``settings.market_data.cache_ttl_seconds``.
           Daily/weekly bars are inherently "old" at close of market, so
           the age check is skipped for them.

        If both checks pass the cached bars are returned as-is. If the count
        check passes but the age check fails (intraday only), the cached
        bars are returned with ``data_status = DataStatus.STALE`` so callers
        can show a freshness indicator while still rendering the data.

        Never raises; returns ``[]`` on failure so callers (e.g. the
        scanner) can treat "no history" as a benign state.
        """
        expected = _EXPECTED_BAR_COUNTS.get((timeframe, range_), 0)
        cache_threshold = int(expected * 0.8) if expected else 0

        if use_cache and db is not None:
            try:
                # Local import keeps the manager importable without
                # requiring SQLAlchemy to be configured at import time.
                from backend.repositories import bar_repository

                cached = bar_repository.get_bars(
                    db, symbol=symbol, timeframe=timeframe, limit=None
                )
                if cached and (
                    cache_threshold == 0 or len(cached) >= cache_threshold
                ):
                    age = _newest_bar_age_seconds(cached)
                    ttl = _settings.market_data.cache_ttl_seconds
                    is_intraday = timeframe in _INTRADAY_TIMEFRAMES
                    if age is not None and is_intraday and age > ttl:
                        # Count threshold met but cache is too old — return
                        # the data anyway, tagged STALE so the UI can warn.
                        age_s = f"{age:.1f}s"
                        logger.warning(
                            f"Serving stale bars for {symbol} {timeframe}: "
                            f"newest bar is {age_s} old (TTL {ttl}s)"
                        )
                        return [
                            bar.model_copy(update={"data_status": DataStatus.STALE})
                            for bar in cached
                        ]
                    logger.debug(
                        f"Cache hit for {symbol} {timeframe} {range_}: "
                        f"{len(cached)} bars (threshold {cache_threshold}, "
                        f"age {age:.1f}s)"
                    )
                    return cached
            except Exception as e:
                logger.warning(f"Bar cache lookup failed for {symbol}: {e}")

        # Cache miss (or cache disabled / unavailable) — fetch from provider.
        bars: list[Bar] = []
        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                bars = _call_provider(
                    provider, "get_historical_bars", symbol,
                    timeframe=timeframe, range_=range_
                )
                logger.debug(
                    f"Got {len(bars)} historical bars for {symbol} "
                    f"({timeframe}, {range_}) from {provider_name}"
                )
                break
            except Exception as e:
                logger.warning(
                    f"Failed to get historical bars for {symbol} from "
                    f"{provider_name}: {e}"
                )
                continue

        if not bars:
            return []

        if db is not None:
            try:
                from backend.repositories import bar_repository
                written = bar_repository.upsert_bars(db, bars)
                logger.debug(f"Persisted {written} bars for {symbol} {timeframe}")
            except Exception as e:
                logger.warning(
                    f"Failed to persist bars for {symbol} {timeframe}: {e}"
                )

        return bars

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Get quotes for multiple symbols with fallback"""
        # For batch quotes, we'll try to use providers that support it efficiently
        # For now, we'll fall back to individual quotes
        quotes = {}
        errors = []

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                batch_quotes = provider.get_batch_quotes(symbols)
                # Only use the result if we got quotes for all requested symbols
                if all(symbol in batch_quotes and batch_quotes[symbol].data_status != "ERROR"
                       for symbol in symbols):
                    quotes = batch_quotes
                    logger.debug(f"Got batch quotes for {symbols} from {provider_name}")
                    break
                else:
                    # Partial success, but we'll try other providers for complete data
                    logger.warning(f"Partial batch quote success from {provider_name}")
            except Exception as e:
                errors.append(f"{provider_name}: {e}")
                logger.warning(f"Failed to get batch quotes for {symbols} from {provider_name}: {e}")
                continue

        # If we didn't get complete data from any provider, fall back to individual quotes
        if not all(symbol in quotes and quotes[symbol].data_status != "ERROR" for symbol in symbols):
            logger.info("Falling back to individual quote requests")
            quotes = {}
            for symbol in symbols:
                try:
                    quotes[symbol] = self.get_quote(symbol)
                except Exception as e:
                    logger.error(f"Failed to get quote for {symbol}: {e}")
                    quotes[symbol] = Quote(
                        symbol=symbol.upper(),
                        price=0.0,
                        timestamp=datetime.now(),
                        provider="fallback_failed",
                        data_status="ERROR"
                    )

        return quotes

    def get_market_status(self, symbol: str) -> MarketStatus:
        """Get market status for a symbol with fallback"""
        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                status = _call_provider(provider, "get_market_status", symbol)
                logger.debug(f"Got market status for {symbol} from {provider_name}")
                return status
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to get market status for {symbol} from {provider_name}: {e}")
                continue

        if last_error:
            raise last_error
        else:
            raise RuntimeError("No available providers")

    def get_provider_statuses(self) -> dict[str, ProviderStatus]:
        """Get status of all providers"""
        statuses = {}
        for name, provider in self.providers.items():
            try:
                statuses[name] = provider.get_provider_status()
            except Exception as e:
                logger.error(f"Failed to get status for provider {name}: {e}")
                statuses[name] = ProviderStatus(
                    provider_name=name,
                    is_healthy=False,
                    error_message=str(e),
                    timestamp=datetime.now()
                )
        return statuses

# Global instance
market_data_manager = MarketDataManager()
