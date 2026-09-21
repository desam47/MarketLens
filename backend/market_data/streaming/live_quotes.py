"""Process-local cache for live Webull quote updates."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any


class LiveQuoteCache:
    def __init__(self, max_symbols: int = 2000) -> None:
        self._values: dict[str, dict[str, Any]] = {}
        self._updated: dict[str, float] = {}
        self._lock = threading.RLock()
        self._max_symbols = max_symbols

    def update(self, symbol: str, *, price: float, timestamp: Any,
               volume: float | None = None, bid: float | None = None,
               ask: float | None = None, bid_size: float | None = None,
               ask_size: float | None = None, provider: str = "webull",
               event_type: str = "snapshot") -> dict[str, Any]:
        symbol = symbol.upper()
        with self._lock:
            previous = self._values.get(symbol, {})
        payload = {
            "price": price,
            "volume": volume if volume is not None else previous.get("volume"),
            "bid": bid if bid is not None else previous.get("bid"),
            "ask": ask if ask is not None else previous.get("ask"),
            "bid_size": bid_size if bid_size is not None else previous.get("bid_size"),
            "ask_size": ask_size if ask_size is not None else previous.get("ask_size"),
            "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else timestamp,
            "received_at": time.time(), "provider": provider, "event_type": event_type,
        }
        with self._lock:
            self._values[symbol] = payload
            self._updated[symbol] = time.monotonic()
            if len(self._values) > self._max_symbols:
                oldest = min(self._updated, key=self._updated.get)
                self._values.pop(oldest, None)
                self._updated.pop(oldest, None)
            return deepcopy(payload)

    def get(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._values.get(symbol.upper())
            return deepcopy(value) if value is not None else None

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"symbols": len(self._values), "max_symbols": self._max_symbols}


live_quote_cache = LiveQuoteCache()
