"""
Tests for the realtime bar WebSocket channel (``backend.api.realtime.ws_router``).

Covers the broadcast manager (bounded concurrent sends, subscription
bookkeeping), the provider-stream lifecycle (idempotent start, release when
the last subscriber leaves — including on disconnect) and the endpoint's
input validation. This module previously had no tests at all.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.realtime import ws_router
from backend.api.realtime.ws_router import RealtimeBroadcastManager


class _FakeSocket:
    """Minimal stand-in for a WebSocket: records sends, optionally stalls or fails."""

    def __init__(self, *, stall: bool = False, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self._stall = stall
        self._fail = fail

    async def send_json(self, payload: dict) -> None:
        if self._fail:
            raise RuntimeError("socket closed")
        if self._stall:
            await asyncio.sleep(3600)  # a client that never drains its buffer
        self.sent.append(payload)


class TestBroadcastManager(unittest.IsolatedAsyncioTestCase):
    async def test_slow_client_does_not_block_or_delay_others_and_is_dropped(self):
        mgr = RealtimeBroadcastManager()
        stalled, healthy = _FakeSocket(stall=True), _FakeSocket()
        await mgr.subscribe(stalled, "AAPL", "1m")
        await mgr.subscribe(healthy, "AAPL", "1m")

        with patch.object(ws_router, "_SEND_TIMEOUT_SECONDS", 0.05):
            # Would hang for an hour if sends were sequential and unbounded.
            await asyncio.wait_for(mgr.broadcast("AAPL:1m", {"type": "bar_update"}), timeout=2)

        self.assertEqual(healthy.sent, [{"type": "bar_update"}])
        self.assertTrue(mgr.has_subscribers("AAPL", "1m"))
        self.assertEqual(mgr.get_stats()["active_connections"], 1)  # stalled one dropped
        self.assertEqual(mgr.get_stats()["messages_sent_total"], 1)

    async def test_failing_client_is_dropped_others_still_receive(self):
        mgr = RealtimeBroadcastManager()
        bad, good = _FakeSocket(fail=True), _FakeSocket()
        await mgr.subscribe(bad, "MSFT", "5m")
        await mgr.subscribe(good, "MSFT", "5m")

        await mgr.broadcast("MSFT:5m", {"n": 1})

        self.assertEqual(good.sent, [{"n": 1}])
        self.assertEqual(mgr.get_stats()["active_connections"], 1)

    async def test_duplicate_subscribe_is_reported_and_not_double_counted(self):
        mgr = RealtimeBroadcastManager()
        ws = _FakeSocket()
        self.assertTrue(await mgr.subscribe(ws, "aapl", "1m"))
        self.assertFalse(await mgr.subscribe(ws, "AAPL", "1M"))
        self.assertEqual(mgr.subscription_count(ws), 1)
        self.assertEqual(mgr.get_stats()["total_subscriptions"], 1)

    async def test_remove_socket_reports_only_keys_left_without_subscribers(self):
        mgr = RealtimeBroadcastManager()
        a, b = _FakeSocket(), _FakeSocket()
        await mgr.subscribe(a, "AAPL", "1m")  # shared with b -> not orphaned
        await mgr.subscribe(b, "AAPL", "1m")
        await mgr.subscribe(a, "TSLA", "1m")  # only a -> orphaned

        orphaned = await mgr.remove_socket(a)

        self.assertEqual(orphaned, ["TSLA:1m"])
        self.assertTrue(mgr.has_subscribers("AAPL", "1m"))
        self.assertFalse(mgr.has_subscribers("TSLA", "1m"))
        self.assertEqual(mgr.subscription_count(a), 0)
        # Second removal is a harmless no-op.
        self.assertEqual(await mgr.remove_socket(a), [])

    async def test_unsubscribe_updates_reverse_index(self):
        mgr = RealtimeBroadcastManager()
        ws = _FakeSocket()
        await mgr.subscribe(ws, "AAPL", "1m")
        await mgr.unsubscribe(ws, "AAPL", "1m")
        self.assertEqual(mgr.subscription_count(ws), 0)
        self.assertEqual(await mgr.remove_socket(ws), [])

    def test_split_key_both_shapes(self):
        self.assertEqual(RealtimeBroadcastManager._split_key("AAPL:1m"), ("AAPL", "1m"))
        # No colon: used to return ("X", ("", "")) because of a precedence bug.
        self.assertEqual(RealtimeBroadcastManager._split_key("AAPL"), ("AAPL", ""))


class TestProviderStreamLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ws_router._provider_stream_registry.clear()
        self.provider = MagicMock()
        manager = MagicMock()
        manager.providers = {"alpaca": self.provider}
        patcher = patch("backend.market_data.services.manager.market_data_manager", manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(ws_router._provider_stream_registry.clear)

    async def test_start_is_idempotent_across_repeats_and_sockets(self):
        for _ in range(3):
            await ws_router._maybe_start_provider_stream("alpaca", "aapl", "1m")
        self.provider.subscribe.assert_called_once_with("AAPL", "1m")
        self.assertEqual(ws_router.get_provider_stream("AAPL", "1m"), "alpaca")

    async def test_unsupported_provider_is_ignored(self):
        await ws_router._maybe_start_provider_stream("bogus", "AAPL", "1m")
        self.provider.subscribe.assert_not_called()
        self.assertIsNone(ws_router.get_provider_stream("AAPL", "1m"))

    async def test_release_stops_stream_once_and_clears_registry(self):
        await ws_router._maybe_start_provider_stream("alpaca", "AAPL", "1m")
        await ws_router._release_provider_streams(["AAPL:1m", "AAPL:1m"])
        self.provider.unsubscribe.assert_called_once_with("AAPL", "1m")
        self.assertIsNone(ws_router.get_provider_stream("AAPL", "1m"))

    async def test_release_ignores_keys_without_a_provider_stream(self):
        await ws_router._release_provider_streams(["NVDA:1m"])
        self.provider.unsubscribe.assert_not_called()


class TestRealtimeEndpoint(unittest.TestCase):
    """End-to-end through TestClient on a minimal app (no full lifespan)."""

    def setUp(self):
        app = FastAPI()
        app.include_router(ws_router.router)
        self.client = TestClient(app)
        ws_router.broadcast_manager.__init__()  # fresh registry per test
        ws_router._provider_stream_registry.clear()
        self.addCleanup(ws_router.reset)
        self.addCleanup(ws_router._provider_stream_registry.clear)

    def _roundtrip(self, ws, frame):
        ws.send_json(frame)
        return ws.receive_json()

    def test_subscribe_normalises_and_acks(self):
        with self.client.websocket_connect("/api/realtime/ws") as ws:
            reply = self._roundtrip(
                ws, {"action": "subscribe", "symbol": " aapl ", "timeframe": "5M"}
            )
        self.assertEqual(
            reply, {"type": "subscribed", "symbol": "AAPL", "timeframe": "5m", "provider": "local"}
        )

    def test_timeframe_defaults_to_1m(self):
        with self.client.websocket_connect("/api/realtime/ws") as ws:
            reply = self._roundtrip(ws, {"action": "subscribe", "symbol": "SPY"})
        self.assertEqual(reply["timeframe"], "1m")

    def test_rejects_invalid_symbol_and_timeframe_without_registering(self):
        with self.client.websocket_connect("/api/realtime/ws") as ws:
            for symbol in ("A" * 21, "AA PL", "'; DROP TABLE bars;--", ""):
                reply = self._roundtrip(ws, {"action": "subscribe", "symbol": symbol})
                self.assertEqual(reply["type"], "error", symbol)
            reply = self._roundtrip(
                ws, {"action": "subscribe", "symbol": "AAPL", "timeframe": "yearly"}
            )
            self.assertEqual(reply["type"], "error")
            self.assertEqual(ws_router.broadcast_manager.get_stats()["subscribed_keys"], 0)

    def test_index_style_symbols_are_accepted(self):
        with self.client.websocket_connect("/api/realtime/ws") as ws:
            for symbol in ("^VIX", "BRK.B", "BRK-B", "EURUSD=X"):
                reply = self._roundtrip(ws, {"action": "subscribe", "symbol": symbol})
                self.assertEqual(reply["type"], "subscribed", symbol)

    def test_non_object_frame_is_an_error_not_a_crash(self):
        with self.client.websocket_connect("/api/realtime/ws") as ws:
            self.assertEqual(self._roundtrip(ws, ["subscribe"])["type"], "error")
            # Connection still usable afterwards.
            self.assertEqual(self._roundtrip(ws, {"action": "ping"}), {"type": "pong"})

    def test_subscription_cap_per_socket(self):
        with (
            patch.object(ws_router, "_MAX_SUBSCRIPTIONS_PER_SOCKET", 2),
            self.client.websocket_connect("/api/realtime/ws") as ws,
        ):
            self.assertEqual(
                self._roundtrip(ws, {"action": "subscribe", "symbol": "A"})["type"], "subscribed"
            )
            self.assertEqual(
                self._roundtrip(ws, {"action": "subscribe", "symbol": "B"})["type"], "subscribed"
            )
            self.assertEqual(
                self._roundtrip(ws, {"action": "subscribe", "symbol": "C"})["type"], "error"
            )
            # Re-subscribing to an existing key at the cap is not a new subscription.
            self.assertEqual(
                self._roundtrip(ws, {"action": "subscribe", "symbol": "A"})["type"], "subscribed"
            )

    def test_unknown_provider_is_not_registered_and_acks_local(self):
        with self.client.websocket_connect("/api/realtime/ws") as ws:
            reply = self._roundtrip(
                ws, {"action": "subscribe", "symbol": "AAPL", "provider": "evil"}
            )
        self.assertEqual(reply["provider"], "local")
        self.assertEqual(ws_router._provider_stream_registry, {})

    def test_disconnect_releases_provider_stream(self):
        provider = MagicMock()
        manager = MagicMock()
        manager.providers = {"alpaca": provider}
        with patch("backend.market_data.services.manager.market_data_manager", manager):
            with self.client.websocket_connect("/api/realtime/ws") as ws:
                reply = self._roundtrip(
                    ws, {"action": "subscribe", "symbol": "AAPL", "provider": "alpaca"}
                )
                self.assertEqual(reply["provider"], "alpaca")
                provider.subscribe.assert_called_once_with("AAPL", "1m")
            # Client dropped without unsubscribing: stream must be released.
            provider.unsubscribe.assert_called_once_with("AAPL", "1m")
        self.assertEqual(ws_router._provider_stream_registry, {})

    def test_two_sockets_share_one_stream_until_the_last_leaves(self):
        provider = MagicMock()
        manager = MagicMock()
        manager.providers = {"alpaca": provider}
        sub = {"action": "subscribe", "symbol": "AAPL", "provider": "alpaca"}
        with patch("backend.market_data.services.manager.market_data_manager", manager):
            with self.client.websocket_connect("/api/realtime/ws") as ws1:
                self._roundtrip(ws1, sub)
                with self.client.websocket_connect("/api/realtime/ws") as ws2:
                    self._roundtrip(ws2, sub)
                    provider.subscribe.assert_called_once()  # not once per socket
                provider.unsubscribe.assert_not_called()  # ws1 still watching
            provider.unsubscribe.assert_called_once_with("AAPL", "1m")


if __name__ == "__main__":
    unittest.main()
