"""Bounded in-memory retention of streamed trades and BBO updates for replay."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from copy import deepcopy
from typing import Any


class TickReplayStore:
    """Keep a local, finite tick history; it never calls a provider to replay."""

    def __init__(self, max_events_per_symbol: int = 10_000, retention_seconds: int = 7_200) -> None:
        self._events: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._max_events = max_events_per_symbol
        self._retention_seconds = retention_seconds
        self._lock = threading.RLock()

    def record(self, symbol: str, payload: dict[str, Any]) -> None:
        event = {key: payload.get(key) for key in (
            "price", "volume", "bid", "ask", "bid_size", "ask_size", "spread_bps",
            "spread_change_bps", "timestamp", "received_at", "provider", "event_type",
        )}
        event["symbol"] = symbol.upper()
        now = time.time()
        event["_recorded_at"] = now
        with self._lock:
            events = self._events[event["symbol"]]
            events.append(event)
            cutoff = now - self._retention_seconds
            while events and (len(events) > self._max_events or events[0]["_recorded_at"] < cutoff):
                events.popleft()

    def get(self, symbol: str, limit: int = 2_000) -> list[dict[str, Any]]:
        with self._lock:
            events = deepcopy(list(self._events.get(symbol.upper(), ()))[-limit:])
        for event in events:
            event.pop("_recorded_at", None)
        return events


tick_replay_store = TickReplayStore()
