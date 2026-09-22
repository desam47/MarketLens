"""
Redis-based caching layer for market data.

Kept separate from the manager so it can be tested independently and so
the cache lifecycle (initialisation, pub/sub listener startup) is isolated.
"""

import json
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from backend.models.market_data import Bar, Quote

if TYPE_CHECKING:
    import redis  # used only for type hints in class bodies

# ``_providers`` provides module-level defaults AND runtime helpers
# (``get_settings()`` / ``get_redis()``) that look up the current value
# from the manager shim so test patches are honoured.
from . import _providers as _shared

# Module-level defaults — used for type hints and the singleton.
_settings = _shared._settings
redis = _shared.redis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _bar_to_json(bar: Bar) -> str:
    """Serialize a single ``Bar`` to a JSON string.

    ``model_dump()`` produces a dict; ``json.dumps(..., default=str)`` handles
    non-standard types (datetime, Enum) that are not natively JSON-serializable.
    """
    return json.dumps(bar.model_dump(), default=str)


def _bars_to_json(bars: list[Bar]) -> str:
    """Serialize a list of ``Bar`` objects to a JSON string."""
    return json.dumps([bar.model_dump() for bar in bars], default=str)


def _quote_to_json(quote: Quote) -> str:
    """Serialize a single ``Quote`` to a JSON string."""
    return json.dumps(quote.model_dump(), default=str)


# ---------------------------------------------------------------------------
# TTL table
# ---------------------------------------------------------------------------

# Phase 3.1: per-timeframe cache TTLs for resampled bar series.
# Higher-TF resamples are stable for longer than 1m data, so they
# can be cached for longer without serving stale values. Values are
# the minimum *and* maximum cache freshness — clients see a fresh
# resample every TTL window. The ``1m`` entry is the default for any
# 1m cache key (we still cache 1m bars for the same window so the
# resample path is faster on the next read).
_BAR_CACHE_TTL: dict[str, int] = {
    "1m": 60,  # 1m: every minute is a new bar
    "5m": 120,  # 5m: refresh every 2 minutes
    "15m": 180,
    "30m": 240,
    "1h": 300,
    "4h": 360,  # 4h: refresh every 6 minutes
    "1d": 600,  # 10 minutes — plan spec
    "5d": 1200,  # 20 minutes
    "1wk": 3600,  # 1 hour   — plan spec
    "1mo": 3600,
}

# Default TTL for any timeframe not in the table.
_BAR_CACHE_DEFAULT_TTL = 300


def get_bar_cache_ttl(timeframe: str) -> int:
    """Return the Redis cache TTL (seconds) for a resampled bar series."""
    return _BAR_CACHE_TTL.get(timeframe, _BAR_CACHE_DEFAULT_TTL)


class RedisCache:
    """Redis-based caching layer for market data."""

    def __init__(self):
        self._client: _shared.redis.Redis | None = None
        self._pubsub: _shared.redis.client.PubSub | None = None
        self._initialize_redis()

    def _initialize_redis(self):
        """Initialize Redis connection if enabled."""
        # Look up settings at runtime so test patches on ``manager._settings``
        # are honoured even though the singleton is created at module import time.
        settings = _shared.get_settings()
        r = _shared.get_redis()
        if not settings.redis.enabled:
            logger.info("Redis caching is disabled")
            return

        try:
            if settings.redis.password:
                self._client = r.Redis.from_url(
                    settings.redis.url,
                    password=settings.redis.password,
                    decode_responses=True,
                )
            else:
                self._client = r.Redis.from_url(
                    settings.redis.url,
                    decode_responses=True,
                )
            self._client.ping()
            logger.info(f"Connected to Redis at {settings.redis.url}")
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
        return f"marketlens:bars:{symbol}:{timeframe}"

    def _make_quote_key(self, symbol: str) -> str:
        return f"marketlens:quote:{symbol}"

    def _make_latest_bar_key(self, symbol: str, timeframe: str) -> str:
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
            bars_data = json.loads(data)
            return [Bar(**bd) for bd in bars_data]
        except Exception as e:
            logger.warning(f"Failed to get bars from Redis for {symbol}:{timeframe}: {e}")
            return None

    def set_bars(
        self,
        symbol: str,
        timeframe: str,
        bars: list[Bar],
        ttl: int | None = None,
    ) -> bool:
        """Cache bar data for symbol/timeframe.

        Phase 3.1: ``ttl`` overrides the default per-TF TTL when set.
        Callers that cache resampled series should pass the desired TTL
        explicitly (e.g. ``ttl=get_bar_cache_ttl(timeframe)``) so the
        right cache age applies regardless of the global default.
        """
        if not self.is_available():
            return False
        try:
            settings = _shared.get_settings()
            key = self._make_bar_key(symbol, timeframe)
            data = _bars_to_json(bars)
            effective_ttl = ttl if ttl is not None else settings.redis.bar_data_ttl
            self._client.setex(key, effective_ttl, data)
            self._enforce_size_limit("bars")
            logger.debug(
                f"Cached {len(bars)} bars for {symbol}:{timeframe} (TTL={effective_ttl}s) in Redis"
            )
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
            return Quote(**json.loads(data))
        except Exception as e:
            logger.warning(f"Failed to get quote from Redis for {symbol}: {e}")
            return None

    def set_quote(self, symbol: str, quote: Quote) -> bool:
        """Cache quote for symbol."""
        if not self.is_available():
            return False
        try:
            settings = _shared.get_settings()
            key = self._make_quote_key(symbol)
            data = _quote_to_json(quote)
            self._client.setex(key, settings.redis.quote_ttl, data)
            self._enforce_size_limit("quotes")
            logger.debug(f"Cached quote for {symbol} in Redis")
            return True
        except Exception as e:
            logger.warning(f"Failed to cache quote to Redis for {symbol}: {e}")
            return False

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar | None:
        """Get cached latest bar for symbol/timeframe (separate from historical series)."""
        if not self.is_available():
            return None
        try:
            key = self._make_latest_bar_key(symbol, timeframe)
            data = self._client.get(key)
            if data is None:
                return None
            return Bar(**json.loads(data))
        except Exception as e:
            logger.warning(f"Failed to get latest bar from Redis for {symbol}:{timeframe}: {e}")
            return None

    def set_latest_bar(self, symbol: str, timeframe: str, bar: Bar) -> bool:
        """Cache the latest bar for symbol/timeframe."""
        if not self.is_available():
            return False
        try:
            key = self._make_latest_bar_key(symbol, timeframe)
            data = _bar_to_json(bar)
            self._client.setex(key, get_bar_cache_ttl(timeframe), data)
            try:
                self._client.publish(f"marketlens:bar_updates:{symbol}:{timeframe}", data)
            except Exception:
                pass
            logger.debug(f"Cached latest bar for {symbol}:{timeframe} in Redis")
            return True
        except Exception as e:
            logger.warning(f"Failed to cache latest bar to Redis for {symbol}:{timeframe}: {e}")
            return False

    def invalidate_bars_for_symbol(self, symbol: str) -> int:
        """Invalidate all cached bar series and latest-bar entries for a symbol.

        Called by the ingestion service after a successful 1m upsert so the
        next read recomputes the resampled series from the DB instead of
        serving the stale cached version (which could be up to ``_BAR_CACHE_TTL``
        seconds out of date — 600s for 1d, 3600s for 1wk).

        Returns the number of Redis keys deleted. Returns 0 when Redis is
        unavailable; callers should treat that as a non-fatal cache miss.

        Notes on key patterns:
          - Bar series:   ``marketlens:bars:{symbol}:*`` (5m, 1h, 1d, 1wk, etc.)
          - Latest bar:   ``marketlens:latest_bar:{symbol}:*`` (1m, 5m, etc.)
        We use ``SCAN`` instead of ``KEYS`` to avoid blocking Redis on large
        keyspaces (a single bar symbol has at most ~10 keys, so SCAN is overkill
        but it's the same pattern used elsewhere for consistency).
        """
        if not self.is_available():
            return 0
        try:
            patterns = [
                f"marketlens:bars:{symbol}:*",
                f"marketlens:latest_bar:{symbol}:*",
            ]
            total_deleted = 0
            for pattern in patterns:
                keys: list[str] = []
                cursor = 0
                while True:
                    cursor, batch = self._client.scan(cursor=cursor, match=pattern, count=100)
                    keys.extend(batch)
                    if cursor == 0:
                        break
                if keys:
                    self._client.delete(*keys)
                    total_deleted += len(keys)
            if total_deleted:
                logger.debug(f"Invalidated {total_deleted} cached bar keys for {symbol}")
            return total_deleted
        except Exception as e:
            logger.warning(f"Failed to invalidate bar cache for {symbol}: {e}")
            return 0

    def _enforce_size_limit(self, cache_type: str):
        """Enforce size limits by removing oldest keys when limits are exceeded."""
        if not self.is_available():
            return
        try:
            settings = _shared.get_settings()
            if cache_type == "bars":
                max_keys = settings.redis.max_bar_keys
                pattern = "marketlens:bars:*"
            elif cache_type == "quotes":
                max_keys = settings.redis.max_quote_keys
                pattern = "marketlens:quote:*"
            else:
                return
            keys = self._client.keys(pattern)
            if len(keys) > max_keys:
                excess = len(keys) - max_keys
                keys_to_remove = keys[:excess]
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
            message = _bar_to_json(bar)
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
            message = _quote_to_json(quote)
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
                        pass
            except Exception as e:
                logger.warning(f"Redis pub/sub listener error: {e}")

        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        logger.debug("Started Redis pub/sub listener thread")

    def get_stats(self) -> dict[str, Any]:
        """Get Redis cache statistics."""
        settings = _shared.get_settings()
        if not settings.redis.enabled:
            return {"enabled": False, "connected": False, "status": "disabled"}
        if not self.is_available():
            return {"enabled": True, "connected": False, "status": "unavailable"}
        try:
            info = self._client.info()
            hits = info.get("keyspace_hits", 0)
            misses = info.get("keyspace_misses", 0)
            return {
                "enabled": True,
                "connected": True,
                "status": "running",
                "connected_clients": info.get("connected_clients", 0),
                "used_memory_human": info.get("used_memory_human", "0B"),
                "total_commands_processed": info.get("total_commands_processed", 0),
                "instantaneous_ops_per_sec": info.get("instantaneous_ops_per_sec", 0),
                "keyspace_hits": hits,
                "keyspace_misses": misses,
                "hit_rate": (hits / max(hits + misses, 1)) * 100,
            }
        except Exception as e:
            logger.warning(f"Failed to get Redis stats: {e}")
            return {"enabled": True, "connected": False, "status": "unavailable", "error": str(e)}


# Module-level singleton — instantiated when this module is first imported.
_redis_cache = RedisCache()

# Start pub/sub listener when this module is imported and Redis is enabled.
if _redis_cache.is_available():
    _redis_cache.start_listening()
