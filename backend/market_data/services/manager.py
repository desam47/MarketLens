"""
MarketDataManager - handles provider selection, fallback, caching, etc.
"""
import json
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import redis
from tenacity import (
    retry,
    retry_if_exception,
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

from ..circuit_breaker import CircuitBreaker, CircuitBreakerOpen, CircuitState
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


def _get_webull_class() -> type[MarketDataProvider] | None:
    """Lazy resolver for WebullProvider.

    Deferred so a missing ``requests`` install only breaks the Webull
    path, not Yahoo Finance. The class is skipped entirely when
    ``WEBULL_ENABLED`` is false or credentials are not set.
    """
    try:
        from ..providers.webull_provider import WebullProvider
    except Exception as e:
        logger.warning(f"WebullProvider is not importable: {e}")
        return None

    webull_settings = _settings.webull
    if not webull_settings.enabled:
        return None
    if not webull_settings.app_key or not webull_settings.app_secret:
        return None
    return WebullProvider


def _get_finnhub_class() -> type[MarketDataProvider] | None:
    """Lazy resolver for FinnhubProvider.

    Deferred so a missing ``requests`` install only breaks the Finnhub
    path, not Yahoo Finance. The class is skipped entirely when
    ``FINNHUB_ENABLED`` is false.
    """
    try:
        from ..providers.finnhub_provider import FinnhubProvider
    except Exception as e:
        logger.warning(f"FinnhubProvider is not importable: {e}")
        return None

    finnhub_settings = _settings.finnhub
    if not finnhub_settings.enabled:
        return None
    return FinnhubProvider


# Register the actual Finnhub class at module import time (if eligible).
def _register_finnhub() -> None:
    finnhub_cls = _get_finnhub_class()
    if finnhub_cls is not None:
        _PROVIDER_CLASSES["finnhub"] = finnhub_cls


_register_finnhub()


# Register the actual Webull class at module import time (if eligible).
# The check is repeated on every init so credentials-toggled at runtime
# are respected.
def _register_webull() -> None:
    webull_cls = _get_webull_class()
    if webull_cls is not None:
        _PROVIDER_CLASSES["webull"] = webull_cls


_register_webull()


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

    Two attempts (original + one retry) with exponential backoff (0.5s → 1s).
    HTTP 429 rate-limit errors are NOT retried — the circuit breaker handles
    backoff, and retrying a rate-limited endpoint just burns more of the
    quota while making the burst worse.  Other transient errors (503,
    network timeouts) are retried once to handle brief glitches.

    CircuitBreakerOpen is re-raised immediately — fail-fast is intentional
    and retrying a wide-open breaker wastes time.
    """
    def _should_retry(exc: BaseException) -> bool:
        # Don't retry circuit-breaker fail-fast signals.
        if isinstance(exc, CircuitBreakerOpen):
            return False
        # Don't retry HTTP 429 — the circuit breaker's 60s recovery timeout
        # is the back-off mechanism. Retrying a rate-limited endpoint just
        # widens the burst and keeps the circuit open longer.
        if isinstance(exc, RuntimeError) and "rate limited" in str(exc):
            return False
        return True

    return retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
        reraise=True,
        retry=retry_if_exception(_should_retry),
    )


def _get_per_provider_rate_limit(provider_name: str) -> int:
    """Return the per-provider rate limit for ``provider_name``.

    Settings ``yahoo_finance_rate_limit_per_minute`` and
    ``webull_rate_limit_per_minute`` on ``MarketDataSettings`` are read
    via pydantic. Unknown providers fall back to the global
    ``rate_limit_per_minute`` from settings.
    """
    md = _settings.market_data
    return getattr(md, f"{provider_name}_rate_limit_per_minute", md.rate_limit_per_minute)


# Module-level circuit breaker registry (one per provider name).
# Lazily created when a provider first calls through _call_provider.
_circuit_breakers: dict[str, CircuitBreaker] = {}
_cb_lock = threading.Lock()


def _get_breaker(provider_name: str) -> CircuitBreaker:
    """Return the circuit breaker for ``provider_name``, creating it if needed."""
    with _cb_lock:
        if provider_name not in _circuit_breakers:
            _circuit_breakers[provider_name] = CircuitBreaker(
                name=provider_name,
                failure_threshold=5,
                recovery_timeout=60.0,
            )
        return _circuit_breakers[provider_name]


def _call_provider(
    provider: MarketDataProvider,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Invoke a provider method with per-provider rate limiting, circuit
    breaking, and tenacity retry.

    Rate limiting (per ``settings.market_data.<provider>_rate_limit_per_minute``)
    is checked before every call so bursts are throttled even if individual
    calls succeed quickly. The circuit breaker fail-fast path is evaluated
    before the call so OPEN providers skip the rate limiter entirely.

    Tenacity retry is applied on top of the circuit breaker — three
    attempts with exponential backoff (0.5s → 1s → 2s, capped at 8s).

    Structured logging: every call is logged at DEBUG with the correlation
    ID (when one is active in the request context), the provider name,
    method, the first positional argument (typically a symbol), latency
    in ms, and success/failure status. Response bodies are NEVER logged —
    they may contain sensitive data and would be high-cardinality noise.
    """
    provider_name = provider.name
    breaker = _get_breaker(provider_name)

    # Rate limiting
    limit = _get_per_provider_rate_limit(provider_name)
    _rate_limiter.acquire(provider_name, limit)

    method: Callable = getattr(provider, method_name)

    # Per-call observability: log start (DEBUG), measure latency, log result.
    # The first positional arg is conventionally the symbol for our 8
    # provider methods; pulling it out here keeps log lines searchable.
    # Lazy import avoids circular dependency (manager <-> observability).
    symbol = args[0] if args else "-"
    call_started = time.monotonic()
    logger.debug(
        "provider_call_start provider=%s method=%s symbol=%s correlation_id=%s",
        provider_name, method_name, symbol, _correlation_id_placeholder(),
    )
    try:
        result = _provider_call_with_breaker(
            provider_name, breaker, method, *args, **kwargs
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - call_started) * 1000.0
        logger.debug(
            "provider_call_fail provider=%s method=%s symbol=%s "
            "latency_ms=%.2f correlation_id=%s error_type=%s",
            provider_name, method_name, symbol, latency_ms,
            _correlation_id_placeholder(), type(exc).__name__,
        )
        raise
    latency_ms = (time.monotonic() - call_started) * 1000.0
    logger.debug(
        "provider_call_ok provider=%s method=%s symbol=%s "
        "latency_ms=%.2f correlation_id=%s",
        provider_name, method_name, symbol, latency_ms,
        _correlation_id_placeholder(),
    )
    return result


# Lazy-loaded at first use to avoid circular imports:
#   manager.py -> observability.logging_enhanced -> api.structured_logging
#   -> api.__init__ -> market_data_routes -> ingestion_service -> manager.py
_corr_id_fn: Callable[[], str] | None = None


def _correlation_id_placeholder() -> str:
    """Return the current correlation ID, or '-' if none is active."""
    global _corr_id_fn
    if _corr_id_fn is None:
        try:
            from backend.observability.logging_enhanced import get_correlation_id

            _corr_id_fn = get_correlation_id
        except Exception:
            _corr_id_fn = lambda: "-"  # type: ignore[assignment]
    return _corr_id_fn() or "-"


@_provider_retry()
def _provider_call_with_breaker(
    provider_name: str,
    breaker: CircuitBreaker,
    method: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute ``method(*args, **kwargs)`` through the circuit breaker.

    Called via tenacity so each retry gets its own circuit-breaker
    success/failure recording.
    """
    return breaker.call(method, *args, **kwargs)


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


class RedisCache:
    """Redis-based caching layer for market data."""

    def __init__(self):
        self._client: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._initialize_redis()

    def _initialize_redis(self):
        """Initialize Redis connection if enabled."""
        if not _settings.redis.enabled:
            logger.info("Redis caching is disabled")
            return

        try:
            # Parse Redis URL to handle password if needed
            if _settings.redis.password:
                # If password is provided, we might need to construct URL differently
                # For simplicity, assuming URL already contains credentials or we add them
                self._client = redis.Redis.from_url(
                    _settings.redis.url,
                    password=_settings.redis.password,
                    decode_responses=True  # Automatically decode responses to strings
                )
            else:
                self._client = redis.Redis.from_url(
                    _settings.redis.url,
                    decode_responses=True
                )

            # Test connection
            self._client.ping()
            logger.info(f"Connected to Redis at {_settings.redis.url}")

            # Initialize pub/sub for real-time data distribution
            self._pubsub = self._client.pubsub()

        except Exception as e:
            logger.warning(f"Failed to initialize Redis connection: {e}")
            self._client = None
            self._pubsub = None

    def is_available(self) -> bool:
        """Check if Redis is available and connected."""
        if not self._client:
            return False
        try:
            return self._client.ping()
        except Exception:
            return False

    def _make_bar_key(self, symbol: str, timeframe: str) -> str:
        """Generate Redis key for bar data."""
        return f"marketlens:bars:{symbol}:{timeframe}"

    def _make_quote_key(self, symbol: str) -> str:
        """Generate Redis key for quote data."""
        return f"marketlens:quote:{symbol}"

    def _make_latest_bar_key(self, symbol: str, timeframe: str) -> str:
        """Generate Redis key for the latest-bar cache (distinct from
        ``get_bars`` which holds a historical series)."""
        return f"marketlens:latest_bar:{symbol}:{timeframe}"

    def get_bars(self, symbol: str, timeframe: str) -> list[Bar] | None:
        """Get cached bar data for symbol/timeframe."""
        if not self.is_available():
            return None

        try:
            key = self._make_bar_key(symbol, timeframe)
            data = self._client.get(key)
            if data is None:
                return None

            # Deserialize bars from JSON
            bars_data = json.loads(data)
            bars = []
            for bar_dict in bars_data:
                bar = Bar(**bar_dict)
                bars.append(bar)
            return bars
        except Exception as e:
            logger.warning(f"Failed to get bars from Redis for {symbol}:{timeframe}: {e}")
            return None

    def set_bars(self, symbol: str, timeframe: str, bars: list[Bar]) -> bool:
        """Cache bar data for symbol/timeframe."""
        if not self.is_available():
            return False

        try:
            key = self._make_bar_key(symbol, timeframe)
            # Serialize bars to JSON
            bars_data = [bar.model_dump() for bar in bars]
            data = json.dumps(bars_data, default=str)  # Handle datetime serialization

            # Set with TTL from settings
            self._client.setex(
                key,
                _settings.redis.bar_data_ttl,
                data
            )

            # Enforce size limit by removing oldest keys if needed
            self._enforce_size_limit("bars")

            logger.debug(f"Cached {len(bars)} bars for {symbol}:{timeframe} in Redis")
            return True
        except Exception as e:
            logger.warning(f"Failed to cache bars to Redis for {symbol}:{timeframe}: {e}")
            return False

    def get_quote(self, symbol: str) -> Quote | None:
        """Get cached quote for symbol."""
        if not self.is_available():
            return None

        try:
            key = self._make_quote_key(symbol)
            data = self._client.get(key)
            if data is None:
                return None

            # Deserialize quote from JSON
            quote_data = json.loads(data)
            quote = Quote(**quote_data)
            return quote
        except Exception as e:
            logger.warning(f"Failed to get quote from Redis for {symbol}: {e}")
            return None

    def set_quote(self, symbol: str, quote: Quote) -> bool:
        """Cache quote for symbol."""
        if not self.is_available():
            return False

        try:
            key = self._make_quote_key(symbol)
            # Serialize quote to JSON
            quote_data = quote.model_dump()
            data = json.dumps(quote_data, default=str)  # Handle datetime serialization

            # Set with TTL from settings
            self._client.setex(
                key,
                _settings.redis.quote_ttl,
                data
            )

            # Enforce size limit by removing oldest keys if needed
            self._enforce_size_limit("quotes")

            logger.debug(f"Cached quote for {symbol} in Redis")
            return True
        except Exception as e:
            logger.warning(f"Failed to cache quote to Redis for {symbol}: {e}")
            return False

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar | None:
        """Get cached latest bar for symbol/timeframe (separate from
        ``get_bars`` which returns a historical series)."""
        if not self.is_available():
            return None

        try:
            key = self._make_latest_bar_key(symbol, timeframe)
            data = self._client.get(key)
            if data is None:
                return None

            bar_dict = json.loads(data)
            return Bar(**bar_dict)
        except Exception as e:
            logger.warning(f"Failed to get latest bar from Redis for {symbol}:{timeframe}: {e}")
            return None

    def set_latest_bar(self, symbol: str, timeframe: str, bar: Bar) -> bool:
        """Cache the latest bar for symbol/timeframe.

        Uses a short TTL (configured via ``redis.bar_data_ttl``) so that
        fresh ingestion cycles naturally overwrite stale entries without
        having to invalidate by hand.
        """
        if not self.is_available():
            return False

        try:
            key = self._make_latest_bar_key(symbol, timeframe)
            data = json.dumps(bar.model_dump(), default=str)

            self._client.setex(
                key,
                _settings.redis.bar_data_ttl,
                data,
            )

            # Publish so any subscriber (WebSocket fan-out, etc.) sees the update.
            try:
                self._client.publish(
                    f"marketlens:bar_updates:{symbol}:{timeframe}",
                    data,
                )
            except Exception:
                # Publish failures should never break a cache write.
                pass

            logger.debug(f"Cached latest bar for {symbol}:{timeframe} in Redis")
            return True
        except Exception as e:
            logger.warning(f"Failed to cache latest bar to Redis for {symbol}:{timeframe}: {e}")
            return False

    def _enforce_size_limit(self, cache_type: str):
        """Enforce size limits by removing oldest keys when limits are exceeded."""
        if not self.is_available():
            return

        try:
            if cache_type == "bars":
                max_keys = _settings.redis.max_bar_keys
                pattern = "marketlens:bars:*"
            elif cache_type == "quotes":
                max_keys = _settings.redis.max_quote_keys
                pattern = "marketlens:quote:*"
            else:
                return

            # Get all matching keys
            keys = self._client.keys(pattern)
            if len(keys) > max_keys:
                # Sort by insertion time (approximate using TTL - not perfect but works)
                # In a production system, we'd use Redis LRU eviction or sorted sets with timestamps
                # For simplicity, we'll remove random excess keys
                excess = len(keys) - max_keys
                keys_to_remove = keys[:excess]  # Remove first 'excess' keys
                if keys_to_remove:
                    self._client.delete(*keys_to_remove)
                    logger.debug(f"Removed {len(keys_to_remove)} old {cache_type} keys from Redis")
        except Exception as e:
            logger.warning(f"Failed to enforce size limit for {cache_type}: {e}")

    def publish_bar_update(self, symbol: str, timeframe: str, bar: Bar):
        """Publish bar update to Redis channel for real-time distribution."""
        if not self.is_available() or not self._pubsub:
            return

        try:
            channel = f"marketlens:bar_updates:{symbol}:{timeframe}"
            # Serialize bar to JSON
            bar_data = bar.model_dump()
            message = json.dumps(bar_data, default=str)
            self._client.publish(channel, message)
            logger.debug(f"Published bar update for {symbol}:{timeframe} to Redis")
        except Exception as e:
            logger.warning(f"Failed to publish bar update to Redis: {e}")

    def publish_quote_update(self, symbol: str, quote: Quote):
        """Publish quote update to Redis channel for real-time distribution."""
        if not self.is_available() or not self._pubsub:
            return

        try:
            channel = f"marketlens:quote_updates:{symbol}"
            # Serialize quote to JSON
            quote_data = quote.model_dump()
            message = json.dumps(quote_data, default=str)
            self._client.publish(channel, message)
            logger.debug(f"Published quote update for {symbol} to Redis")
        except Exception as e:
            logger.warning(f"Failed to publish quote update to Redis: {e}")

    def subscribe_to_bar_updates(self, symbol: str, timeframe: str, callback: Callable):
        """Subscribe to bar updates for a symbol/timeframe."""
        if not self.is_available() or not self._pubsub:
            return

        try:
            channel = f"marketlens:bar_updates:{symbol}:{timeframe}"
            self._pubsub.subscribe(**{channel: callback})
            logger.debug(f"Subscribed to bar updates for {symbol}:{timeframe}")
        except Exception as e:
            logger.warning(f"Failed to subscribe to bar updates: {e}")

    def subscribe_to_quote_updates(self, symbol: str, callback: Callable):
        """Subscribe to quote updates for a symbol."""
        if not self.is_available() or not self._pubsub:
            return

        try:
            channel = f"marketlens:quote_updates:{symbol}"
            self._pubsub.subscribe(**{channel: callback})
            logger.debug(f"Subscribed to quote updates for {symbol}")
        except Exception as e:
            logger.warning(f"Failed to subscribe to quote updates: {e}")

    def start_listening(self):
        """Start listening for pub/sub messages in a background thread."""
        if not self.is_available() or not self._pubsub:
            return

        def listen():
            try:
                for message in self._pubsub.listen():
                    if message["type"] == "message":
                        # Message handling is done via callbacks in subscribe methods
                        pass
            except Exception as e:
                logger.warning(f"Redis pub/sub listener error: {e}")

        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        logger.debug("Started Redis pub/sub listener thread")

    def get_stats(self) -> dict[str, Any]:
        """Get Redis cache statistics."""
        if not self.is_available():
            return {"enabled": False}

        try:
            info = self._client.info()
            return {
                "enabled": True,
                "connected_clients": info.get("connected_clients", 0),
                "used_memory": info.get("used_memory", 0),
                "used_memory_human": info.get("used_memory_human", "0B"),
                "total_commands_processed": info.get("total_commands_processed", 0),
                "instantaneous_ops_per_sec": info.get("instantaneous_ops_per_sec", 0),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "hit_rate": (
                    info.get("keyspace_hits", 0) /
                    max(info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0), 1)
                ) * 100
            }
        except Exception as e:
            logger.warning(f"Failed to get Redis stats: {e}")
            return {"enabled": True, "error": str(e)}


# Initialize Redis cache instance
_redis_cache = RedisCache()


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
        """Get list of available providers in priority order.

        Providers with an OPEN circuit breaker are skipped — the breaker
        is consulted after ``is_available()`` so a temporarily-failing
        provider doesn't get hammered with retries.
        """
        available = []
        for provider_name in self.provider_priority:
            provider = self.providers.get(provider_name)
            if not provider or not provider.is_available():
                continue
            breaker = _circuit_breakers.get(provider_name)
            if breaker is not None and breaker.get_state() in (
                CircuitState.OPEN,
                CircuitState.HALF_OPEN,
            ):
                # OPEN and HALF_OPEN are both skipped — OPEN fail-fasts on the
                # next call attempt, and HALF_OPEN allows only one test call
                # through. Keeping the provider out of the available list
                # avoids racing multiple consumers onto the same probe call.
                continue
            available.append(provider_name)
        return available

    def get_quote(self, symbol: str) -> Quote:
        """Get quote for a symbol with fallback.

        Each provider gets up to 3 attempts (with exponential backoff) before
        the manager falls through to the next provider in priority order.
        """
        # Check Redis cache first
        cached_quote = _redis_cache.get_quote(symbol)
        if cached_quote is not None:
            self._cache_stats["quote_hits"] += 1
            logger.debug(f"Redis cache hit for quote {symbol}")
            return cached_quote

        self._cache_stats["quote_misses"] += 1
        logger.debug(f"Redis cache miss for quote {symbol}")

        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                quote = _call_provider(provider, "get_quote", symbol)
                logger.debug(f"Got quote for {symbol} from {provider_name}")

                # Cache the quote in Redis
                _redis_cache.set_quote(symbol, quote)

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
        """Get latest bar for a symbol with fallback.

        Reads from Redis first (so repeated ingestion cycles don't hammer
        the provider chain), then falls through the provider list on miss.
        Successful fetches are written back to Redis for the next caller.
        """
        # Check Redis cache first
        if _settings.redis.enabled:
            cached_bar = _redis_cache.get_latest_bar(symbol, timeframe)
            if cached_bar is not None:
                self._cache_stats["bar_hits"] += 1
                logger.debug(f"Redis cache hit for latest bar {symbol}:{timeframe}")
                return cached_bar
            self._cache_stats["bar_misses"] += 1

        last_error = None

        for provider_name in self._get_available_providers():
            try:
                provider = self.providers[provider_name]
                bar = _call_provider(provider, "get_latest_bar", symbol, timeframe)
                logger.debug(f"Got latest bar for {symbol} from {provider_name}")

                # Cache for the next caller.
                _redis_cache.set_latest_bar(symbol, timeframe, bar)

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

        # Try Redis cache first if enabled and use_cache is True
        if use_cache and _settings.redis.enabled:
            cached_bars = _redis_cache.get_bars(symbol, timeframe)
            if cached_bars is not None and len(cached_bars) > 0:
                self._cache_stats["bar_hits"] += 1
                logger.debug(f"Redis cache hit for bars {symbol}:{timeframe} ({len(cached_bars)} bars)")

                # Apply same staleness logic as database cache
                if cache_threshold == 0 or len(cached_bars) >= cache_threshold:
                    age = _newest_bar_age_seconds(cached_bars)
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
                        f"Database cache hit for {symbol} {timeframe} {range_}: "
                        f"{len(cached)} bars (threshold {cache_threshold}, "
                        f"age {age:.1f}s)"
                    )

                    # Also populate Redis cache with database hit for faster future access
                    if _settings.redis.enabled:
                        _redis_cache.set_bars(symbol, timeframe, cached)

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

        # Cache the result in Redis for future requests
        if _settings.redis.enabled and use_cache:
            _redis_cache.set_bars(symbol, timeframe, bars)
            # Publish update for real-time subscribers
            if bars:
                _redis_cache.publish_bar_update(symbol, timeframe, bars[-1])  # Latest bar

        return bars

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Get quotes for multiple symbols with fallback"""
        # For batch quotes, we'll try to use providers that support it efficiently
        quotes = {}
        errors = []  # Initialize errors list

        # First, try to get all quotes from Redis cache
        if _settings.redis.enabled:
            cached_quotes = {}
            uncached_symbols = []
            for symbol in symbols:
                cached_quote = _redis_cache.get_quote(symbol)
                if cached_quote is not None:
                    cached_quotes[symbol] = cached_quote
                    self._cache_stats["quote_hits"] += 1
                else:
                    uncached_symbols.append(symbol)

            quotes.update(cached_quotes)
            logger.debug(f"Redis cache hits for quotes: {len(cached_quotes)}/{len(symbols)}")

            # If we got all quotes from cache, return early
            if not uncached_symbols:
                return quotes

            # Otherwise, we need to fetch the remaining symbols
            symbols_to_fetch = uncached_symbols
        else:
            symbols_to_fetch = symbols

        if symbols_to_fetch:
            for provider_name in self._get_available_providers():
                try:
                    provider = self.providers[provider_name]
                    batch_quotes = provider.get_batch_quotes(symbols_to_fetch)
                    # Use the batch results, filling in any missing/failed symbols with individual quotes
                    for symbol in symbols_to_fetch:
                        if symbol in batch_quotes and batch_quotes[symbol].data_status != "ERROR":
                            quotes[symbol] = batch_quotes[symbol]
                        # If symbol missing or has ERROR status, we'll try to get it individually below

                    # Check if we got all symbols successfully from batch
                    if all(symbol in quotes and quotes[symbol].data_status != "ERROR" for symbol in symbols_to_fetch):
                        logger.debug(f"Got complete batch quotes for {symbols_to_fetch} from {provider_name}")

                        # Cache the batch quotes in Redis
                        for symbol, quote in batch_quotes.items():
                            if symbol in symbols_to_fetch and quote.data_status != "ERROR":
                                _redis_cache.set_quote(symbol, quote)

                        return quotes
                    else:
                        logger.warning(f"Partial batch quote success from {provider_name}: {len([s for s in symbols_to_fetch if s in quotes and quotes[s].data_status != 'ERROR'])}/{len(symbols_to_fetch)} symbols")
                except Exception as e:
                    errors.append(f"{provider_name}: {e}")
                    logger.warning(f"Failed to get batch quotes for {symbols_to_fetch} from {provider_name}: {e}")
                    continue

            # If we didn't get complete data from any provider, fall back to individual quotes for missing/failed symbols
            if not all(symbol in quotes and quotes[symbol].data_status != "ERROR" for symbol in symbols_to_fetch):
                logger.info("Falling back to individual quote requests for missing symbols")
                for symbol in symbols_to_fetch:
                    if symbol not in quotes or quotes[symbol].data_status == "ERROR":
                        try:
                            quote = self.get_quote(symbol)  # This will use Redis cache internally
                            quotes[symbol] = quote
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

    def get_batch_historical_bars(
        self,
        symbols: list[str],
        timeframe: str = "1d",
        range_: str = "3mo",
        use_cache: bool = True,
        db: "Session | None" = None,
    ) -> dict[str, list[Bar]]:
        """Get historical bars for multiple symbols with fallback"""
        # We'll try to use providers that support batch historical bars efficiently
        results = {}

        # First, try to get all bars from Redis cache
        if _settings.redis.enabled:
            cached_results = {}
            uncached_symbols = []
            for symbol in symbols:
                cached_bars = _redis_cache.get_bars(symbol, timeframe)
                if cached_bars is not None and len(cached_bars) > 0:
                    cached_results[symbol] = cached_bars
                    self._cache_stats["bar_hits"] += 1
                else:
                    uncached_symbols.append(symbol)

            results.update(cached_results)
            logger.debug(f"Redis cache hits for historical bars: {len(cached_results)}/{len(symbols)}")

            # If we got all bars from cache, return early
            if not uncached_symbols:
                return results

            # Otherwise, we need to fetch the remaining symbols
            symbols_to_fetch = uncached_symbols
        else:
            symbols_to_fetch = symbols

        if symbols_to_fetch:
            for provider_name in self._get_available_providers():
                try:
                    provider = self.providers[provider_name]
                    # Check if the provider supports batch historical bars
                    if hasattr(provider, 'get_batch_historical_bars'):
                        batch_bars = provider.get_batch_historical_bars(symbols_to_fetch, timeframe=timeframe, range_=range_)
                        # Use the batch results, filling in any missing/failed symbols with individual quotes
                        for symbol in symbols_to_fetch:
                            if symbol in batch_bars and batch_bars[symbol]:
                                results[symbol] = batch_bars[symbol]
                            # If symbol missing or empty, we'll try to get it individually below

                        # Check if we got all symbols successfully from batch
                        if all(symbol in results and results[symbol] for symbol in symbols_to_fetch):
                            logger.debug(f"Got complete batch historical bars for {symbols_to_fetch} from {provider_name}")

                            # Cache the batch results in Redis
                            for symbol, bars in batch_bars.items():
                                if symbol in symbols_to_fetch and bars:
                                    _redis_cache.set_bars(symbol, timeframe, bars)
                                    # Publish update for real-time subscribers (latest bar)
                                    if bars:
                                        _redis_cache.publish_bar_update(symbol, timeframe, bars[-1])

                            return results
                        else:
                            logger.warning(f"Partial batch historical bar success from {provider_name}: {len([s for s in symbols_to_fetch if s in results and results[s]])}/{len(symbols_to_fetch)} symbols")
                    else:
                        # Provider doesn't support batch, we'll fall back to individual calls later
                        logger.debug(f"Provider {provider_name} does not support batch historical bars")
                except Exception as e:
                    logger.warning(f"Failed to get batch historical bars for {symbols_to_fetch} from {provider_name}: {e}")
                    continue

            # If we didn't get complete data from any provider, fall back to individual historical bars for missing/failed symbols
            if not all(symbol in results and results[symbol] for symbol in symbols_to_fetch):
                logger.info("Falling back to individual historical bar requests for missing symbols")
                for symbol in symbols_to_fetch:
                    if symbol not in results or not results[symbol]:
                        try:
                            bars = self.get_historical_bars(symbol, timeframe=timeframe, range_=range_, use_cache=use_cache, db=db)
                            results[symbol] = bars
                        except Exception as e:
                            logger.error(f"Failed to get historical bars for {symbol}: {e}")
                            results[symbol] = []

        return results

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
        """Get status of all providers.

        Each status is enriched with circuit-breaker state pulled from the
        global ``_circuit_breakers`` registry so the health endpoint can
        surface OPEN/HALF_OPEN states to callers.
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

            # Enrich with circuit breaker state.
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
                    self._cache_stats["bar_hits"] /
                    max(self._cache_stats["bar_hits"] + self._cache_stats["bar_misses"], 1)
                ) * 100,
                "quote_hit_rate": (
                    self._cache_stats["quote_hits"] /
                    max(self._cache_stats["quote_hits"] + self._cache_stats["quote_misses"], 1)
                ) * 100,
            }
        }

        # Add Redis stats if available
        if _settings.redis.enabled:
            stats["redis"] = _redis_cache.get_stats()

        return stats

    def reset_cache_stats(self):
        """Reset cache statistics."""
        self._cache_stats = {
            "bar_hits": 0,
            "bar_misses": 0,
            "quote_hits": 0,
            "quote_misses": 0,
        }


# Global instance
market_data_manager = MarketDataManager()

# Start Redis pub/sub listener if Redis is enabled
if _settings.redis.enabled:
    _redis_cache.start_listening()