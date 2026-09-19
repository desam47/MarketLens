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
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.market_data.services.engine_seeder import engine_registry

logger = logging.getLogger(__name__)

# Note: this WS endpoint sits at /api/realtime/ws (NOT
# /api/market-data/ws) to avoid colliding with the HTTP
# ``/api/market-data/{symbol}`` route family.
router = APIRouter(prefix="/api/realtime", tags=["realtime-ws"])

# All timestamps in responses → America/New_York (EST/EDT auto-handled).
_DASHBOARD_TZ = ZoneInfo("America/New_York")

# A client that stops reading must not be able to hold up delivery to everyone
# else, so each send is bounded and a socket that exceeds it is dropped.
_SEND_TIMEOUT_SECONDS = 5.0
# Bounds per-connection state: without a cap a single client could register
# unlimited (symbol, timeframe) keys and grow the process-wide registry.
_MAX_SUBSCRIPTIONS_PER_SOCKET = 200
# Client-supplied values are used as registry keys and forwarded to provider
# streams, so they are validated instead of trusted.
_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^=/]{1,20}$")
_TIMEFRAME_RE = re.compile(r"^\d{1,3}(?:m|h|d|wk)$")
# Providers whose live stream can be started on a client's behalf.
_SUPPORTED_STREAM_PROVIDERS = frozenset({"alpaca"})

# Strong references to in-flight broadcast tasks (the loop only keeps weak
# ones, so an unreferenced task can be garbage-collected mid-send).
_broadcast_tasks: set[asyncio.Task] = set()


def _to_dashboard_tz(value: datetime | None) -> str | None:
    from backend.utils.timezone import format_edt_iso

    return format_edt_iso(value)


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
        # id(websocket) -> the "SYMBOL:TF" keys it is subscribed to. The
        # reverse index makes disconnect cleanup and the per-socket cap
        # O(subscriptions of that socket) instead of a scan of every key.
        self._socket_keys: dict[int, set[str]] = {}
        self._lock = asyncio.Lock()
        self._connections_total: int = 0
        self._disconnections_total: int = 0
        self._messages_sent: int = 0
        self._broadcasts_total: int = 0

    # ── subscription management ────────────────────────────────────────────

    async def subscribe(self, ws: WebSocket, symbol: str, timeframe: str) -> bool:
        """Subscribe ``ws``; return True only if this is a *new* subscription."""
        async with self._lock:
            key = self._make_key(symbol, timeframe)
            ws_id = id(ws)
            self._sockets.setdefault(ws_id, ws)
            ids = self._subs.setdefault(key, set())
            if ws_id in ids:
                return False
            ids.add(ws_id)
            self._socket_keys.setdefault(ws_id, set()).add(key)
            return True

    async def unsubscribe(self, ws: WebSocket, symbol: str, timeframe: str) -> None:
        async with self._lock:
            key = self._make_key(symbol, timeframe)
            ws_id = id(ws)
            if key in self._subs:
                self._subs[key].discard(ws_id)
                if not self._subs[key]:
                    del self._subs[key]
            keys = self._socket_keys.get(ws_id)
            if keys is not None:
                keys.discard(key)

    async def remove_socket(self, ws: WebSocket) -> list[str]:
        """Drop ``ws`` from every subscription it was in.

        Returns the keys that were left with no subscribers, so the caller
        can tear down any provider stream that only existed for them.
        """
        async with self._lock:
            return self._drop_socket_locked(id(ws))

    def _drop_socket_locked(self, ws_id: int) -> list[str]:
        self._sockets.pop(ws_id, None)
        orphaned: list[str] = []
        for key in self._socket_keys.pop(ws_id, ()):
            ids = self._subs.get(key)
            if ids is None:
                continue
            ids.discard(ws_id)
            if not ids:
                del self._subs[key]
                orphaned.append(key)
        return orphaned

    def subscription_count(self, ws: WebSocket) -> int:
        return len(self._socket_keys.get(id(ws), ()))

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
        """Send ``payload`` to every socket subscribed to ``key``.

        Sends run concurrently, each bounded by ``_SEND_TIMEOUT_SECONDS``.
        Sequential, unbounded sends let one client with a full TCP buffer
        stall delivery to every other subscriber (and back up the ingestion
        callbacks feeding this method); a socket that times out or errors is
        dropped instead.
        """
        async with self._lock:
            ws_ids = list(self._subs.get(key, ()))
            sockets = [self._sockets[ws_id] for ws_id in ws_ids if ws_id in self._sockets]

        self._broadcasts_total += 1
        if not sockets:
            return

        async def _send(ws: WebSocket) -> int | None:
            try:
                await asyncio.wait_for(ws.send_json(payload), _SEND_TIMEOUT_SECONDS)
                return None
            except Exception as e:  # includes TimeoutError
                logger.debug(f"Realtime broadcast send failed for {key}: {e!r}")
                return id(ws)

        results = await asyncio.gather(*(_send(ws) for ws in sockets))
        dead = [ws_id for ws_id in results if ws_id is not None]
        self._messages_sent += len(sockets) - len(dead)

        if dead:
            orphaned: list[str] = []
            async with self._lock:
                for ws_id in dead:
                    orphaned.extend(self._drop_socket_locked(ws_id))
            await _release_provider_streams(orphaned)

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _make_key(symbol: str, timeframe: str) -> str:
        return f"{symbol.upper()}:{timeframe.lower()}"

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        parts = key.rsplit(":", 1)
        # Parenthesised: unparenthesised, the conditional bound only to
        # ``parts[1]`` and the fallback returned ``(key, ("", ""))``.
        return (parts[0], parts[1]) if len(parts) == 2 else (key, "")


# Process-wide singleton.
broadcast_manager = RealtimeBroadcastManager()


# ---------------------------------------------------------------------------
# Provider-aware broadcasting (v3.6 — Alpaca WebSocket integration)
# ---------------------------------------------------------------------------

# Tracks which provider stream is the active source for a given (symbol, tf).
# Keys: "{SYMBOL}:{TF}" → "alpaca" | "local" (local = engine_registry dispatcher).
_provider_stream_registry: dict[str, str] = {}
_provider_stream_lock = asyncio.Lock()


async def set_provider_stream(symbol: str, timeframe: str, provider: str) -> None:
    """Record that ``provider`` is the active stream source for (symbol, tf).

    Used by the Alpaca WebSocket client to claim ownership of bar pushes for
    a subscribed symbol. While the local ``engine_registry`` dispatcher is
    also active, this is informational only — the local dispatcher naturally
    short-circuits when ``has_subscribers`` returns false.

    Args:
        symbol: Ticker symbol (uppercased)
        timeframe: Timeframe string (e.g., "1m", "5m")
        provider: Provider name (e.g., "alpaca") or "local" to clear.
    """
    key = RealtimeBroadcastManager._make_key(symbol, timeframe)
    async with _provider_stream_lock:
        if provider == "local":
            _provider_stream_registry.pop(key, None)
        else:
            _provider_stream_registry[key] = provider


def get_provider_stream(symbol: str, timeframe: str) -> str | None:
    """Return the active provider stream source for (symbol, tf), or None."""
    key = RealtimeBroadcastManager._make_key(symbol, timeframe)
    return _provider_stream_registry.get(key)


async def _broadcast_bar(bar: Any, symbol: str | None = None, timeframe: str | None = None) -> None:
    """Provider-side helper: broadcast a bar to WebSocket subscribers.

    This is the function providers (e.g., Alpaca WebSocket client) call to
    push live bars into the existing realtime channel. The signature
    matches the ``on_bar`` callback contract used by ``AlpacaWebSocketClient``.

    Args:
        bar: ``Bar`` model from ``backend.models.market_data``
        symbol: Optional symbol override; defaults to ``bar.symbol``.
        timeframe: Optional timeframe override; defaults to ``bar.timeframe``.
    """
    sym = (symbol or bar.symbol).upper()
    tf = (timeframe or bar.timeframe).lower()
    if not broadcast_manager.has_subscribers(sym, tf):
        return
    bar_data = bar.model_dump()
    # Convert timestamp to the dashboard timezone for client display.
    bar_data["timestamp"] = (
        _to_dashboard_tz(bar.timestamp)
        if isinstance(bar.timestamp, datetime)
        else bar.timestamp
    )
    payload = {
        "type": "bar_update",
        "symbol": sym,
        "timeframe": tf,
        "data": bar_data,
    }
    key = RealtimeBroadcastManager._make_key(sym, tf)
    await broadcast_manager.broadcast(key, payload)


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
        def _schedule() -> None:
            # Runs on the loop thread. Creating the coroutine here (not in
            # the caller's thread) means a closed loop can't leave behind a
            # coroutine object that was never awaited.
            task = asyncio.ensure_future(self._broadcast_bar(symbol, timeframe, kwargs))
            _broadcast_tasks.add(task)
            task.add_done_callback(_broadcast_tasks.discard)

        try:
            self._loop.call_soon_threadsafe(_schedule)
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
                "timestamp": _to_dashboard_tz(data.get("timestamp")) if isinstance(data.get("timestamp"), datetime) else data.get("timestamp"),
            },
        }
        await self._manager.broadcast(key, payload)


# Lazy-initialized singleton + loop capture.
_dispatcher: RealtimeDispatcher | None = None


def install(loop: asyncio.AbstractEventLoop | None = None) -> RealtimeDispatcher:
    global _dispatcher
    if _dispatcher is None:
        from backend.api.trend.registry import _TREND_TIMEFRAMES

        target_loop = loop or asyncio.get_running_loop()
        _dispatcher = RealtimeDispatcher(broadcast_manager, target_loop)
        _dispatcher.register(_TREND_TIMEFRAMES)
    return _dispatcher


def reset() -> None:
    """Test-only: clear the global dispatcher."""
    global _dispatcher
    _dispatcher = None


# ---------------------------------------------------------------------------
# Provider stream management (v3.6)
# ---------------------------------------------------------------------------

def _call_provider(provider: str, method: str, symbol: str, timeframe: str) -> None:
    """Invoke ``subscribe``/``unsubscribe`` on a provider instance, if it has one."""
    try:
        from backend.market_data.services.manager import market_data_manager

        provider_instance = market_data_manager.providers.get(provider)
        if provider_instance and hasattr(provider_instance, method):
            getattr(provider_instance, method)(symbol, timeframe)
            logger.info("%s stream %s for %s (%s)", provider, method, symbol, timeframe)
    except Exception:
        logger.exception("Failed to %s %s stream for %s", method, provider, symbol)


async def _maybe_start_provider_stream(provider: str, symbol: str, timeframe: str) -> None:
    """Start ``provider``'s stream for (symbol, tf) unless it is already running.

    ``_provider_stream_registry`` is the single source of truth for "which
    keys have a live provider stream", so this is idempotent no matter how
    many sockets (or repeated subscribe messages) ask for the same key.
    """
    if provider not in _SUPPORTED_STREAM_PROVIDERS:
        return
    sym_upper = symbol.upper()
    key = RealtimeBroadcastManager._make_key(sym_upper, timeframe)
    async with _provider_stream_lock:
        if _provider_stream_registry.get(key) == provider:
            return
        _provider_stream_registry[key] = provider
    _call_provider(provider, "subscribe", sym_upper, timeframe)


async def _maybe_stop_provider_stream(provider: str, symbol: str, timeframe: str) -> None:
    """Stop ``provider``'s stream for (symbol, tf) if one is running.

    Callers invoke this once the key has no local subscribers left.
    """
    sym_upper = symbol.upper()
    key = RealtimeBroadcastManager._make_key(sym_upper, timeframe)
    async with _provider_stream_lock:
        if _provider_stream_registry.get(key) != provider:
            return
        del _provider_stream_registry[key]
    _call_provider(provider, "unsubscribe", sym_upper, timeframe)


async def _release_provider_streams(orphaned_keys: list[str]) -> None:
    """Stop the provider streams for keys that just lost their last subscriber."""
    for key in orphaned_keys:
        provider = _provider_stream_registry.get(key)
        if provider:
            symbol, timeframe = RealtimeBroadcastManager._split_key(key)
            await _maybe_stop_provider_stream(provider, symbol, timeframe)


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

            if not isinstance(msg, dict):
                await websocket.send_json(
                    {"type": "error", "message": "Frame must be a JSON object"}
                )
                continue

            action = msg.get("action")
            symbol = msg.get("symbol")
            timeframe = msg.get("timeframe")
            provider = msg.get("provider")

            if action in ("subscribe", "unsubscribe"):
                sym = symbol.strip().upper() if isinstance(symbol, str) else ""
                tf = timeframe.strip().lower() if isinstance(timeframe, str) and timeframe else "1m"
                if not _SYMBOL_RE.match(sym):
                    await websocket.send_json(
                        {"type": "error", "message": f"Invalid symbol: {symbol!r}"}
                    )
                    continue
                if not _TIMEFRAME_RE.match(tf):
                    await websocket.send_json(
                        {"type": "error", "message": f"Invalid timeframe: {timeframe!r}"}
                    )
                    continue

            if action == "subscribe":
                if (
                    broadcast_manager.subscription_count(websocket) >= _MAX_SUBSCRIPTIONS_PER_SOCKET
                    and not broadcast_manager.has_subscribers(sym, tf)
                ):
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Subscription limit reached ({_MAX_SUBSCRIPTIONS_PER_SOCKET})",
                    })
                    continue
                await broadcast_manager.subscribe(websocket, sym, tf)
                # If client requested a supported provider, route the stream
                # through it (v3.6 — currently "alpaca"). Idempotent: repeated
                # subscribes, or several sockets on one key, start it once.
                stream_provider = (
                    provider
                    if isinstance(provider, str) and provider in _SUPPORTED_STREAM_PROVIDERS
                    else None
                )
                if stream_provider:
                    await _maybe_start_provider_stream(stream_provider, sym, tf)
                await websocket.send_json({
                    "type": "subscribed",
                    "symbol": sym,
                    "timeframe": tf,
                    "provider": stream_provider or "local",
                })
            elif action == "unsubscribe":
                await broadcast_manager.unsubscribe(websocket, sym, tf)
                # If no more local subscribers and a provider stream is active,
                # tear it down.
                if not broadcast_manager.has_subscribers(sym, tf):
                    await _release_provider_streams([broadcast_manager._make_key(sym, tf)])
                await websocket.send_json({
                    "type": "unsubscribed",
                    "symbol": sym,
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
        # A disconnect used to leave provider streams running for symbols
        # nobody watches any more; release the ones this socket held last.
        orphaned = await broadcast_manager.remove_socket(websocket)
        await _release_provider_streams(orphaned)
        broadcast_manager._disconnections_total += 1
