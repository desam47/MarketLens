"""
Tests for backend.market_data.streaming.webull_stream.WebullStreamClient.

The Webull SDK's DataStreamingClient is mocked — no MQTT, no network.
"""
import logging
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from backend.market_data.streaming import webull_stream
from backend.market_data.streaming.webull_stream import (
    WebullStreamClient,
    get_webull_stream_client,
)


def _basic(symbol="AAPL", ts_ms=1_700_000_000_000):
    b = MagicMock()
    b.symbol = symbol
    b.timestamp = ts_ms
    return b


def _snapshot(symbol="AAPL", price=191.5, volume=1000, high=192.0, low=190.0, open_=191.0):
    r = MagicMock()
    r.get_basic.return_value = _basic(symbol)
    r.get_price.return_value = price
    r.get_volume.return_value = volume
    r.get_high.return_value = high
    r.get_low.return_value = low
    r.get_open.return_value = open_
    return r


def _tick(symbol="AAPL", price=191.5, volume=300, side=1):
    r = MagicMock()
    r.get_basic.return_value = _basic(symbol)
    r.get_time.return_value = 1_700_000_000_000
    r.get_price.return_value = price
    r.get_volume.return_value = volume
    r.get_side.return_value = side
    return r


class TestMessageMapping(unittest.TestCase):
    def setUp(self):
        self.snaps, self.trades = [], []
        self.client = WebullStreamClient(
            "k", "s",
            on_snapshot=lambda *a: self.snaps.append(a),
            on_trade=lambda *a: self.trades.append(a),
        )

    def test_snapshot_maps_and_marks_live(self):
        self.client._on_message(None, "snapshot", _snapshot(price=191.5, volume=1000))
        self.assertEqual(len(self.snaps), 1)
        sym, price, vol, ts, high, low, open_ = self.snaps[0]
        self.assertEqual(sym, "AAPL")
        self.assertEqual(price, 191.5)
        self.assertEqual(vol, 1000.0)
        self.assertEqual((high, low, open_), (192.0, 190.0, 191.0))
        self.assertIn("AAPL", self.client._last_msg_at)

    def test_tick_maps_side_buy(self):
        self.client._on_message(None, "tick", _tick(side=1))
        self.assertEqual(self.trades[0][0], "AAPL")
        self.assertEqual(self.trades[0][1], 191.5)
        self.assertEqual(self.trades[0][2], 300.0)
        self.assertEqual(self.trades[0][4], "buy")

    def test_tick_maps_side_sell_and_unknown(self):
        self.client._on_message(None, "tick", _tick(side=2))
        self.assertEqual(self.trades[-1][4], "sell")
        self.client._on_message(None, "tick", _tick(side=3))
        self.assertIsNone(self.trades[-1][4])

    def test_snapshot_without_price_is_dropped(self):
        r = _snapshot()
        r.get_price.return_value = None
        self.client._on_message(None, "snapshot", r)
        self.assertEqual(self.snaps, [])

    def test_bad_message_does_not_raise(self):
        boom = MagicMock()
        boom.get_basic.side_effect = RuntimeError("corrupt")
        self.client._on_message(None, "tick", boom)  # must not raise
        self.assertEqual(self.trades, [])


class TestHealthAndSubscription(unittest.TestCase):
    def setUp(self):
        self.client = WebullStreamClient("k", "s")

    def test_is_live_requires_recent_message(self):
        self.assertFalse(self.client.is_live("AAPL"))
        self.client._connected = True
        self.assertFalse(self.client.is_live("AAPL"))  # no message yet
        self.client._last_msg_at["AAPL"] = time.monotonic()
        self.assertTrue(self.client.is_live("AAPL"))
        self.client._last_msg_at["AAPL"] = time.monotonic() - 9999
        self.assertFalse(self.client.is_live("AAPL"))

    def test_subscribe_accumulates_and_uppercases(self):
        self.client.subscribe(["aapl", "MSFT"])
        self.client.subscribe(["nvda"])
        self.assertEqual(self.client._subscribed, {"AAPL", "MSFT", "NVDA"})

    def test_on_connect_resubscribes_full_set(self):
        mock_sdk = MagicMock()
        self.client._client = mock_sdk
        self.client.subscribe(["AAPL", "MSFT"])
        self.client._on_connect(mock_sdk, MagicMock(), "sess-1")
        self.assertTrue(self.client._connected)
        args = mock_sdk.subscribe.call_args[0]
        self.assertEqual(sorted(args[0]), ["AAPL", "MSFT"])
        self.assertIn("SNAPSHOT", args[2])
        self.assertIn("TICK", args[2])

    def test_unsubscribe_drops_symbols(self):
        self.client._client = MagicMock()
        self.client._connected = True
        self.client.subscribe(["AAPL", "MSFT"])
        self.client.unsubscribe(["aapl"])
        self.assertEqual(self.client._subscribed, {"MSFT"})


class TestSupervisor(unittest.TestCase):
    def test_start_spawns_supervisor_and_stop_joins_it(self):
        c = WebullStreamClient("k", "s")
        # _build_client raises every time -> supervisor must keep looping,
        # not die. Make the retry backoff instant.
        with patch.object(c, "_build_client", side_effect=RuntimeError("429")):
            c.start()
            self.assertIsNotNone(c._supervisor)
            self.assertTrue(c._supervisor.is_alive())
            time.sleep(0.2)  # let it fail-and-retry a couple times
            self.assertTrue(c._supervisor.is_alive())  # still alive, still retrying
            c.stop()
        self.assertFalse(c._supervisor.is_alive())
        self.assertFalse(c._connected)

    def test_supervisor_marks_connected_when_sdk_connects(self):
        c = WebullStreamClient("k", "s")
        fake_sdk = MagicMock()
        fake_sdk._thread = MagicMock(is_alive=MagicMock(return_value=True))

        def _build():
            # simulate the SDK firing on_connect shortly after start
            c._on_connect(fake_sdk, MagicMock(), "sess")
            return fake_sdk

        with patch.object(c, "_build_client", side_effect=_build):
            c.start()
            time.sleep(0.3)
            self.assertTrue(c._connected)
            c.stop()


class TestTeardown(unittest.TestCase):
    """The SDK's loop_stop() blocks ~10 s (it sleeps before noticing the stop) and logs an ERROR."""

    def _client_with(self, sdk):
        c = WebullStreamClient("k", "s")
        c._client = sdk
        c._connected = True
        return c

    def test_does_not_wait_for_a_slow_loop_stop(self):
        release = threading.Event()
        self.addCleanup(release.set)
        sdk = MagicMock()
        sdk.loop_stop.side_effect = lambda: release.wait(10)   # the SDK's 10 s sleep
        c = self._client_with(sdk)
        with patch.object(webull_stream, "_TEARDOWN_WAIT_S", 0.2):
            t0 = time.monotonic()
            c._teardown_client()
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertIsNone(c._client)
        self.assertFalse(c._connected)

    def test_disconnect_is_sent_before_the_blocking_loop_stop(self):
        release = threading.Event()
        self.addCleanup(release.set)
        order = []
        sdk = MagicMock()
        sdk.unsubscribe.side_effect = lambda **_: order.append("unsubscribe")
        sdk.disconnect.side_effect = lambda: order.append("disconnect")
        sdk.loop_stop.side_effect = lambda: (order.append("loop_stop"), release.wait(10))
        with patch.object(webull_stream, "_TEARDOWN_WAIT_S", 0.2):
            self._client_with(sdk)._teardown_client()
        self.assertEqual(order, ["unsubscribe", "disconnect", "loop_stop"])

    def test_a_failing_call_does_not_skip_the_rest(self):
        sdk = MagicMock()
        sdk.unsubscribe.side_effect = RuntimeError("not connected")
        sdk.disconnect.side_effect = RuntimeError("socket closed")
        self._client_with(sdk)._teardown_client()
        sdk.loop_stop.assert_called_once()

    def test_no_client_is_a_no_op(self):
        c = WebullStreamClient("k", "s")
        c._teardown_client()
        self.assertEqual(webull_stream._teardowns_in_flight, 0)

    def test_in_flight_count_returns_to_zero(self):
        self._client_with(MagicMock())._teardown_client()
        deadline = time.monotonic() + 2
        while webull_stream._teardowns_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(webull_stream._teardowns_in_flight, 0)


class TestExpectedStopLogFilter(unittest.TestCase):
    MSG = "exception:loop ack code: 1, msg: Protocol not supported"

    def _record(self, msg, level=logging.ERROR):
        return logging.LogRecord("webull.data", level, __file__, 1, msg, None, None)

    def test_demoted_only_while_a_teardown_is_running(self):
        f = webull_stream._ExpectedStopFilter()
        with patch.object(webull_stream, "_teardowns_in_flight", 1):
            r = self._record(self.MSG)
            self.assertTrue(f.filter(r))
            self.assertEqual((r.levelno, r.levelname), (logging.INFO, "INFO"))
        with patch.object(webull_stream, "_teardowns_in_flight", 0):
            r = self._record(self.MSG)
            f.filter(r)
            self.assertEqual(r.levelno, logging.ERROR)   # a real loop failure stays an error

    def test_other_errors_during_a_teardown_stay_errors(self):
        f = webull_stream._ExpectedStopFilter()
        with patch.object(webull_stream, "_teardowns_in_flight", 1):
            r = self._record("connect exception:timed out")
            self.assertTrue(f.filter(r))
            self.assertEqual(r.levelno, logging.ERROR)

    def test_is_installed_on_the_sdk_logger(self):
        self.assertTrue(any(isinstance(f, webull_stream._ExpectedStopFilter)
                            for f in logging.getLogger("webull.data").filters))


class TestFactory(unittest.TestCase):
    @patch("backend.market_data.streaming.webull_stream._client", None)
    def test_returns_none_when_disabled(self):
        from backend.config.settings import settings

        with patch.object(settings.webull, "streaming_enabled", False):
            self.assertIsNone(get_webull_stream_client())

    @patch("backend.market_data.streaming.webull_stream._client", None)
    def test_returns_instance_when_enabled_and_credentialed(self):
        from backend.config.settings import settings

        with patch.object(settings.webull, "streaming_enabled", True), \
             patch.object(settings.webull, "app_key", "k"), \
             patch.object(settings.webull, "app_secret", "s"):
            c = get_webull_stream_client()
        self.assertIsInstance(c, WebullStreamClient)


if __name__ == "__main__":
    unittest.main()
