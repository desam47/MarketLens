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
               event_type: str = "snapshot") -> dict[str, Any] | None:
        symbol = symbol.upper()
        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else timestamp
        with self._lock:
            # Hold the lock for the full read-build-write cycle so concurrent
            # updates for the same symbol can't race between the two acquisitions
            # that the previous split-lock design had.
            previous = self._values.get(symbol, {})
            payload = {
                "price": price,
                "volume": volume if volume is not None else previous.get("volume"),
                "bid": bid if bid is not None else previous.get("bid"),
                "ask": ask if ask is not None else previous.get("ask"),
                "bid_size": bid_size if bid_size is not None else previous.get("bid_size"),
                "ask_size": ask_size if ask_size is not None else previous.get("ask_size"),
                "timestamp": ts_str,
                "received_at": time.time(), "provider": provider, "event_type": event_type,
            }
            # Keep lightweight BBO-derived values beside the raw quote.  They are
            # shared by the scanner, alerts, and UI, so consumers do not each need
            # their own stateful spread calculation.
            current_bid, current_ask = payload["bid"], payload["ask"]
            if (
                isinstance(current_bid, (int, float))
                and isinstance(current_ask, (int, float))
                and current_bid > 0
                and current_ask >= current_bid
            ):
                midpoint = (current_bid + current_ask) / 2
                spread_bps = ((current_ask - current_bid) / midpoint) * 10_000 if midpoint else 0.0
                payload["spread_bps"] = round(spread_bps, 3)
                previous_spread = previous.get("spread_bps")
                if isinstance(previous_spread, (int, float)):
                    payload["spread_change_bps"] = round(spread_bps - previous_spread, 3)
            # Webull can replay a message after reconnect. Suppress an exact
            # duplicate before it fans out to every browser and tape engine.
            if previous and all(previous.get(key) == payload.get(key) for key in (
                "price", "volume", "bid", "ask", "bid_size", "ask_size",
                "timestamp", "provider", "event_type",
            )):
                return None
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
