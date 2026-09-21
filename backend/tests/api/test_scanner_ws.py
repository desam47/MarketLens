"""
Tests for the Scanner WebSocket push channel.

The WebSocket endpoint is exercised end-to-end through ``TestClient``;
the broadcast manager and dispatcher are reset between tests so
subscriptions from one test don't leak into another.
"""

import asyncio
import os
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.scanner import ws_router
from backend.api.scanner.ws_router import (
    _MAX_SUBSCRIPTIONS_PER_SOCKET,
    ScannerBroadcastManager,
    reset_dispatcher,
)
from backend.models.market_data import DataStatus, Quote
from backend.scanner.scanner import ScanResult


def _make_quote(symbol: str = "AAPL", price: float = 150.0) -> Quote:
    return Quote(
        symbol=symbol,
        price=price,
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        provider="yahoo_finance",
        data_status=DataStatus.DELAYED,
        volume=1_000_000,
        bid=price - 0.5,
        ask=price + 0.5,
    )


def _make_scan_result(symbol: str = "AAPL", signals: list[str] | None = None) -> ScanResult:
    result = ScanResult(symbol, datetime(2025, 1, 1, 12, 0, 0))
    result.quote = _make_quote(symbol)
    result.indicator_values = {"price": 150.0, "rsi": 55.0}
    result.scores = {"momentum": 60.0}
    result.signals = list(signals or ["RSI_OVERSOLD"])
    return result


@contextmanager
def _reset_broadcast_state():
    """Snapshot + restore the process-wide broadcast state.

    Lets every test start from a clean manager / dispatcher without
    interfering with the other tests in this file.
    """
    saved_manager = ws_router.broadcast_manager
    saved_dispatcher = ws_router._dispatcher
    ws_router.broadcast_manager = ScannerBroadcastManager()
    reset_dispatcher()
    try:
        yield ws_router.broadcast_manager
    finally:
        # Drain any lingering futures on the in-flight dispatcher.
        ws_router.broadcast_manager = saved_manager
        ws_router._dispatcher = saved_dispatcher


class TestScannerWebSocket(unittest.TestCase):
    """The protocol surface: subscribe / unsubscribe / ping / errors."""

    def setUp(self):
        self.client = TestClient(app)
        # Make sure no cross-test subscription state leaks in.
        # Enter the context manager here so tearDown's __exit__ works correctly.
        self._state_context = _reset_broadcast_state()
        self._state = self._state_context.__enter__()
        # Don't let the install() inside the endpoint touch the real
        # engine_registry — it would register a callback that fires for
        # every test's first connect. Patch the registry so registration
        # is a no-op.
        self._registry_patcher = patch("backend.api.scanner.ws_router.engine_registry")
        self.mock_registry = self._registry_patcher.start()

    def tearDown(self):
        self._registry_patcher.stop()
        self._state_context.__exit__(None, None, None)

    # --- handshake / ping ---------------------------------------------------

    def test_connect_accepted(self):
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            # If the endpoint didn't accept(), entering the context
            # manager would have raised. Round-trip a ping to confirm
            # the loop is alive.
            ws.send_json({"action": "ping"})
            response = ws.receive_json()
            self.assertEqual(response, {"type": "pong"})

    def test_ping_replies_with_pong(self):
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_json({"action": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

    # --- subscribe / unsubscribe --------------------------------------------

    def test_subscribe_acks(self):
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_json({"action": "subscribe", "symbol": "AAPL"})
            self.assertEqual(
                ws.receive_json(),
                {"type": "subscribed", "symbol": "AAPL"},
            )
            self.assertIn("AAPL", self._state.get_subscribed_symbols())

    def test_unsubscribe_acks_and_removes(self):
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_json({"action": "subscribe", "symbol": "AAPL"})
            ws.receive_json()  # ack

            ws.send_json({"action": "unsubscribe", "symbol": "AAPL"})
            self.assertEqual(
                ws.receive_json(),
                {"type": "unsubscribed", "symbol": "AAPL"},
            )
            self.assertNotIn("AAPL", self._state.get_subscribed_symbols())

    def test_two_sockets_to_same_symbol(self):
        """Two sockets to the same symbol are tracked independently."""
        # Open both sockets, subscribe them to AAPL.
        with (
            self.client.websocket_connect("/api/scanner-stream/ws") as ws_a,
            self.client.websocket_connect("/api/scanner-stream/ws") as ws_b,
        ):
            ws_a.send_json({"action": "subscribe", "symbol": "AAPL"})
            ws_b.send_json({"action": "subscribe", "symbol": "AAPL"})
            ws_a.receive_json()
            ws_b.receive_json()

            # Both subscribed → AAPL is in the registry with 2 sockets.
            self.assertEqual(len(self._state._subs.get("AAPL", set())), 2)

        # After both contexts exit, the server-side cleanup should have
        # removed both sockets.
        self.assertNotIn("AAPL", self._state.get_subscribed_symbols())

    # --- error handling -----------------------------------------------------

    def test_unknown_action_sends_error(self):
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_json({"action": "frobnicate"})
            response = ws.receive_json()
            self.assertEqual(response["type"], "error")
            self.assertIn("frobnicate", response["message"])

    def test_missing_action_sends_error(self):
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_json({"not_action": "subscribe"})
            response = ws.receive_json()
            self.assertEqual(response["type"], "error")
            self.assertIn("action", response["message"].lower())

    def test_subscribe_without_symbol_sends_error(self):
        """Sending a subscribe without a symbol is treated as an error,
        not a no-op — the client should know it didn't actually
        subscribe."""
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_json({"action": "subscribe"})
            response = ws.receive_json()
            self.assertEqual(response["type"], "error")

    def test_invalid_symbol_is_rejected(self):
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_json({"action": "subscribe", "symbol": "not a ticker!"})
            response = ws.receive_json()
            self.assertEqual(response["type"], "error")
            self.assertIn("Invalid symbol", response["message"])
            self.assertEqual(self._state.get_subscribed_symbols(), set())

    def test_index_symbol_is_allowed(self):
        """Yahoo-style index tickers remain valid scanner subscriptions."""
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_json({"action": "subscribe", "symbol": "^vix"})
            self.assertEqual(
                ws.receive_json(),
                {"type": "subscribed", "symbol": "^VIX"},
            )

    def test_subscription_limit_rejects_new_symbols(self):
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            for index in range(_MAX_SUBSCRIPTIONS_PER_SOCKET):
                symbol = f"S{index:02d}"
                ws.send_json({"action": "subscribe", "symbol": symbol})
                self.assertEqual(ws.receive_json()["type"], "subscribed")

            ws.send_json({"action": "subscribe", "symbol": "OVERFLOW"})
            response = ws.receive_json()
            self.assertEqual(response["type"], "error")
            self.assertIn("Subscription limit", response["message"])
            self.assertEqual(
                len(self._state.get_subscribed_symbols()), _MAX_SUBSCRIPTIONS_PER_SOCKET
            )

    def test_invalid_json_sends_error(self):
        """A non-JSON frame yields an error and the connection stays open."""
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_text("not json at all")
            response = ws.receive_json()
            self.assertEqual(response["type"], "error")
            # The connection is still usable after the error.
            ws.send_json({"action": "ping"})
            self.assertEqual(ws.receive_json(), {"type": "pong"})

    # --- disconnect cleanup -------------------------------------------------

    def test_disconnect_removes_socket_from_subscriptions(self):
        with self.client.websocket_connect("/api/scanner-stream/ws") as ws:
            ws.send_json({"action": "subscribe", "symbol": "AAPL"})
            ws.receive_json()  # ack
            self.assertIn("AAPL", self._state.get_subscribed_symbols())

        # Context manager exit closes the WS. The server-side cleanup
        # should drop the subscription.
        # Give the event loop a chance to run the finally block.
        self.assertNotIn("AAPL", self._state.get_subscribed_symbols())


class TestScannerBroadcastManager(unittest.IsolatedAsyncioTestCase):
    """Direct unit tests for the broadcast manager — no FastAPI."""

    async def test_broadcast_sends_to_all_subscribers(self):
        manager = ScannerBroadcastManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.subscribe(ws1, "AAPL")
        await manager.subscribe(ws2, "AAPL")

        await manager.broadcast("AAPL", {"type": "scan_result", "symbol": "AAPL"})

        ws1.send_json.assert_awaited_once()
        ws2.send_json.assert_awaited_once()

    async def test_broadcast_lowercases_symbol(self):
        manager = ScannerBroadcastManager()
        ws = AsyncMock()
        await manager.subscribe(ws, "aapl")

        await manager.broadcast("AAPL", {"type": "scan_result", "symbol": "AAPL"})

        ws.send_json.assert_awaited_once()

    async def test_broadcast_skips_unknown_symbol(self):
        manager = ScannerBroadcastManager()
        ws = AsyncMock()
        await manager.subscribe(ws, "AAPL")

        await manager.broadcast("MSFT", {"type": "scan_result", "symbol": "MSFT"})

        ws.send_json.assert_not_called()

    async def test_failed_send_cleans_up_socket(self):
        """A subscriber whose send raises is dropped, not retried."""
        manager = ScannerBroadcastManager()
        ws_good = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_json.side_effect = RuntimeError("client gone")
        await manager.subscribe(ws_good, "AAPL")
        await manager.subscribe(ws_dead, "AAPL")

        await manager.broadcast("AAPL", {"type": "scan_result", "symbol": "AAPL"})

        # The dead socket should be removed; the good one survives.
        self.assertEqual(len(manager._subs.get("AAPL", set())), 1)
        # And a subsequent broadcast only hits the surviving socket.
        ws_good.send_json.reset_mock()
        await manager.broadcast("AAPL", {"type": "scan_result", "symbol": "AAPL"})
        ws_good.send_json.assert_awaited_once()

    async def test_remove_socket_clears_all_symbols(self):
        manager = ScannerBroadcastManager()
        ws = AsyncMock()
        await manager.subscribe(ws, "AAPL")
        await manager.subscribe(ws, "MSFT")
        self.assertEqual(len(manager._subs), 2)

        await manager.remove_socket(ws)

        self.assertEqual(manager._subs, {})


class TestScannerDispatcher(unittest.IsolatedAsyncioTestCase):
    """The dispatcher fans fresh quotes out to subscribers."""

    async def test_no_subscribers_does_nothing(self):
        manager = ScannerBroadcastManager()
        # Mock the scanner so we can prove it isn't called.
        with patch("backend.api.scanner.ws_router.market_scanner") as mock_scanner:
            # Construct a dispatcher with a dummy loop — it will never
            # be used because the manager is empty.
            loop = asyncio.get_running_loop()
            dispatcher = ws_router.ScannerDispatcher(manager, loop)

            # Simulate a fresh quote callback.
            dispatcher._on_quote(
                price=150.0, volume=1_000_000, timestamp=datetime.now(), symbol="AAPL"
            )

            # Yield once so any scheduled task can run.
            await asyncio.sleep(0)
            mock_scanner.scan_symbol.assert_not_called()

    async def test_dispatcher_scans_and_broadcasts(self):
        manager = ScannerBroadcastManager()
        ws = AsyncMock()
        await manager.subscribe(ws, "AAPL")

        with patch("backend.api.scanner.ws_router.market_scanner") as mock_scanner:
            mock_scanner.scan_symbols_async = AsyncMock(return_value=[_make_scan_result("AAPL")])
            loop = asyncio.get_running_loop()
            dispatcher = ws_router.ScannerDispatcher(manager, loop)
            # Drive the full flow directly.
            await dispatcher._scan_and_broadcast_all()

        # The batch scan ran for AAPL...
        mock_scanner.scan_symbols_async.assert_awaited_once_with(["AAPL"])
        # ...and the result was pushed to the subscriber.
        ws.send_json.assert_awaited_once()
        sent = ws.send_json.await_args.args[0]
        self.assertEqual(sent["type"], "scan_result")
        self.assertEqual(sent["symbol"], "AAPL")
        self.assertIn("data", sent)

    async def test_dispatcher_sends_scan_error(self):
        manager = ScannerBroadcastManager()
        ws = AsyncMock()
        await manager.subscribe(ws, "AAPL")

        with patch("backend.api.scanner.ws_router.market_scanner") as mock_scanner:
            mock_scanner.scan_symbols_async = AsyncMock(side_effect=RuntimeError("upstream down"))
            loop = asyncio.get_running_loop()
            dispatcher = ws_router.ScannerDispatcher(manager, loop)
            await dispatcher._scan_and_broadcast_all()

        ws.send_json.assert_awaited_once()
        sent = ws.send_json.await_args.args[0]
        self.assertEqual(sent["type"], "scan_error")
        self.assertEqual(sent["symbol"], "AAPL")
        self.assertIn("upstream down", sent["error"])

    async def test_dispatcher_cooldown_suppresses_repeat_scans(self):
        """A second call within the cooldown window must skip the scan.

        Without the cooldown gate, ``_scan_and_broadcast_all`` would
        re-scan + re-serialize + broadcast on every quote event —
        which can fire many times per second under load. With a 30s
        cooldown, the second call within the window short-circuits
        before touching the scanner or any subscriber.
        """
        manager = ScannerBroadcastManager()
        ws = AsyncMock()
        await manager.subscribe(ws, "AAPL")

        with patch("backend.api.scanner.ws_router.market_scanner") as mock_scanner:
            mock_scanner.scan_symbols_async = AsyncMock(return_value=[_make_scan_result("AAPL")])
            loop = asyncio.get_running_loop()
            dispatcher = ws_router.ScannerDispatcher(manager, loop)
            # Make the cooldown effectively infinite so the second
            # call is guaranteed to be inside the window.
            dispatcher._cooldown_seconds = 999.0

            # First call: scan runs, broadcast happens.
            await dispatcher._scan_and_broadcast_all()
            self.assertEqual(mock_scanner.scan_symbols_async.await_count, 1)
            self.assertEqual(ws.send_json.await_count, 1)

            # Second call (still inside the cooldown): scan is skipped
            # and the subscriber receives no new push.
            await dispatcher._scan_and_broadcast_all()
            self.assertEqual(mock_scanner.scan_symbols_async.await_count, 1)  # unchanged
            self.assertEqual(ws.send_json.await_count, 1)  # unchanged

    async def test_dispatcher_cooldown_expires_for_next_call(self):
        """Once the cooldown elapses, scanning resumes normally."""
        manager = ScannerBroadcastManager()
        ws = AsyncMock()
        await manager.subscribe(ws, "AAPL")

        with patch("backend.api.scanner.ws_router.market_scanner") as mock_scanner:
            mock_scanner.scan_symbols_async = AsyncMock(return_value=[_make_scan_result("AAPL")])
            loop = asyncio.get_running_loop()
            dispatcher = ws_router.ScannerDispatcher(manager, loop)
            # Zero cooldown means every call is allowed through.
            dispatcher._cooldown_seconds = 0.0

            await dispatcher._scan_and_broadcast_all()
            await dispatcher._scan_and_broadcast_all()
            await dispatcher._scan_and_broadcast_all()

            self.assertEqual(mock_scanner.scan_symbols_async.await_count, 3)
            self.assertEqual(ws.send_json.await_count, 3)


if __name__ == "__main__":
    unittest.main()
