"""
Tests for backend/market_data/providers/alpaca_provider.py — AlpacaProvider

These tests mock the official ``alpaca-py`` SDK clients
(``StockHistoricalDataClient``, ``TradingClient``, ``StockDataStream``) so
no real HTTP/WS calls are made.

Coverage:
  - Public API surface (get_quote, get_historical_bars, get_latest_bar,
    get_batch_quotes, get_market_status, is_available, get_capabilities)
  - Timeframe resolution helper
  - WebSocket client lifecycle (subscribe/unsubscribe/close)
  - Provider subscribe/unsubscribe API (for ws_router integration)
  - Provider registration
"""
import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.market_data.providers.alpaca_provider import (
    AlpacaProvider,
    AlpacaWebSocketClient,
    _resolve_feed,
    _resolve_tf,
    _ts_to_ny,
)
from backend.models.market_data import Bar, DataStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings_mock(enabled=True, api_key="test_key", secret_key="test_secret"):
    """Build a settings mock suitable for AlpacaProvider.__init__."""
    mock = MagicMock()
    mock.alpaca.enabled = enabled
    mock.alpaca.api_key = api_key
    mock.alpaca.secret_key = secret_key
    mock.alpaca.paper = True
    mock.alpaca.data_tier = "iex"
    mock.alpaca.request_timeout = 15.0
    return mock


def _sdk_quote(symbol="AAPL", bid=150.0, ask=150.5, ts=None):
    """Build a mock SDK ``Quote`` object (has .bid_price, .ask_price, etc.)."""
    return SimpleNamespace(
        symbol=symbol,
        bid_price=bid,
        ask_price=ask,
        bid_size=100,
        ask_size=200,
        timestamp=ts or datetime(2024, 1, 15, 14, 30, tzinfo=UTC),
    )


def _sdk_bar(symbol="AAPL", ts=None, o=100.0, h=101.0, low=99.0, c=100.5, v=1000, **kwargs):
    """Build a mock SDK ``Bar`` object."""
    low = kwargs.pop("l", low)  # backwards-compatible short name used by callers
    return SimpleNamespace(
        symbol=symbol,
        timestamp=ts or datetime(2024, 1, 15, 14, 30, tzinfo=UTC),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
    )


def _barset(symbol="AAPL", bars=None):
    """Build a mock SDK ``Barset`` (has .data = {symbol: [Bar, ...]})."""
    return SimpleNamespace(data={symbol: bars or []})


# ---------------------------------------------------------------------------
# Timeframe resolution tests
# ---------------------------------------------------------------------------


class TestTimeframeResolution(unittest.TestCase):
    def test_resolve_tf_supports_common_timeframes(self):
        # Just check each call returns a TimeFrame object (not None).
        for tf in ["1m", "5m", "15m", "30m", "1h", "1d", "1wk"]:
            assert _resolve_tf(tf) is not None, f"missing {tf}"

    def test_resolve_tf_approximates_unsupported(self):
        # 4h/2m fall back to nearest supported.
        assert _resolve_tf("4h").value == _resolve_tf("1h").value
        assert _resolve_tf("2m").value == _resolve_tf("1m").value

    def test_resolve_tf_raises_for_unknown(self):
        with self.assertRaises(ValueError):
            _resolve_tf("3y")

    def test_resolve_feed_defaults_to_iex(self):
        from alpaca.data.enums import DataFeed
        assert _resolve_feed("iex") == DataFeed.IEX
        assert _resolve_feed("") == DataFeed.IEX
        assert _resolve_feed("unknown") == DataFeed.IEX
        assert _resolve_feed("sip") == DataFeed.SIP
        assert _resolve_feed("DELAYED_SIP") == DataFeed.DELAYED_SIP

    def test_ts_to_ny_handles_naive(self):
        naive = datetime(2024, 1, 1, 12, 0, 0)
        out = _ts_to_ny(naive)
        assert out.tzinfo is None
        # _ts_to_ny returns naive NY time (no timezone info)


# ---------------------------------------------------------------------------
# Quote tests
# ---------------------------------------------------------------------------


class TestAlpacaProviderQuote(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.providers.alpaca_provider._settings", _settings_mock()):
            self.provider = AlpacaProvider()
        # Inject a mock data client.
        self._mock_data_client = MagicMock()
        self.provider._data_client = self._mock_data_client

    def test_get_quote_returns_quote(self):
        self._mock_data_client.get_stock_latest_quote.return_value = {
            "AAPL": _sdk_quote(bid=150.0, ask=150.5),
        }
        q = self.provider.get_quote("AAPL")
        self.assertEqual(q.symbol, "AAPL")
        self.assertEqual(q.price, 150.5)  # prefers ask (non-zero)
        self.assertEqual(q.bid, 150.0)
        self.assertEqual(q.ask, 150.5)
        self.assertEqual(q.provider, "alpaca")
        self.assertEqual(q.data_status, DataStatus.DELAYED)

    def test_get_quote_falls_back_to_bid_when_ask_zero(self):
        # Off-hours quotes often have ask=0; bid is the "last" price.
        self._mock_data_client.get_stock_latest_quote.return_value = {
            "SPY": _sdk_quote(bid=761.85, ask=0.0),
        }
        q = self.provider.get_quote("SPY")
        self.assertEqual(q.price, 761.85)

    def test_get_quote_rejects_implausible_spread(self):
        """Regression for a live bug (2026-09-16): CTNT (trading ~$0.04)
        got bid=0.04 (correct) but ask=200.0 (garbage — presumably a
        stale/foreign print from Alpaca's IEX feed), and the old
        "always prefer ask when non-zero" logic took the $200 print as
        the quote price, producing a false +322,480% change downstream.
        A >3x spread is never a real NBBO — prefer the smaller side."""
        self._mock_data_client.get_stock_latest_quote.return_value = {
            "CTNT": _sdk_quote(symbol="CTNT", bid=0.04, ask=200.0),
        }
        q = self.provider.get_quote("CTNT")
        self.assertEqual(q.price, 0.04)

    def test_get_quote_accepts_normal_wide_spread(self):
        """A genuinely wide (but plausible) spread on a thin symbol must
        still prefer ask, matching the existing default — only a >3x
        ratio is treated as bad data."""
        self._mock_data_client.get_stock_latest_quote.return_value = {
            "THIN": _sdk_quote(symbol="THIN", bid=1.00, ask=2.50),
        }
        q = self.provider.get_quote("THIN")
        self.assertEqual(q.price, 2.50)

    def test_get_quote_raises_on_missing_symbol(self):
        self._mock_data_client.get_stock_latest_quote.return_value = {}
        with self.assertRaises(ValueError):
            self.provider.get_quote("INVALID")

    def test_get_quote_raises_on_both_sides_zero(self):
        self._mock_data_client.get_stock_latest_quote.return_value = {
            "BAD": _sdk_quote(bid=0.0, ask=0.0),
        }
        with self.assertRaises(ValueError):
            self.provider.get_quote("BAD")

    def test_get_quote_raises_on_sdk_error(self):
        self._mock_data_client.get_stock_latest_quote.side_effect = RuntimeError("auth failed")
        with self.assertRaises(RuntimeError):
            self.provider.get_quote("AAPL")


# ---------------------------------------------------------------------------
# Bars tests
# ---------------------------------------------------------------------------


class TestAlpacaProviderBars(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.providers.alpaca_provider._settings", _settings_mock()):
            self.provider = AlpacaProvider()
        self._mock_data_client = MagicMock()
        self.provider._data_client = self._mock_data_client

    def test_get_historical_bars_returns_bars(self):
        b1 = _sdk_bar(o=100.0, h=101.5, l=99.5, c=101.0, v=1000)
        b2 = _sdk_bar(ts=datetime(2024, 1, 15, 14, 31, tzinfo=UTC), o=101.0, h=102.0, l=100.5, c=101.5, v=1500)
        self._mock_data_client.get_stock_bars.return_value = _barset(bars=[b1, b2])

        bars = self.provider.get_historical_bars("AAPL", timeframe="1d", range_="5d")
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].open, 100.0)
        self.assertEqual(bars[0].close, 101.0)
        self.assertEqual(bars[0].provider, "alpaca")
        self.assertEqual(bars[0].data_status, DataStatus.HISTORICAL)
        # ``end`` should be omitted from the SDK request (IEX tier compat).
        kwargs = self._mock_data_client.get_stock_bars.call_args.kwargs
        self.assertNotIn("end", kwargs)
        # ``adjustment`` should be omitted (IEX tier compat).
        self.assertNotIn("adjustment", kwargs)

    def test_get_historical_bars_returns_empty_on_no_data(self):
        self._mock_data_client.get_stock_bars.return_value = _barset(bars=[])
        bars = self.provider.get_historical_bars("INVALID", timeframe="1d", range_="1mo")
        self.assertEqual(bars, [])

    def test_get_latest_bar_returns_most_recent(self):
        b1 = _sdk_bar(ts=datetime(2024, 1, 15, 14, 30, tzinfo=UTC), c=100.5, v=1000)
        b2 = _sdk_bar(ts=datetime(2024, 1, 15, 14, 31, tzinfo=UTC), c=101.0, v=1100)
        self._mock_data_client.get_stock_bars.return_value = _barset(bars=[b1, b2])
        bar = self.provider.get_latest_bar("AAPL", "1m")
        self.assertEqual(bar.close, 101.0)
        self.assertEqual(bar.symbol, "AAPL")

    def test_get_latest_bar_raises_on_empty(self):
        self._mock_data_client.get_stock_bars.return_value = _barset(bars=[])
        with self.assertRaises(ValueError):
            self.provider.get_latest_bar("INVALID", "1m")

    def test_get_bar_uses_uncapped_window_for_historical_ts(self):
        """Historical timestamps (outside recent zone) must NOT have end capped."""
        from datetime import timedelta
        yesterday = datetime.now(UTC) - timedelta(days=1)
        self._mock_data_client.get_stock_bars.return_value = _barset(bars=[
            _sdk_bar(ts=yesterday, c=150.0, v=1000)
        ])

        self.provider.get_bar("AAPL", "1d", yesterday)

        # The SDK request must NOT have an end cap.
        req_obj = self._mock_data_client.get_stock_bars.call_args.args[0]
        end = req_obj.end
        # end should be the natural window end (~yesterday + 1 day), not "now - 15min"
        self.assertLess(end, datetime.now(UTC).replace(tzinfo=None))


# ---------------------------------------------------------------------------
# Batch quotes + market status + capabilities
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Batch quotes + market status + capabilities
# ---------------------------------------------------------------------------


class TestAlpacaProviderBatchAndStatus(unittest.TestCase):
    def setUp(self):
        with patch("backend.market_data.providers.alpaca_provider._settings", _settings_mock()):
            self.provider = AlpacaProvider()
        self._mock_data_client = MagicMock()
        self.provider._data_client = self._mock_data_client

    def test_get_batch_quotes_handles_partial_failures(self):
        # SDK returns one valid quote, no entry for the second symbol.
        self._mock_data_client.get_stock_latest_quote.return_value = {
            "AAPL": _sdk_quote(bid=149.5, ask=150.0),
            # "INVALID" missing — provider should mark it ERROR.
        }
        result = self.provider.get_batch_quotes(["AAPL", "INVALID"])
        self.assertEqual(len(result), 2)
        self.assertEqual(result["AAPL"].price, 150.0)
        self.assertEqual(result["INVALID"].data_status, DataStatus.ERROR)
        self.assertEqual(result["INVALID"].price, 0.0)

    def test_get_batch_quotes_rejects_implausible_spread(self):
        """Same bad-spread guard as get_quote — see
        test_get_quote_rejects_implausible_spread's docstring."""
        self._mock_data_client.get_stock_latest_quote.return_value = {
            "CTNT": _sdk_quote(symbol="CTNT", bid=0.04, ask=200.0),
        }
        result = self.provider.get_batch_quotes(["CTNT"])
        self.assertEqual(result["CTNT"].price, 0.04)

    def test_get_market_status_returns_open_status(self):
        clock = SimpleNamespace(
            is_open=True,
            next_open=datetime(2024, 1, 16, 14, 30, tzinfo=UTC),
            next_close=datetime(2024, 1, 15, 21, 0, tzinfo=UTC),
        )
        mock_tc = MagicMock()
        mock_tc.get_clock.return_value = clock
        self.provider._trading_client = mock_tc

        status = self.provider.get_market_status("AAPL")
        self.assertTrue(status.is_open)
        self.assertEqual(status.timezone, "America/New_York")
        self.assertEqual(status.provider, "alpaca")
        self.assertIsNotNone(status.next_open)
        self.assertIsNotNone(status.next_close)

    def test_get_market_status_when_closed(self):
        clock = SimpleNamespace(
            is_open=False,
            next_open=datetime(2024, 1, 16, 14, 30, tzinfo=UTC),
            next_close=None,
        )
        mock_tc = MagicMock()
        mock_tc.get_clock.return_value = clock
        self.provider._trading_client = mock_tc

        status = self.provider.get_market_status("AAPL")
        self.assertFalse(status.is_open)

    def test_get_capabilities(self):
        caps = self.provider.get_capabilities()
        self.assertEqual(caps.provider_name, "alpaca")
        self.assertTrue(caps.supports_historical_bars)
        self.assertTrue(caps.supports_latest_quote)
        self.assertTrue(caps.supports_market_status)

    def test_is_available_when_quote_succeeds(self):
        self._mock_data_client.get_stock_latest_quote.return_value = {
            "AAPL": _sdk_quote(bid=150.0, ask=150.5),
        }
        self.assertTrue(self.provider.is_available())

    def test_is_available_when_quote_fails(self):
        self._mock_data_client.get_stock_latest_quote.side_effect = RuntimeError("down")
        self.assertFalse(self.provider.is_available())


# ---------------------------------------------------------------------------
# WebSocket client tests
# ---------------------------------------------------------------------------


class TestAlpacaWebSocketClient(unittest.TestCase):
    def setUp(self):
        self.bar_callback = MagicMock()
        self.status_callback = MagicMock()
        # Mock the SDK stream so we never open a real WS.
        self._stream_patcher = patch(
            "backend.market_data.providers.alpaca_provider.StockDataStream"
        )
        self._mock_stream_cls = self._stream_patcher.start()
        self._mock_stream = MagicMock()
        self._mock_stream_cls.return_value = self._mock_stream
        self.addCleanup(self._stream_patcher.stop)

        self.client = AlpacaWebSocketClient(
            api_key="test_key",
            secret_key="test_secret",
            data_tier="iex",
            paper=True,
            on_bar=self.bar_callback,
            on_status_change=self.status_callback,
        )

    def test_initial_state_disconnected(self):
        self.assertFalse(self.client.is_connected)
        self.assertEqual(self.client._subs, set())

    def test_subscribe_adds_symbol(self):
        self.client.subscribe("AAPL", "1m")
        self.assertIn(("AAPL", "1m"), self.client._subs)

    def test_subscribe_is_idempotent(self):
        self.client.subscribe("AAPL", "1m")
        self.client.subscribe("AAPL", "1m")
        self.assertEqual(len(self.client._subs), 1)

    def test_subscribe_normalises_symbol(self):
        self.client.subscribe("aapl", "1m")
        self.assertIn(("AAPL", "1m"), self.client._subs)

    def test_subscribe_with_no_credentials_is_noop(self):
        client = AlpacaWebSocketClient(
            api_key="", secret_key="", data_tier="iex", paper=True,
            on_bar=MagicMock(), on_status_change=MagicMock(),
        )
        # Should not raise, should not start a thread.
        client.subscribe("AAPL", "1m")
        self.assertIsNone(client._thread)

    def test_subscribe_starts_background_thread(self):
        self.client.subscribe("AAPL", "1m")
        # Give the thread a moment to spin up.
        import time
        for _ in range(20):
            if self.client._thread is not None:
                break
            time.sleep(0.05)
        self.assertIsNotNone(self.client._thread)
        # Clean up.
        self.client.close()
        if self.client._thread is not None:
            self.client._thread.join(timeout=2.0)

    def test_unsubscribe_removes_symbol(self):
        self.client.subscribe("AAPL", "1m")
        self.client.unsubscribe("AAPL", "1m")
        self.assertNotIn(("AAPL", "1m"), self.client._subs)


class TestAlpacaWebSocketBarHandling(unittest.TestCase):
    """Verify the WebSocket client forwards SDK bar events to on_bar."""

    def setUp(self):
        self.bars_received: list[Bar] = []

        def on_bar(symbol, bar):
            self.bars_received.append(bar)

        # Mock the SDK stream so we never open a real WS.
        self._stream_patcher = patch(
            "backend.market_data.providers.alpaca_provider.StockDataStream"
        )
        self._mock_stream_cls = self._stream_patcher.start()
        self._mock_stream = MagicMock()
        self._mock_stream_cls.return_value = self._mock_stream
        self.addCleanup(self._stream_patcher.stop)

        self.client = AlpacaWebSocketClient(
            api_key="k", secret_key="s", data_tier="iex", paper=True,
            on_bar=on_bar, on_status_change=MagicMock(),
        )

    def test_handle_bar_event(self):
        sdk_bar = _sdk_bar(symbol="AAPL", o=100.0, h=101.0, l=99.0, c=100.5, v=1000)
        asyncio.run(self.client._handle_bar_event(sdk_bar))
        self.assertEqual(len(self.bars_received), 1)
        bar = self.bars_received[0]
        self.assertEqual(bar.symbol, "AAPL")
        self.assertEqual(bar.open, 100.0)
        self.assertEqual(bar.close, 100.5)
        self.assertEqual(bar.volume, 1000)
        self.assertEqual(bar.provider, "alpaca")
        self.assertEqual(bar.data_status, DataStatus.DELAYED)


# ---------------------------------------------------------------------------
# Provider registration + system health metrics
# ---------------------------------------------------------------------------


class TestAlpacaProviderRegistration(unittest.TestCase):
    def test_registered_when_enabled_with_credentials(self):
        """AlpacaProvider should appear in _PROVIDER_CLASSES when enabled + creds set."""
        with patch("backend.market_data.services.providers._settings") as mock_s:
            mock_s.alpaca.enabled = True
            mock_s.alpaca.api_key = "key"
            mock_s.alpaca.secret_key = "secret"
            from backend.market_data.services.providers import _get_alpaca_class
            cls = _get_alpaca_class()
            self.assertIs(cls, AlpacaProvider)

    def test_not_registered_when_disabled(self):
        with patch("backend.market_data.services.providers._settings") as mock_s:
            mock_s.alpaca.enabled = False
            mock_s.alpaca.api_key = "key"
            mock_s.alpaca.secret_key = "secret"
            from backend.market_data.services.providers import _get_alpaca_class
            self.assertIsNone(_get_alpaca_class())

    def test_not_registered_when_credentials_missing(self):
        with patch("backend.market_data.services.providers._settings") as mock_s:
            mock_s.alpaca.enabled = True
            mock_s.alpaca.api_key = ""
            mock_s.alpaca.secret_key = "secret"
            from backend.market_data.services.providers import _get_alpaca_class
            self.assertIsNone(_get_alpaca_class())


class TestAlpacaProviderStatus(unittest.TestCase):
    def test_provider_status_includes_ws_status(self):
        with patch("backend.market_data.providers.alpaca_provider._settings", _settings_mock()):
            provider = AlpacaProvider()
        provider._ws_status = "connected"
        status = provider.get_provider_status()
        self.assertIn("WS: connected", status.error_message)

    def test_provider_status_when_disconnected(self):
        with patch("backend.market_data.providers.alpaca_provider._settings", _settings_mock()):
            provider = AlpacaProvider()
        status = provider.get_provider_status()
        self.assertIn("WS: disconnected", status.error_message)


# ---------------------------------------------------------------------------
# Provider subscribe/unsubscribe API (for ws_router integration)
# ---------------------------------------------------------------------------


class TestAlpacaProviderSubscribeAPI(unittest.TestCase):
    def setUp(self):
        self._stream_patcher = patch(
            "backend.market_data.providers.alpaca_provider.StockDataStream"
        )
        self._mock_stream_cls = self._stream_patcher.start()
        self._mock_stream = MagicMock()
        self._mock_stream_cls.return_value = self._mock_stream
        self.addCleanup(self._stream_patcher.stop)

    def test_subscribe_with_no_credentials_is_noop(self):
        with patch("backend.market_data.providers.alpaca_provider._settings", _settings_mock(api_key="", secret_key="")):
            provider = AlpacaProvider()
            provider.subscribe("AAPL", "1m")
            self.assertIsNone(provider._ws_client)

    def test_subscribe_creates_ws_client_with_credentials(self):
        with patch("backend.market_data.providers.alpaca_provider._settings", _settings_mock()):
            provider = AlpacaProvider()
            provider.subscribe("AAPL", "1m")
            self.assertIsNotNone(provider._ws_client)
            self.assertIn(("AAPL", "1m"), provider._ws_client._subs)

    def test_unsubscribe_proxies_to_ws_client(self):
        with patch("backend.market_data.providers.alpaca_provider._settings", _settings_mock()):
            provider = AlpacaProvider()
            provider.subscribe("AAPL", "1m")
            provider.unsubscribe("AAPL", "1m")
            self.assertNotIn(("AAPL", "1m"), provider._ws_client._subs)


if __name__ == "__main__":
    unittest.main()
