"""Bounded in-memory retention of streamed trades and BBO updates for replay."""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from backend.utils.timezone import format_edt_iso, to_ny


class TickReplayStore:
    """Keep a local, finite tick history; it never calls a provider to replay."""

    def __init__(
        self,
        max_events_per_symbol: int = 10_000,
        retention_seconds: int = 7_200,
        *,
        persist: bool = False,
    ) -> None:
        self._events: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._max_events = max_events_per_symbol
        self._retention_seconds = retention_seconds
        self._lock = threading.RLock()
        self._persist = persist
        self._pending: deque[dict[str, Any]] = deque()
        self._wake = threading.Event()
        if persist:
            self._writer = threading.Thread(
                target=self._persistence_loop, name="tick-replay-writer", daemon=True
            )
            self._writer.start()

    def record(self, symbol: str, payload: dict[str, Any]) -> None:
        event = {
            key: payload.get(key)
            for key in (
                "price",
                "volume",
                "bid",
                "ask",
                "bid_size",
                "ask_size",
                "spread_bps",
                "spread_change_bps",
                "timestamp",
                "received_at",
                "provider",
                "event_type",
            )
        }
        event["symbol"] = symbol.upper()
        now = time.time()
        event["_recorded_at"] = now
        with self._lock:
            events = self._events[event["symbol"]]
            events.append(event)
            cutoff = now - self._retention_seconds
            while events and (len(events) > self._max_events or events[0]["_recorded_at"] < cutoff):
                events.popleft()
            if self._persist:
                self._pending.append(event.copy())
                if len(self._pending) >= 100:
                    self._wake.set()

    def _persistence_loop(self) -> None:
        while True:
            self._wake.wait(1.0)
            self._wake.clear()
            self._flush_persisted()

    def _flush_persisted(self) -> None:
        with self._lock:
            if not self._pending:
                return
            pending = list(self._pending)
            self._pending.clear()
        try:
            from backend.database import SessionLocal
            from backend.models.market_data_sql import TickReplayEventModel

            db = SessionLocal()
            try:
                db.add_all(
                    [
                        TickReplayEventModel(
                            symbol=event["symbol"],
                            event_timestamp=str(event.get("timestamp"))
                            if event.get("timestamp")
                            else None,
                            received_at=float(event["_recorded_at"]),
                            payload=json.dumps(event, default=str),
                        )
                        for event in pending
                    ]
                )
                cutoff = time.time() - self._retention_seconds
                db.query(TickReplayEventModel).filter(
                    TickReplayEventModel.received_at < cutoff
                ).delete(synchronize_session=False)
                db.commit()
            finally:
                db.close()
        except Exception:
            # Restore the batch so a transient DB/migration issue is recoverable.
            with self._lock:
                self._pending.extendleft(reversed(pending))

    def get(self, symbol: str, limit: int = 2_000) -> list[dict[str, Any]]:
        with self._lock:
            events = deepcopy(list(self._events.get(symbol.upper(), ()))[-limit:])
        if self._persist and len(events) < limit:
            try:
                from backend.database import SessionLocal
                from backend.models.market_data_sql import TickReplayEventModel

                db = SessionLocal()
                try:
                    rows = (
                        db.query(TickReplayEventModel)
                        .filter(TickReplayEventModel.symbol == symbol.upper())
                        .order_by(TickReplayEventModel.received_at.desc())
                        .limit(limit)
                        .all()
                    )
                    persisted = [json.loads(row.payload) for row in rows]
                    seen = {event.get("_recorded_at") for event in events}
                    events = [
                        event for event in persisted[::-1] if event.get("_recorded_at") not in seen
                    ] + events
                    events = events[-limit:]
                finally:
                    db.close()
            except Exception:
                pass
        for event in events:
            event.pop("_recorded_at", None)
        return events


def reconstruct_tick_signals(
    symbol: str,
    events: list[dict[str, Any]],
    *,
    timeframe: str = "1m",
    warmup: int = 0,
) -> list[dict[str, Any]]:
    """Aggregate retained ticks and replay causal signal state candle by candle."""
    if timeframe != "1m":
        raise ValueError("tick reconstruction currently supports only the 1m timeframe")

    buckets: dict[datetime, dict[str, Any]] = {}
    for event in events:
        price = event.get("price")
        timestamp = event.get("timestamp")
        if not isinstance(price, (int, float)) or price <= 0 or not timestamp:
            continue
        try:
            parsed = (
                timestamp
                if isinstance(timestamp, datetime)
                else datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            )
        except (TypeError, ValueError):
            continue
        local = to_ny(parsed)
        if local is None:
            continue
        bucket = local.replace(second=0, microsecond=0)
        row = buckets.setdefault(
            bucket,
            {
                "open": float(price),
                "high": float(price),
                "low": float(price),
                "close": float(price),
                "volume": 0,
                "ticks": 0,
            },
        )
        row["high"] = max(row["high"], float(price))
        row["low"] = min(row["low"], float(price))
        row["close"] = float(price)
        row["ticks"] += 1
        if str(event.get("event_type", "")).lower() in {"trade", "time_and_sales", "tape"}:
            volume = event.get("volume")
            if isinstance(volume, (int, float)) and volume > 0:
                row["volume"] += int(volume)

    if not buckets:
        return []

    bars = [
        SimpleNamespace(timestamp=timestamp, **values)
        for timestamp, values in sorted(buckets.items())
    ]
    from backend.services.signal_replay import label_columns, replay_trend_scores, state_from_score

    scores = replay_trend_scores(symbol.upper(), timeframe, bars, warmup=max(0, warmup))
    reconstructed: list[dict[str, Any]] = []
    for bar in bars:
        score = scores.get(bar.timestamp)
        reconstructed.append(
            {
                "timestamp": format_edt_iso(bar.timestamp),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "tick_count": bar.ticks,
                "signal_score": score,
                "signal_state": state_from_score(score) if score is not None else None,
                "signal": label_columns(score),
            }
        )
    return reconstructed


tick_replay_store = TickReplayStore(persist=True)
