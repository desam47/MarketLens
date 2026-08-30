"""
Realtime bar + trend WebSocket push channel.

Clients connect to ``/api/realtime/ws`` and subscribe to symbol+timeframe
combinations. When the ingestion service produces a fresh bar for a
subscribed symbol, the server pushes the updated bar to every client
subscribed to that symbol.

This complements the HTTP endpoints (``/api/bar``, ``/api/trend``) by
eliminating the polling loop — the browser receives new candles as soon
as the backend ingests them.

Concurrency model
-----------------
- The WebSocket receive loop runs on the FastAPI / asyncio event loop.
- ``engine_registry.dispatch_bar`` may run on any thread (the
  ingestion service runs on the loop, but other callers — tests, a CLI
  tool — may not). Our registered callback therefore bridges back to
  the loop via ``loop.call_soon_threadsafe``.
- WebSocket sends happen only on the event loop.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.market_data.services.engine_seeder import engine_registry

logger = logging.getLogger(__name__)

# Note: this WS endpoint sits at /api/realtime/ws (NOT
# /api/market-data/ws) to avoid colliding with the HTTP
# ``/api/market-data/{symbol}`` route family.
router = APIRouter(prefix="/api/realtime", tags=["realtime-ws"])


# ---------------------------------------------------------------------------
# Broadcast manager
# ---------------------------------------------------------------------------

class RealtimeBroadcastManager:
    """Process-wide subscription registry for realtime WebSocket clients.

    ``_subs`` is keyed by "symbol:timeframe" (uppercase). Each value is a
    set of WebSocket connection ids subscribed to it.
    """

    def __init__(self) -> None:
        # "SYMBOL:TF" -> set of WebSocket ids
        self._subs: dict[str, set[int]] = {}
        # id(websocket) -> websocket
        self._sockets: dict[int, WebSocket] = {}
        self._lock = asyncio.Lock()
        self._connections_total: int = 0
        self._disconnections_total: int = 0
        self._messages_sent: int = 0
        self._broadcasts_total: int = 0

    # ── subscription management ────────────────────────────────────────────

    async def subscribe(self, ws: WebSocket, symbol: str, timeframe: str) -> None:
        async with self._lock:
            key = self._make_key(symbol, timeframe)
            ws_id = id(ws)
            self._sockets.setdefault(ws_id, ws)
            self._subs.setdefault(key, set()).add(ws_id)

    async def unsubscribe(self, ws: WebSocket, symbol: str, timeframe: str) -> None:
        async with self._lock:
            key = self._make_key(symbol, timeframe)
            ws_id = id(ws)
            if key in self._subs:
                self._subs[key].discard(ws_id)
                if not self._subs[key]:
                    del self._subs[key]

    async def remove_socket(self, ws: WebSocket) -> None:
        """Drop ``ws`` from every subscription it was in."""
        async with self._lock:
            ws_id = id(ws)
            self._sockets.pop(ws_id, None)
            empty_keys: list[str] = []
            for key, ids in self._subs.items():
                if ws_id in ids:
                    ids.discard(ws_id)
                    if not ids:
                        empty_keys.append(key)
            for key in empty_keys:
                del self._subs[key]

    def has_subscribers(self, symbol: str, timeframe: str) -> bool:
        """Cheap read used by the dispatcher (no lock). Best-effort."""
        return bool(self._subs.get(self._make_key(symbol, timeframe)))

    def get_subscribed_symbols(self) -> set[str]:
        """Return the set of unique symbols that have any subscribers."""
        keys = set(self._subs.keys())
        return {k.split(":")[0] for k in keys}

    def get_stats(self) -> dict:
        total_subscriptions = sum(len(ids) for ids in self._subs.values())
        return {
            "active_connections": len(self._sockets),
            "subscribed_keys": len(self._subs),
            "total_subscriptions": total_subscriptions,
            "connections_total": self._connections_total,
            "disconnections_total": self._disconnections_total,
            "messages_sent_total": self._messages_sent,
            "broadcasts_total": self._broadcasts_total,
        }

    # ── broadcast ─────────────────────────────────────────────────────────

    async def broadcast(self, key: str, payload: dict[str, Any]) -> None:
        """Send ``payload`` to every socket subscribed to ``key``."""
        async with self._lock:
            ws_ids = list(self._subs.get(key, ()))
            sockets = [self._sockets[ws_id] for ws_id in ws_ids if ws_id in self._sockets]

        self._broadcasts_total += 1
        dead: list[int] = []
        for ws in sockets:
            try:
                await ws.send_json(payload)
                self._messages_sent += 1
            except Exception as e:
                logger.debug(f"Realtime broadcast send failed for {key}: {e}")
                dead.append(id(ws))

        if dead:
            async with self._lock:
                for ws_id in dead:
                    self._sockets.pop(ws_id, None)
                    for _, ids in self._subs.items():
                        if ws_id in ids:
                            ids.discard(ws_id)
                empty = [k for k, v in self._subs.items() if not v]
                for k in empty:
                    del self._subs[k]

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _make_key(symbol: str, timeframe: str) -> str:
        return f"{symbol.upper()}:{timeframe.lower()}"

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        parts = key.rsplit(":", 1)
        return parts[0], parts[1] if len(parts) == 2 else ("", "")


# Process-wide singleton.
broadcast_manager = RealtimeBroadcastManager()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class RealtimeDispatcher:
    """Bridge ``engine_registry`` bar events into the broadcast manager.

    Registered for every ``"bar:{tf}"`` kind in engine_registry. When a
    bar arrives, we broadcast it to every WebSocket subscriber for that
    symbol+timeframe.
    """

    def __init__(self, manager: RealtimeBroadcastManager, loop: asyncio.AbstractEventLoop):
        self._manager = manager
        self._loop = loop
        self._registered = False

    def register(self, timeframes: tuple[str, ...]) -> None:
        """Attach to ``engine_registry`` for all tracked timeframes (idempotent)."""
        if self._registered:
            return
        for tf in timeframes:
            engine_registry.register(f"bar:{tf}", "__REALTIME_DISPATCHER__", self._on_bar)
        self._registered = True
        logger.info(f"RealtimeDispatcher registered for timeframes: {timeframes}")

    def _on_bar(self, **kwargs: Any) -> None:
        """Sync callback invoked by ``engine_registry.dispatch_bar``.

        ``dispatch_bar`` passes the bar's symbol/timeframe + OHLCV as kwargs.
        We only need symbol+timeframe for routing to subscribers; the rest is
        forwarded as the broadcast payload.
        """
        symbol = kwargs.get("symbol", "")
        timeframe = kwargs.get("timeframe", "")
        if not symbol:
            return
        if not self._manager.has_subscribers(symbol, timeframe):
            return
        try:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._broadcast_bar(symbol, timeframe, kwargs),
            )
        except RuntimeError as e:
            logger.debug(f"RealtimeDispatcher could not schedule: {e}")

    async def _broadcast_bar(self, symbol: str, timeframe: str, data: dict[str, Any]) -> None:
        key = RealtimeBroadcastManager._make_key(symbol, timeframe)
        payload = {
            "type": "bar_update",
            "symbol": symbol,
            "timeframe": timeframe,
            "data": {
                "open": data.get("open"),
                "high": data.get("high"),
                "low": data.get("low"),
                "close": data.get("close"),
                "volume": data.get("volume"),
                "timestamp": (
                    data.get("timestamp").isoformat()
                    if isinstance(data.get("timestamp"), datetime)
                    else data.get("timestamp")
                ),
            },
        }
        await self._manager.broadcast(key, payload)


# Lazy-initialized singleton + loop capture.
_dispatcher: RealtimeDispatcher | None = None


def install(loop: asyncio.AbstractEventLoop | None = None) -> RealtimeDispatcher:
    global _dispatcher
    if _dispatcher is None:
        from backend.api.trend.router import _TREND_TIMEFRAMES

        target_loop = loop or asyncio.get_running_loop()
        _dispatcher = RealtimeDispatcher(broadcast_manager, target_loop)
        _dispatcher.register(_TREND_TIMEFRAMES)
    return _dispatcher


def reset() -> None:
    """Test-only: clear the global dispatcher."""
    global _dispatcher
    _dispatcher = None


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket):
    """WebSocket realtime bar push channel.

    Protocol
    --------
    Client → Server (JSON):
        {"action": "subscribe",   "symbol": "AAPL", "timeframe": "1m"}
        {"action": "unsubscribe", "symbol": "AAPL", "timeframe": "1m"}
        {"action": "ping"}

    Server → Client (JSON):
        {"type": "bar_update",   "symbol": "...", "timeframe": "...", "data": {...}}
        {"type": "subscribed",   "symbol": "...", "timeframe": "..."}
        {"type": "unsubscribed", "symbol": "...", "timeframe": "..."}
        {"type": "error",        "message": "..."}
        {"type": "pong"}
    """
    await websocket.accept()
    install()  # Ensure dispatcher is registered for this process lifetime.
    broadcast_manager._connections_total += 1

    try:
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.debug(f"Bad WS frame: {e}")
                try:
                    await websocket.send_json(
                        {"type": "error", "message": f"Invalid frame: {e}"}
                    )
                except Exception:
                    break
                continue

            action = msg.get("action") if isinstance(msg, dict) else None
            symbol = msg.get("symbol") if isinstance(msg, dict) else None
            timeframe = msg.get("timeframe") if isinstance(msg, dict) else None

            if action == "subscribe" and isinstance(symbol, str) and symbol:
                tf = (timeframe or "1m") if isinstance(timeframe, str) else "1m"
                await broadcast_manager.subscribe(websocket, symbol, tf)
                await websocket.send_json({
                    "type": "subscribed",
                    "symbol": symbol.upper(),
                    "timeframe": tf,
                })
            elif action == "unsubscribe" and isinstance(symbol, str) and symbol:
                tf = (timeframe or "1m") if isinstance(timeframe, str) else "1m"
                await broadcast_manager.unsubscribe(websocket, symbol, tf)
                await websocket.send_json({
                    "type": "unsubscribed",
                    "symbol": symbol.upper(),
                    "timeframe": tf,
                })
            elif action == "ping":
                await websocket.send_json({"type": "pong"})
            elif action in (None, ""):
                await websocket.send_json(
                    {"type": "error", "message": "Missing 'action'"}
                )
            else:
                await websocket.send_json(
                    {"type": "error", "message": f"Unknown action: {action!r}"}
                )
    except WebSocketDisconnect:
        pass
    finally:
        await broadcast_manager.remove_socket(websocket)
        broadcast_manager._disconnections_total += 1
