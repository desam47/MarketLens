"""
WebSocket scanner push channel.

Clients connect to ``/api/scanner-stream/ws`` and subscribe to symbols they care
about. When the ingestion service picks up a fresh quote for a
subscribed symbol, the server re-runs the scanner and pushes the result
to every client subscribed to that symbol.

This is the live-update complement to the HTTP
``/api/scanner/{symbol}`` endpoint, which always triggers a fresh scan
on every request. Subscribing once here avoids that.

Concurrency model
-----------------
- The WebSocket receive loop runs on the FastAPI / asyncio event loop.
- ``engine_registry.dispatch_quote`` may run on any thread (the
  ingestion service runs on the loop, but other callers — tests, a CLI
  tool — may not). Our registered callback therefore bridges back to
  the loop via ``loop.call_soon_threadsafe``.
- The scanner's ``scan_symbol`` is synchronous and triggers YFinance
  network I/O; we run it via ``asyncio.to_thread`` so the event loop
  stays responsive while the scan is in flight.
- WebSocket sends happen only on the event loop.
"""
import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.market_data.services.engine_seeder import engine_registry
from backend.scanner.scanner import market_scanner

from .router import _result_to_dict

logger = logging.getLogger(__name__)

# Note: this WS endpoint sits at /api/scanner-stream/ws (NOT
# /api/scanner/ws) because the existing /api/scanner/{symbol} HTTP
# route would otherwise match the path as a literal symbol="ws" and
# answer the WebSocket upgrade with an HTTP response. A distinct
# prefix keeps the two route families from colliding.
router = APIRouter(prefix="/api/scanner-stream", tags=["scanner-ws"])


# ---------------------------------------------------------------------------
# Broadcast manager
# ---------------------------------------------------------------------------
# Tracks which WebSocket connections are subscribed to which symbols. The
# dispatcher reads this to decide whether a fresh quote is worth re-scanning
# for, and the WebSocket receive loop mutates it on subscribe / unsubscribe /
# disconnect.

class ScannerBroadcastManager:
    """Process-wide subscription registry for scanner WebSocket clients.

    ``_subs`` is keyed by uppercase symbol; each value is a set of
    WebSocket connections subscribed to it. Both keys and socket
    identities are normalized to a hashable form so callers can pass the
    WebSocket object directly without worrying about its
    identity-vs-equals semantics.
    """

    def __init__(self) -> None:
        # symbol.upper() -> set[id(websocket)]
        self._subs: dict[str, set[int]] = {}
        # id(websocket) -> websocket (for fan-out on broadcast)
        self._sockets: dict[int, WebSocket] = {}
        self._lock = asyncio.Lock()
        # Lifetime counters for metrics.
        self._connections_total: int = 0
        self._disconnections_total: int = 0
        self._messages_sent: int = 0
        self._broadcasts_total: int = 0

    async def subscribe(self, ws: WebSocket, symbol: str) -> None:
        async with self._lock:
            key = symbol.upper()
            ws_id = id(ws)
            self._sockets.setdefault(ws_id, ws)
            self._subs.setdefault(key, set()).add(ws_id)

    async def unsubscribe(self, ws: WebSocket, symbol: str) -> None:
        async with self._lock:
            key = symbol.upper()
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

    def has_subscribers(self, symbol: str) -> bool:
        """Cheap read used by the dispatcher (no lock). Best-effort."""
        return bool(self._subs.get(symbol.upper()))

    def get_subscribed_symbols(self) -> set[str]:
        return set(self._subs.keys())

    def get_stats(self) -> dict:
        """Return observability counters for the metrics endpoint.

        Note: ``_subs`` is mutated on the event loop without holding
        ``_lock`` here because the dict-snapshot is best-effort — the
        numbers may be slightly stale but are good enough for monitoring.
        """
        total_subscriptions = sum(len(ids) for ids in self._subs.values())
        return {
            "active_connections": len(self._sockets),
            "subscribed_symbols": len(self._subs),
            "total_subscriptions": total_subscriptions,
            "subs_by_symbol": {k: len(v) for k, v in self._subs.items()},
            "connections_total": self._connections_total,
            "disconnections_total": self._disconnections_total,
            "messages_sent_total": self._messages_sent,
            "broadcasts_total": self._broadcasts_total,
        }

    async def broadcast(self, symbol: str, payload: dict[str, Any]) -> None:
        """Send ``payload`` to every socket subscribed to ``symbol``.

        Sockets that fail to send (closed, broken) are removed from the
        registry so we don't leak them.
        """
        async with self._lock:
            key = symbol.upper()
            ws_ids = list(self._subs.get(key, ()))
            sockets = [self._sockets[ws_id] for ws_id in ws_ids if ws_id in self._sockets]

        self._broadcasts_total += 1
        dead: list[int] = []
        for ws in sockets:
            try:
                await ws.send_json(payload)
                self._messages_sent += 1
            except Exception as e:
                # Broken pipe / client closed mid-send. Mark for cleanup;
                # don't propagate — other subscribers should still get the
                # push.
                logger.debug(f"Broadcast send failed for {symbol}: {e}")
                dead.append(id(ws))

        if dead:
            async with self._lock:
                for ws_id in dead:
                    self._sockets.pop(ws_id, None)
                    for _, ids in self._subs.items():
                        if ws_id in ids:
                            ids.discard(ws_id)
                # Purge empty buckets.
                empty = [k for k, v in self._subs.items() if not v]
                for k in empty:
                    del self._subs[k]


# Process-wide singleton. Imported by the WebSocket endpoint and by the
# dispatcher below.
broadcast_manager = ScannerBroadcastManager()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
# Bridges ``engine_registry`` quote events into the broadcast manager.
# When ingestion pushes a fresh quote for a symbol, we re-scan and push
# the result to every WebSocket subscriber. The work runs on the asyncio
# event loop (captured at app startup); the actual scan runs in a thread
# so the loop isn't blocked.

class ScannerDispatcher:
    """Re-scan on every fresh quote and broadcast to subscribers.

    A single instance is registered with ``engine_registry`` for the
    ``"quote"`` kind. The callback is invoked synchronously from any
    thread; we hop to the event loop to do the work.
    """

    def __init__(self, manager: ScannerBroadcastManager, loop: asyncio.AbstractEventLoop):
        self._manager = manager
        self._loop = loop
        self._registered = False
        # Per-symbol cooldown: the dispatcher is called once per
        # engine_registry quote event, but quotes flow every ~30s. To
        # avoid re-scanning the *same* symbol more than once per
        # cooldown window (e.g. when multiple ingestion ticks land in
        # quick succession), we track the last successful scan
        # timestamp per symbol and skip re-runs that come in too soon.
        # Symbol → monotonic seconds of last successful scan.
        self._last_scan_at: dict[str, float] = {}
        # Cooldown in seconds. 30s matches the ingestion quote interval,
        # so a normal flow produces at most one scan per symbol per
        # cycle. Backtests / real-time bursts are still throttled.
        self._cooldown_seconds: float = 30.0

    def register(self) -> None:
        """Attach to ``engine_registry`` (idempotent)."""
        if self._registered:
            return
        engine_registry.register("quote", "__SCANNER_DISPATCHER__", self._on_quote)
        self._registered = True
        logger.info("ScannerDispatcher registered with engine_registry")

    def _on_quote(self, **_: Any) -> None:
        """Sync callback invoked by ``engine_registry.dispatch_quote``.

        The actual symbol is passed via the ``symbol`` kwarg by the
        dispatch machinery, but the registry calls the callback once
        per registered pair — so we don't know which symbol triggered
        us. Instead, we listen for *every* quote and re-scan only the
        symbols that have active subscribers.
        """
        if not self._manager.get_subscribed_symbols():
            return
        # Schedule broadcast of all currently-subscribed symbols on the
        # event loop. Hopping back to the loop is safe from any thread
        # because call_soon_threadsafe is exactly that bridge.
        try:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._scan_and_broadcast_all(),
            )
        except RuntimeError as e:
            # Loop is closed (process shutting down). Drop quietly.
            logger.debug(f"ScannerDispatcher could not schedule: {e}")

    async def _scan_and_broadcast_all(self) -> None:
        """Re-scan every subscribed symbol, respecting the per-symbol cooldown.

        Phase 20 perf fix: the dispatcher is triggered on every
        ``engine_registry`` quote event, but a single quote can fire
        many times per second under load (especially when the regime
        engine or scanner has multiple subscribers). Without throttling
        we re-scan + re-serialize + broadcast for every tick. The
        cooldown table (``_last_scan_at``) means each symbol is scanned
        at most once per ``_cooldown_seconds`` window.
        """
        import time as _time
        symbols = self._manager.get_subscribed_symbols()
        now = _time.monotonic()
        for symbol in symbols:
            # Cooldown gate: skip the scan (and the broadcast) if we
            # already produced a result for this symbol very recently.
            last = self._last_scan_at.get(symbol, 0.0)
            if now - last < self._cooldown_seconds:
                continue

            # Re-check after the scan starts — a subscription may have
            # been removed in the meantime, in which case we skip the
            # broadcast but still run the scan (the cached result is
            # useful for any later HTTP read).
            try:
                result = await asyncio.to_thread(market_scanner.scan_symbol, symbol)
            except Exception as e:
                logger.warning(f"Scan failed for {symbol}: {e}")
                # Still notify subscribers of the error.
                if self._manager.has_subscribers(symbol):
                    await self._manager.broadcast(
                        symbol,
                        {"type": "scan_error", "symbol": symbol, "error": str(e)},
                    )
                continue

            # Record successful scan time *before* the broadcast so
            # that a slow send doesn't get bypassed by a re-entry
            # that races with the same scan.
            self._last_scan_at[symbol] = _time.monotonic()

            if not self._manager.has_subscribers(symbol):
                continue

            try:
                payload = {
                    "type": "scan_result",
                    "symbol": symbol,
                    "data": _result_to_dict(result).model_dump(mode="json"),
                }
            except Exception as e:
                logger.error(f"Failed to serialize scan result for {symbol}: {e}")
                continue

            await self._manager.broadcast(symbol, payload)


# The dispatcher is initialized lazily at app startup (see ``install``).
# We need a running event loop to capture, so we can't construct it at
# import time.
_dispatcher: ScannerDispatcher | None = None


def install(loop: asyncio.AbstractEventLoop | None = None) -> ScannerDispatcher:
    """Install (or return) the process-wide ScannerDispatcher.

    Called from the WebSocket endpoint on first connect; the loop is
    captured then. Subsequent calls return the same dispatcher.
    """
    global _dispatcher
    if _dispatcher is None:
        target_loop = loop or asyncio.get_event_loop()
        _dispatcher = ScannerDispatcher(broadcast_manager, target_loop)
        _dispatcher.register()
    return _dispatcher


def reset_dispatcher() -> None:
    """Test-only: clear the global dispatcher so a fresh one is built."""
    global _dispatcher
    _dispatcher = None


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def scanner_websocket(websocket: WebSocket):
    """WebSocket scanner push channel.

    Protocol
    --------
    Client → Server (JSON):
        {"action": "subscribe",   "symbol": "AAPL"}
        {"action": "unsubscribe", "symbol": "AAPL"}
        {"action": "ping"}

    Server → Client (JSON):
        {"type": "scan_result",   "symbol": "...", "data": {...}}
        {"type": "scan_error",    "symbol": "...", "error": "..."}
        {"type": "subscribed",    "symbol": "..."}
        {"type": "unsubscribed",  "symbol": "..."}
        {"type": "error",         "message": "..."}
        {"type": "pong"}
    """
    await websocket.accept()
    # Ensure the dispatcher is wired in for the lifetime of this
    # process. Cheap to call repeatedly — the dispatcher memoizes
    # itself.
    install()
    broadcast_manager._connections_total += 1

    try:
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception as e:
                # Non-JSON or malformed frame: tell the client and keep
                # the connection alive.
                logger.debug(f"Bad WS frame: {e}")
                try:
                    await websocket.send_json(
                        {"type": "error", "message": f"Invalid frame: {e}"}
                    )
                except Exception:
                    break
                continue

            action = msg.get("action") if isinstance(msg, dict) else None
            symbol = (msg.get("symbol") if isinstance(msg, dict) else None)

            if action == "subscribe" and isinstance(symbol, str) and symbol:
                await broadcast_manager.subscribe(websocket, symbol)
                await websocket.send_json({"type": "subscribed", "symbol": symbol.upper()})
            elif action == "unsubscribe" and isinstance(symbol, str) and symbol:
                await broadcast_manager.unsubscribe(websocket, symbol)
                await websocket.send_json({"type": "unsubscribed", "symbol": symbol.upper()})
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
        # Make sure we don't leak the socket in the subscription set.
        await broadcast_manager.remove_socket(websocket)
        broadcast_manager._disconnections_total += 1
