"""
WebSocket scanner push channel.

Clients connect to ``/api/scanner-stream/ws`` and subscribe to symbols they care
about. When the shared market-data layer receives a fresh quote or
microstructure event for a subscribed symbol, the server marks only that
symbol dirty, debounces bursts, re-runs the scanner, and pushes the result to
every client subscribed to that symbol.

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
import re
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.market_data.services.engine_seeder import engine_registry
from backend.scanner.scanner import market_scanner

from .router import _result_to_lite_dict

logger = logging.getLogger(__name__)

# Note: this WS endpoint sits at /api/scanner-stream/ws (NOT
# /api/scanner/ws) because the existing /api/scanner/{symbol} HTTP
# route would otherwise match the path as a literal symbol="ws" and
# answer the WebSocket upgrade with an HTTP response. A distinct
# prefix keeps the two route families from colliding.
router = APIRouter(prefix="/api/scanner-stream", tags=["scanner-ws"])

# Scanner pushes trigger a real market scan, so keep one local browser from
# accidentally fanning provider work out to an unbounded symbol set. The
# regular realtime socket uses the same 50-subscription ceiling.
_MAX_SUBSCRIPTIONS_PER_SOCKET = 50
# Yahoo's index symbols use a leading caret (for example ``^VIX``), which
# MarketLens already supports elsewhere. Keep that valid while rejecting paths,
# whitespace, and arbitrary provider-query strings.
_SYMBOL_RE = re.compile(r"^\^?[A-Z][A-Z0-9.-]{0,9}$")


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

    def subscription_count(self, ws: WebSocket) -> int:
        """Return the number of distinct symbols subscribed by ``ws``."""
        ws_id = id(ws)
        return sum(ws_id in subscribers for subscribers in self._subs.values())

    def has_subscription(self, ws: WebSocket, symbol: str) -> bool:
        """True when ``ws`` is already subscribed to ``symbol``."""
        return id(ws) in self._subs.get(symbol.upper(), set())

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
# Bridges ``engine_registry`` quote and microstructure events into the
# broadcast manager. A single debounced task drains dirty symbols, so a burst
# of ticks cannot create one asyncio task per event. The work runs on the
# asyncio event loop (captured at app startup); the actual scan runs in a
# thread so the loop isn't blocked.


class ScannerDispatcher:
    """Debounced, symbol-specific scanner refresh dispatcher.

    A single instance is registered with ``engine_registry`` for quote and
    microstructure events. Callbacks are invoked synchronously from any
    thread; we hop to the event loop to coalesce events and do the work.
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
        self._last_broadcast_at: str | None = None
        # A single worker drains this set after a short debounce window so
        # quote/BBO/tape bursts are coalesced into one symbol-specific scan.
        self._dirty_symbols: set[str] = set()
        self._debounce_task: asyncio.Task | None = None
        self._debounce_seconds: float = 0.25
        # Cooldown in seconds. 30s matches the ingestion quote interval,
        # so a normal flow produces at most one scan per symbol per
        # cycle. Backtests / real-time bursts are still throttled.
        self._cooldown_seconds: float = 30.0

    def register(self) -> None:
        """Attach to ``engine_registry`` (idempotent).

        The dispatcher listens for *every* quote event so it can re-scan
        whatever symbols currently have WebSocket subscribers. The
        registry keys callbacks by ``(kind, symbol)`` though, so we
        can't just register once for a wildcard — we have to register
        against each symbol the ingestion service knows about. Symbols
        added to the watchlist after registration are picked up via
        ``register_for_symbol()`` from the watchlist API endpoints.
        """
        if self._registered:
            return
        # Pre-register against any symbols already being ingested so the
        # first quote after server start fires the dispatcher. New
        # symbols are added as they're added to the watchlist via
        # ``register_for_symbol``.
        try:
            from backend.market_data.services.ingestion_service import ingestion_service

            for sym in ingestion_service.symbols:
                engine_registry.register("quote", sym, self._on_quote)
                engine_registry.register("microstructure", sym, self._on_microstructure)
        except Exception as e:
            logger.debug(f"Initial symbol registration skipped: {e}")
        self._registered = True
        logger.info("ScannerDispatcher registered with engine_registry")

    def register_for_symbol(self, symbol: str) -> None:
        """Register the dispatcher for a single symbol (called when a
        symbol is added to the watchlist at runtime)."""
        if not self._registered:
            self.register()
        normalized = symbol.upper()
        engine_registry.register("quote", normalized, self._on_quote)
        engine_registry.register("microstructure", normalized, self._on_microstructure)

    def request_scan(self, symbol: str) -> None:
        """Request an initial or manually-triggered scan for one symbol."""
        self._mark_dirty(symbol.upper())

    def _on_quote(self, symbol: str | None = None, **_: Any) -> None:
        """Sync callback invoked by ``engine_registry.dispatch_quote``."""
        self._queue_event(symbol)

    def _on_microstructure(self, symbol: str | None = None, **_: Any) -> None:
        """Sync callback invoked by live BBO and Time & Sales events."""
        self._queue_event(symbol)

    def _queue_event(self, symbol: str | None) -> None:
        """Bridge a provider-thread event onto the scanner's event loop."""
        if not symbol:
            return
        normalized = symbol.upper()
        if not self._manager.has_subscribers(normalized):
            return
        try:
            self._loop.call_soon_threadsafe(self._mark_dirty, normalized)
        except RuntimeError as e:
            # Loop is closed (process shutting down). Drop quietly.
            logger.debug(f"ScannerDispatcher could not schedule: {e}")

    def _mark_dirty(self, symbol: str) -> None:
        """Add one symbol to the coalesced refresh set."""
        normalized = symbol.upper()
        if not self._manager.has_subscribers(normalized):
            return
        self._dirty_symbols.add(normalized)
        task = self._debounce_task
        if task is None or task.done():
            self._debounce_task = self._loop.create_task(self._drain_dirty())

    async def _drain_dirty(self) -> None:
        """Drain dirty symbols with debounce and per-symbol cooldowns."""
        try:
            await asyncio.sleep(self._debounce_seconds)
            while self._dirty_symbols:
                import time as _time

                now = _time.monotonic()
                pending = {
                    symbol
                    for symbol in self._dirty_symbols
                    if self._manager.has_subscribers(symbol)
                }
                self._dirty_symbols.intersection_update(pending)
                if not pending:
                    return

                to_scan = [
                    symbol
                    for symbol in sorted(pending)
                    if now - self._last_scan_at.get(symbol, 0.0) >= self._cooldown_seconds
                ]
                if not to_scan:
                    next_scan_at = min(
                        self._last_scan_at.get(symbol, now) + self._cooldown_seconds
                        for symbol in pending
                    )
                    await asyncio.sleep(max(self._debounce_seconds, next_scan_at - now))
                    continue

                for symbol in to_scan:
                    self._dirty_symbols.discard(symbol)
                await self._scan_and_broadcast_all(set(to_scan))
                if self._dirty_symbols:
                    await asyncio.sleep(self._debounce_seconds)
        finally:
            self._debounce_task = None
            # A callback may arrive as the worker is exiting. Ensure it gets
            # a new worker instead of leaving a dirty symbol stranded.
            if self._dirty_symbols and not self._loop.is_closed():
                self._debounce_task = self._loop.create_task(self._drain_dirty())

    async def _scan_and_broadcast_all(self, symbols: set[str] | None = None) -> None:
        """Scan selected subscribed symbols, respecting per-symbol cooldown.

        Scans are batched and run concurrently via ``scan_symbols_async``,
        which pre-fetches bars/quotes for all symbols in one batch
        (avoiding the N+1 DB-session pattern) and then runs each
        ``scan_symbol`` on a worker thread via ``asyncio.gather``.
        This turns N sequential HTTP/DB round-trips into a single
        batch fetch + parallel scans.
        """
        import time as _time

        symbols = symbols or self._manager.get_subscribed_symbols()
        now = _time.monotonic()

        # Cooldown gate: partition subscribed symbols into those that need
        # a fresh scan and those still under cooldown.
        to_scan: list[str] = []
        still_cooling: list[str] = []
        for symbol in symbols:
            last = self._last_scan_at.get(symbol, 0.0)
            if now - last < self._cooldown_seconds:
                still_cooling.append(symbol)
            else:
                to_scan.append(symbol)

        if not to_scan:
            # Nothing to scan — but subscribers may still be waiting for
            # initial results. If we've never broadcast any of them, the
            # frontend shows "waiting" indefinitely, so let a single
            # still-cooling symbol through as a best-effort push.
            if still_cooling and self._last_broadcast_at is None:
                to_scan = still_cooling  # will still respect cooldown below
            else:
                return

        # Record scan timestamps BEFORE the batch so re-entrant quote events
        # don't trigger duplicate scans of the same symbols.
        scan_time = _time.monotonic()
        for symbol in to_scan:
            self._last_scan_at[symbol] = scan_time

        # Batch pre-fetch + concurrent scan: scan_symbols_async fetches
        # bars/quotes for all symbols in one batch, then runs each
        # scan_symbol in a worker thread via asyncio.gather.
        try:
            results = await market_scanner.scan_symbols_async(to_scan)
        except Exception as e:
            logger.error(f"Batch scan failed for {len(to_scan)} symbols: {e}")
            # Tell subscribers instead of leaving them on "waiting": the
            # protocol documents a ``scan_error`` frame (and the client types
            # it), and the pre-batch dispatcher sent one per failed symbol.
            # The scan timestamps recorded above keep this bounded by the
            # cooldown, so a persistent failure isn't re-pushed per quote tick.
            for symbol in to_scan:
                if self._manager.has_subscribers(symbol):
                    await self._manager.broadcast(
                        symbol,
                        {"type": "scan_error", "symbol": symbol, "error": str(e)},
                    )
            return

        # Broadcast each result to its subscribers.
        # strict: scan_symbols_async returns one result per symbol, in order. If
        # that ever broke, a silent zip would pair symbols with the wrong results.
        for symbol, result in zip(to_scan, results, strict=True):
            if not self._manager.has_subscribers(symbol):
                continue

            try:
                payload = {
                    "type": "scan_result",
                    "symbol": symbol,
                    "data": _result_to_lite_dict(result).model_dump(mode="json"),
                }
            except Exception as e:
                logger.error(f"Failed to serialize scan result for {symbol}: {e}")
                continue

            await self._manager.broadcast(symbol, payload)
            self._last_broadcast_at = symbol


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
    if _dispatcher is not None:
        task = _dispatcher._debounce_task
        if task is not None and not task.done():
            task.cancel()
        _dispatcher._dirty_symbols.clear()
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
                    await websocket.send_json({"type": "error", "message": f"Invalid frame: {e}"})
                except Exception:
                    break
                continue

            action = msg.get("action") if isinstance(msg, dict) else None
            symbol = msg.get("symbol") if isinstance(msg, dict) else None

            if action in ("subscribe", "unsubscribe"):
                normalized = symbol.strip().upper() if isinstance(symbol, str) else ""
                if not _SYMBOL_RE.fullmatch(normalized):
                    await websocket.send_json(
                        {"type": "error", "message": f"Invalid symbol: {symbol!r}"}
                    )
                    continue

            if action == "subscribe":
                if broadcast_manager.subscription_count(
                    websocket
                ) >= _MAX_SUBSCRIPTIONS_PER_SOCKET and not broadcast_manager.has_subscription(
                    websocket, normalized
                ):
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": (
                                f"Subscription limit reached ({_MAX_SUBSCRIPTIONS_PER_SOCKET})"
                            ),
                        }
                    )
                    continue
                await broadcast_manager.subscribe(websocket, normalized)
                await websocket.send_json({"type": "subscribed", "symbol": normalized})
            elif action == "unsubscribe":
                await broadcast_manager.unsubscribe(websocket, normalized)
                await websocket.send_json({"type": "unsubscribed", "symbol": normalized})
            elif action == "ping":
                await websocket.send_json({"type": "pong"})
            elif action in (None, ""):
                await websocket.send_json({"type": "error", "message": "Missing 'action'"})
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
