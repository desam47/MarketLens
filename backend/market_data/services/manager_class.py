"""
MarketDataManager class — orchestrates providers, cache, and fallback.

The class itself is in its own module so the long method bodies
(get_historical_bars, get_batch_quotes, etc.) don't drown out the
provider/caching/circuit-breaking infrastructure that lives next to it.

``_settings`` is imported from ``_providers`` so test patches at
``backend.market_data.services.manager._settings`` propagate here.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from backend.models.market_data import (
    Bar,
    DataStatus,
    MarketStatus,
    ProviderStatus,
    Quote,
)

from ..circuit_breaker import CircuitState
from ..provider import MarketDataProvider
from ._providers import get_redis_cache, get_settings
from .cache import get_bar_cache_ttl
from .providers import (
    _EXPECTED_BAR_COUNTS,
    _INTRADAY_TIMEFRAMES,
    _PROVIDER_CLASSES,
    _call_provider,
    _call_provider_direct,
    _circuit_breakers,
    _newest_bar_age_seconds,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class MarketDataManager:
    """Manages market data providers with fallback and caching"""

    def __init__(self):
        self.providers: dict[str, MarketDataProvider] = {}
        self.provider_priority: list[str] = []  # List of provider names in priority order
        self.provider_priorities: dict[str, int] = {}  # Map of provider_name -> priority
        self._initialize_providers()

        # Cache statistics
        self._cache_stats = {
            "bar_hits": 0,
            "bar_misses": 0,
            "quote_hits": 0,
            "quote_misses": 0,
        }

    def _initialize_providers(self):
        """Initialize providers from settings (primary + fallbacks).

        Reads ``settings.market_data.primary_provider`` and
        ``settings.market_data.fallback_providers`` and instantiates each
        one via ``_PROVIDER_CLASSES``. Unknown names log a warning and are
        skipped so a misconfigured ``MARKET_DATA_PRIMARY_PROVIDER=foo``
        doesn't crash the whole manager.

        Priority is the index in the [primary, *fallbacks] list — primary
        is priority 0, the first fallback is priority 1, etc.
        """
        settings = get_settings()
        if settings.startup_mode == "api":
            logger.info("Market-data provider initialization skipped (STARTUP_MODE=api)")
            return

        primary = settings.market_data.primary_provider
        fallbacks = list(settings.market_data.fallback_providers)
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
        self.provider_priorities[provider.name] = priority
        self.provider_priority = sorted(
            self.provider_priorities.keys(), key=lambda x: self.provider_priorities[x]
        )
        logger.info(f"Added provider '{provider.name}' with priority {priority}")

    def remove_provider(self, provider_name: str):
        """Remove a provider from the manager"""
        if provider_name in self.providers:
            del self.providers[provider_name]
            if provider_name in self.provider_priorities:
                del self.provider_priorities[provider_name]
                self.provider_priority = sorted(
                    self.provider_priorities.keys(), key=lambda x: self.provider_priorities[x]
                )
            logger.info(f"Removed provider '{provider_name}'")

    def _get_available_providers(self) -> list[str]:
        """Get list of available providers in priority order.

        Providers with an OPEN or HALF_OPEN circuit breaker are skipped.
        """
        available = []
        for provider_name in self.provider_priority:
            provider = self.providers.get(provider_name)
            if not provider or not provider.is_available():
                continue
            breaker = _circuit_breakers.get(provider_name)
            if breaker is not None:
                breaker._check_and_transition()
                if breaker.get_state() in (
                    CircuitState.OPEN,
                    CircuitState.HALF_OPEN,
                ):
                    continue
            available.append(provider_name)
        return available

    def get_quote(self, symbol: str) -> Quote:
        """Get quote for a symbol with fallback."""
        cached_quote = get_redis_cache().get_quote(symbol)
        if cached_quote is not None:
            self._cache_stats["quote_hits"] += 1
            logger.debug(f"Redis cache hit for quote {symbol}")
            return cached_quote

        self._cache_stats["quote_misses"] += 1
        logger.debug(f"Redis cache miss for quote {symbol}")

        last_error = None
        failed_providers: list[str] = []
        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                quote = _call_provider(provider, "get_quote", symbol)
                logger.debug(f"Got quote for {symbol} from {provider_name}")
                if failed_providers:
                    try:
                        from backend.observability.provider_history import record_provider_event

                        record_provider_event(
                            provider_name,
                            "get_quote",
                            "fallback",
                            error=f"after {', '.join(failed_providers)}",
                        )
                    except Exception:
                        pass
                get_redis_cache().set_quote(symbol, quote)
                return quote
            except Exception as e:
                last_error = e
                failed_providers.append(provider_name)
                logger.warning(f"Failed to get quote for {symbol} from {provider_name}: {e}")
                continue

        if last_error:
            raise last_error
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
        raise RuntimeError("No available providers")

    def get_latest_bar(self, symbol: str, timeframe: str, use_cache: bool = True) -> Bar:
        """Get latest bar for a symbol with fallback.

        Reads from Redis first (if ``use_cache=True``), then falls through the
        provider list on miss. Successful fetches are written back to Redis.
        """
        if use_cache and get_settings().redis.enabled:
            cached_bar = get_redis_cache().get_latest_bar(symbol, timeframe)
            if cached_bar is not None:
                self._cache_stats["bar_hits"] += 1
                logger.debug(f"Redis cache hit for latest bar {symbol}:{timeframe}")
                return cached_bar
            self._cache_stats["bar_misses"] += 1

        last_error = None
        failed_providers: list[str] = []
        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                bar = _call_provider(provider, "get_latest_bar", symbol, timeframe)
                logger.debug(f"Got latest bar for {symbol} from {provider_name}")
                if failed_providers:
                    try:
                        from backend.observability.provider_history import record_provider_event

                        record_provider_event(
                            provider_name,
                            "get_latest_bar",
                            "fallback",
                            error=f"after {', '.join(failed_providers)}",
                        )
                    except Exception:
                        pass
                get_redis_cache().set_latest_bar(symbol, timeframe, bar)
                return bar
            except Exception as e:
                last_error = e
                failed_providers.append(provider_name)
                logger.warning(f"Failed to get latest bar for {symbol} from {provider_name}: {e}")
                continue
        if last_error:
            raise last_error
        raise RuntimeError("No available providers")

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1d",
        range_: str = "3mo",
        use_cache: bool = True,
        db: "Session | None" = None,
        include_extended_hours: bool = False,
    ) -> list[Bar]:
        """Get a series of historical bars for a symbol, with optional caching.

        Cache validity is determined by **two** independent checks:

        1. **Count threshold** — at least 80% of the expected bar count
           for ``(timeframe, range_)``.
        2. **Age threshold** — for intraday timeframes the newest cached
           bar must be no older than ``settings.market_data.cache_ttl_seconds``.

        If the count check passes but the age check fails (intraday only),
        the cached bars are returned tagged ``DataStatus.STALE`` so the UI
        can warn the user while still rendering the data.

        Never raises; returns ``[]`` on failure.
        """
        expected = _EXPECTED_BAR_COUNTS.get((timeframe, range_), 0)
        cache_threshold = int(expected * 0.8) if expected else 0

        # Try Redis cache first if enabled and use_cache is True
        if use_cache and get_settings().redis.enabled:
            cached_bars = get_redis_cache().get_bars(symbol, timeframe)
            if cached_bars is not None and len(cached_bars) > 0:
                self._cache_stats["bar_hits"] += 1
                logger.debug(
                    f"Redis cache hit for bars {symbol}:{timeframe} ({len(cached_bars)} bars)"
                )
                if cache_threshold == 0 or len(cached_bars) >= cache_threshold:
                    age = _newest_bar_age_seconds(cached_bars)
                    ttl = get_settings().market_data.cache_ttl_seconds
                    is_intraday = timeframe in _INTRADAY_TIMEFRAMES
                    if age is not None and is_intraday and age > ttl:
                        age_s = f"{age:.1f}s"
                        logger.warning(
                            f"Serving stale bars for {symbol} {timeframe}: "
                            f"newest bar is {age_s} old (TTL {ttl}s)"
                        )
                        return [
                            bar.model_copy(update={"data_status": DataStatus.STALE})
                            for bar in cached_bars
                        ]
                    logger.debug(
                        f"Redis cache hit for {symbol} {timeframe} {range_}: "
                        f"{len(cached_bars)} bars (threshold {cache_threshold}, "
                        f"age {age:.1f}s)"
                    )
                    return cached_bars
                else:
                    logger.debug(
                        f"Redis cache has insufficient bars for {symbol} {timeframe}: "
                        f"{len(cached_bars)} < {cache_threshold}"
                    )
            else:
                self._cache_stats["bar_misses"] += 1
                logger.debug(f"Redis cache miss for bars {symbol}:{timeframe}")

        # Fall back to database cache if Redis is disabled or missed
        if use_cache and db is not None:
            try:
                from backend.repositories import bar_repository

                cached = bar_repository.get_bars(db, symbol=symbol, timeframe=timeframe, limit=None)
                if cached and (cache_threshold == 0 or len(cached) >= cache_threshold):
                    age = _newest_bar_age_seconds(cached)
                    ttl = get_settings().market_data.cache_ttl_seconds
                    is_intraday = timeframe in _INTRADAY_TIMEFRAMES
                    if age is not None and is_intraday and age > ttl:
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
                        f"Database cache hit for {symbol} {timeframe} {range_}: "
                        f"{len(cached)} bars (threshold {cache_threshold}, "
                        f"age {age:.1f}s)"
                    )
                    if get_settings().redis.enabled:
                        get_redis_cache().set_bars(
                            symbol,
                            timeframe,
                            cached,
                            ttl=get_bar_cache_ttl(timeframe),
                        )
                    return cached
            except Exception as e:
                logger.warning(f"Bar cache lookup failed for {symbol}: {e}")

        # Cache miss — fetch from provider.
        bars: list[Bar] = []
        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                bars = _call_provider(
                    provider,
                    "get_historical_bars",
                    symbol,
                    timeframe=timeframe,
                    range_=range_,
                    include_extended_hours=include_extended_hours,
                )
                logger.debug(
                    f"Got {len(bars)} historical bars for {symbol} "
                    f"({timeframe}, {range_}) from {provider_name}"
                )
                break
            except Exception as e:
                logger.warning(
                    f"Failed to get historical bars for {symbol} from {provider_name}: {e}"
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
                logger.warning(f"Failed to persist bars for {symbol} {timeframe}: {e}")

        if get_settings().redis.enabled and use_cache:
            get_redis_cache().set_bars(
                symbol,
                timeframe,
                bars,
                ttl=get_bar_cache_ttl(timeframe),
            )
            if bars:
                get_redis_cache().publish_bar_update(symbol, timeframe, bars[-1])

        return bars

    def get_historical_bars_batch(
        self,
        symbols: list[str],
        timeframe: str = "1d",
        range_: str = "3mo",
        use_cache: bool = True,
        include_extended_hours: bool = False,
    ) -> dict[str, list[Bar]]:
        """Get historical bars for multiple symbols in a single provider call.

        Uses the provider's batch endpoint (e.g. POST /stock/batch-bars for
        Webull) so all symbols are fetched in one API call instead of N calls.
        Falls back to individual per-symbol calls if the provider doesn't
        support batching. Never raises; returns a dict (possibly empty).
        """
        if not symbols:
            return {}

        # Try provider batch method first.
        result: dict[str, list[Bar]] = {}
        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                batch_fn = getattr(provider, "get_historical_bars_batch", None)
                if batch_fn is None:
                    # Provider doesn't support batching — fall back to individual calls.
                    for sym in symbols:
                        try:
                            bars = _call_provider(
                                provider,
                                "get_historical_bars",
                                sym,
                                timeframe=timeframe,
                                range_=range_,
                                include_extended_hours=include_extended_hours,
                            )
                            if bars:
                                result[sym] = bars
                        except Exception as e:
                            logger.debug(f"Batch fallback failed for {sym}: {e}")
                    break
                else:
                    # Call the batch method via _call_provider_direct so rate limiting,
                    # circuit breaking, and structured logging are applied consistently.
                    result = _call_provider_direct(
                        provider,
                        batch_fn,
                        symbols,
                        timeframe=timeframe,
                        range_=range_,
                        include_extended_hours=include_extended_hours,
                    )
                    if not isinstance(result, dict):
                        result = {}
                    break
            except Exception as e:
                logger.warning(f"Batch bars failed from {provider_name}: {e}")
                continue

        return result

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Get quotes for multiple symbols with fallback."""
        quotes = {}
        errors = []

        if get_settings().redis.enabled:
            cached_quotes = {}
            uncached_symbols = []
            for symbol in symbols:
                cached_quote = get_redis_cache().get_quote(symbol)
                if cached_quote is not None:
                    cached_quotes[symbol] = cached_quote
                    self._cache_stats["quote_hits"] += 1
                else:
                    uncached_symbols.append(symbol)
            quotes.update(cached_quotes)
            logger.debug(f"Redis cache hits for quotes: {len(cached_quotes)}/{len(symbols)}")
            if not uncached_symbols:
                return quotes
            symbols_to_fetch = uncached_symbols
        else:
            symbols_to_fetch = symbols

        if symbols_to_fetch:
            for provider_name in self._get_available_providers():
                try:
                    provider = self.providers[provider_name]
                    batch_quotes = provider.get_batch_quotes(symbols_to_fetch)
                    for symbol in symbols_to_fetch:
                        if symbol in batch_quotes and batch_quotes[symbol].data_status != "ERROR":
                            quotes[symbol] = batch_quotes[symbol]

                    if all(
                        symbol in quotes and quotes[symbol].data_status != "ERROR"
                        for symbol in symbols_to_fetch
                    ):
                        logger.debug(
                            f"Got complete batch quotes for {symbols_to_fetch} from {provider_name}"
                        )
                        for symbol, quote in batch_quotes.items():
                            if symbol in symbols_to_fetch and quote.data_status != "ERROR":
                                get_redis_cache().set_quote(symbol, quote)
                        return quotes
                    else:
                        logger.warning(
                            f"Partial batch quote success from {provider_name}: "
                            f"{len([s for s in symbols_to_fetch if s in quotes and quotes[s].data_status != 'ERROR'])}/"
                            f"{len(symbols_to_fetch)} symbols"
                        )
                except Exception as e:
                    errors.append(f"{provider_name}: {e}")
                    logger.warning(
                        f"Failed to get batch quotes for {symbols_to_fetch} "
                        f"from {provider_name}: {e}"
                    )
                    continue

            if not all(
                symbol in quotes and quotes[symbol].data_status != "ERROR"
                for symbol in symbols_to_fetch
            ):
                logger.info("Falling back to individual quote requests for missing symbols")
                for symbol in symbols_to_fetch:
                    if symbol not in quotes or quotes[symbol].data_status == "ERROR":
                        try:
                            quote = self.get_quote(symbol)
                            quotes[symbol] = quote
                        except Exception as e:
                            logger.error(f"Failed to get quote for {symbol}: {e}")
                            quotes[symbol] = Quote(
                                symbol=symbol.upper(),
                                price=0.0,
                                timestamp=datetime.now(),
                                provider="fallback_failed",
                                data_status="ERROR",
                            )

        return quotes

    async def get_batch_historical_bars(
        self,
        symbols: list[str],
        timeframe: str = "1d",
        range_: str = "3mo",
        use_cache: bool = True,
        db: "Session | None" = None,
    ) -> dict[str, list[Bar]]:
        """Get historical bars for multiple symbols with fallback."""
        results = {}

        if get_settings().redis.enabled:
            cached_results = {}
            uncached_symbols = []
            for symbol in symbols:
                cached_bars = get_redis_cache().get_bars(symbol, timeframe)
                if cached_bars is not None and len(cached_bars) > 0:
                    cached_results[symbol] = cached_bars
                    self._cache_stats["bar_hits"] += 1
                else:
                    uncached_symbols.append(symbol)
            results.update(cached_results)
            logger.debug(
                f"Redis cache hits for historical bars: {len(cached_results)}/{len(symbols)}"
            )
            if not uncached_symbols:
                return results
            symbols_to_fetch = uncached_symbols
        else:
            symbols_to_fetch = symbols

        if symbols_to_fetch:
            for provider_name in self._get_available_providers():
                try:
                    provider = self.providers[provider_name]
                    if hasattr(provider, "get_batch_historical_bars"):
                        batch_bars = await provider.get_batch_historical_bars(
                            symbols_to_fetch, timeframe=timeframe, range_=range_
                        )
                        for symbol in symbols_to_fetch:
                            if symbol in batch_bars and batch_bars[symbol]:
                                results[symbol] = batch_bars[symbol]
                        if all(
                            symbol in results and results[symbol] for symbol in symbols_to_fetch
                        ):
                            logger.debug(
                                f"Got complete batch historical bars for {symbols_to_fetch} "
                                f"from {provider_name}"
                            )
                            for symbol, bars in batch_bars.items():
                                if symbol in symbols_to_fetch and bars:
                                    get_redis_cache().set_bars(
                                        symbol,
                                        timeframe,
                                        bars,
                                        ttl=get_bar_cache_ttl(timeframe),
                                    )
                                    if bars:
                                        get_redis_cache().publish_bar_update(
                                            symbol, timeframe, bars[-1]
                                        )
                            return results
                        else:
                            logger.warning(
                                f"Partial batch historical bar success from {provider_name}: "
                                f"{len([s for s in symbols_to_fetch if s in results and results[s]])}/"
                                f"{len(symbols_to_fetch)} symbols"
                            )
                    else:
                        logger.debug(
                            f"Provider {provider_name} does not support batch historical bars"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to get batch historical bars for {symbols_to_fetch} "
                        f"from {provider_name}: {e}"
                    )
                    continue

            if not all(symbol in results and results[symbol] for symbol in symbols_to_fetch):
                logger.info(
                    "Falling back to individual historical bar requests for missing symbols"
                )
                for symbol in symbols_to_fetch:
                    if symbol not in results or not results[symbol]:
                        try:
                            # to_thread: get_historical_bars is a blocking
                            # call (DB read, and on a cache miss a
                            # synchronous provider HTTP request) executed
                            # directly inside this async method would
                            # freeze the caller's event loop for every
                            # remaining missing symbol in sequence — the
                            # same class of bug scan_symbols_async's own
                            # _prefetch() docstring describes ("a blocking
                            # call here previously froze every other
                            # in-flight request on the server, not just
                            # this one").
                            bars = await asyncio.to_thread(
                                self.get_historical_bars,
                                symbol,
                                timeframe=timeframe,
                                range_=range_,
                                use_cache=use_cache,
                                db=db,
                            )
                            results[symbol] = bars
                        except Exception as e:
                            logger.error(f"Failed to get historical bars for {symbol}: {e}")
                            results[symbol] = []

        return results

    def get_market_status(self, symbol: str) -> MarketStatus:
        """Get market status for a symbol with fallback."""
        last_error = None
        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                status = _call_provider(provider, "get_market_status", symbol)
                logger.debug(f"Got market status for {symbol} from {provider_name}")
                return status
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Failed to get market status for {symbol} from {provider_name}: {e}"
                )
                continue
        if last_error:
            raise last_error
        raise RuntimeError("No available providers")

    def get_provider_statuses(self) -> dict[str, ProviderStatus]:
        """Get status of all providers.

        Each status is enriched with circuit-breaker state pulled from the
        global ``_circuit_breakers`` registry.
        """
        statuses = {}
        for name, provider in self.providers.items():
            try:
                status = provider.get_provider_status()
            except Exception as e:
                logger.error(f"Failed to get status for provider {name}: {e}")
                status = ProviderStatus(
                    provider_name=name,
                    is_healthy=False,
                    error_message=str(e),
                    timestamp=datetime.now(),
                )

            breaker = _circuit_breakers.get(name)
            if breaker is not None:
                stats = breaker.stats()
                statuses[name] = ProviderStatus(
                    provider_name=stats.provider_name,
                    is_healthy=status.is_healthy,
                    latency_ms=status.latency_ms,
                    rate_limit_remaining=status.rate_limit_remaining,
                    last_success=status.last_success,
                    error_message=status.error_message,
                    timestamp=datetime.now(),
                    circuit_breaker_state=stats.state.value,
                    consecutive_failures=stats.consecutive_failures,
                    total_successes=stats.total_successes,
                    total_failures=stats.total_failures,
                    error_count=stats.total_failures,
                    last_error=status.error_message,
                )
            else:
                statuses[name] = status
        return statuses

    def get_cache_stats(self) -> dict[str, Any]:
        """Get caching statistics."""
        stats = {
            "cache": {
                "bar_hits": self._cache_stats["bar_hits"],
                "bar_misses": self._cache_stats["bar_misses"],
                "quote_hits": self._cache_stats["quote_hits"],
                "quote_misses": self._cache_stats["quote_misses"],
                "bar_hit_rate": (
                    self._cache_stats["bar_hits"]
                    / max(self._cache_stats["bar_hits"] + self._cache_stats["bar_misses"], 1)
                )
                * 100,
                "quote_hit_rate": (
                    self._cache_stats["quote_hits"]
                    / max(self._cache_stats["quote_hits"] + self._cache_stats["quote_misses"], 1)
                )
                * 100,
            }
        }
        if get_settings().redis.enabled:
            stats["redis"] = get_redis_cache().get_stats()
        return stats

    def reset_cache_stats(self):
        """Reset cache statistics."""
        self._cache_stats = {
            "bar_hits": 0,
            "bar_misses": 0,
            "quote_hits": 0,
            "quote_misses": 0,
        }


# Global singleton — instantiated when this module is first imported.
market_data_manager = MarketDataManager()
