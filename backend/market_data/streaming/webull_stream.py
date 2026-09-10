"""
WebullStreamClient — thin lifecycle wrapper around the Webull SDK's
``DataStreamingClient`` (MQTT over TLS).

Mirrors the ``AlpacaWebSocketClient`` shape in
``backend/market_data/providers/alpaca_provider.py``: the SDK loop runs
on a daemon thread, and decoded messages are forwarded to plain
callbacks (``on_snapshot`` / ``on_trade``) that the caller wires into the
engine registry.

Only SNAPSHOT (L1) + TICK (Time & Sales) are subscribed for now — BBO
(QUOTE depth=1) is a deferred follow-up. Re-subscription on reconnect is
automatic: the SDK re-fires the connect callback on every (re)connect and
we (re)subscribe the full symbol set there.

Streaming limits (Webull): 5 MQTT connections per App Key, unique
session_id per connection, ~3 pushes/sec/connection.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Callable

from backend.config.settings import settings

# Importing the REST provider installs its ``ApiClient.set_file_logger``
# monkey-patch (redirects the SDK's hard-coded relative log path into
# ``logs/``) and gives us the shared epoch->NY helper. See CLAUDE.md.
from backend.market_data.providers.webull_provider import _epoch_ms_to_ny

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STREAM_LOG = _PROJECT_ROOT / "logs" / "webull_data_streaming_sdk.log"

_SnapshotCb = Callable[[str, float, float | None, "object", float | None, float | None, float | None], None]
_TradeCb = Callable[[str, float, float | None, "object", str | None], None]
_StatusCb = Callable[[str], None]

# Webull tick "side" codes seen in the wild. 1 = buy-initiated,
# 2 = sell-initiated; anything else (incl. 3 = neutral) → unknown.
_SIDE_MAP = {1: "buy", 2: "sell", "1": "buy", "2": "sell",
             "BUY": "buy", "SELL": "sell", "B": "buy", "S": "sell"}


def _stream_logger() -> logging.Logger:
    """A dedicated logger for the SDK so it doesn't write ``webull_data_streaming_sdk.log``
    into the process CWD (the SDK's ``_init_logger`` default)."""
    lg = logging.getLogger("webull.data.streaming")
    if not any(getattr(h, "_marketlens_stream", False) for h in lg.handlers):
        _STREAM_LOG.parent.mkdir(exist_ok=True)
        h = TimedRotatingFileHandler(_STREAM_LOG, when="H", interval=1,
                                     backupCount=24, encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(thread)d %(asctime)s %(name)s %(levelname)s %(message)s"))
        h._marketlens_stream = True  # type: ignore[attr-defined]
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
    return lg


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class WebullStreamClient:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        *,
        region: str = "us",
        sandbox: bool = True,
        mqtt_host: str = "",
        on_snapshot: _SnapshotCb | None = None,
        on_trade: _TradeCb | None = None,
        on_status: _StatusCb | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._region = region
        self._http_host = "api.sandbox.webull.com" if sandbox else "api.webull.com"
        self._mqtt_host = mqtt_host or None
        self.on_snapshot = on_snapshot
        self.on_trade = on_trade
        self.on_status = on_status

        self._lock = threading.Lock()
        self._subscribed: set[str] = set()
        self._last_msg_at: dict[str, float] = {}
        self._client = None  # DataStreamingClient, built in start()
        self._connected = False
        self._started = False

    # -- lifecycle ----------------------------------------------------

    def _build_client(self):
        from webull.data.data_streaming_client import DataStreamingClient

        session_id = uuid.uuid4().hex
        client = DataStreamingClient(
            self._app_key, self._app_secret, self._region, session_id,
            http_host=self._http_host, mqtt_host=self._mqtt_host,
        )
        client.on_connect_success = self._on_connect
        client.on_quotes_message = self._on_message
        client.on_subscribe_success = self._on_subscribe_success
        return client

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        try:
            self._client = self._build_client()
            # async mode → daemon background thread; custom logger keeps the
            # SDK from writing its log file into the CWD.
            self._client.connect_and_loop_start(customer_logger=_stream_logger())
            logger.info("Webull stream: connecting (%d symbols queued)", len(self._subscribed))
        except Exception as e:  # noqa: BLE001 — never take the app down
            logger.error("Webull stream failed to start: %s", e)
            self._started = False

    def stop(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.unsubscribe(unsubscribe_all=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            client.loop_stop()
        except Exception:  # noqa: BLE001
            pass
        self._connected = False
        logger.info("Webull stream: stopped")

    # -- subscription ----------------------------------------------------

    def subscribe(self, symbols) -> None:
        new = {s.upper() for s in symbols if s}
        with self._lock:
            added = new - self._subscribed
            self._subscribed |= new
        if added and self._connected and self._client is not None:
            self._do_subscribe(sorted(added))

    def unsubscribe(self, symbols) -> None:
        drop = {s.upper() for s in symbols if s}
        with self._lock:
            drop &= self._subscribed
            self._subscribed -= drop
        for s in drop:
            self._last_msg_at.pop(s, None)
        if drop and self._connected and self._client is not None:
            try:
                from webull.data.common.category import Category
                from webull.data.common.subscribe_type import SubscribeType

                self._client.unsubscribe(
                    symbols=sorted(drop), category=Category.US_STOCK.name,
                    sub_types=[SubscribeType.SNAPSHOT.name, SubscribeType.TICK.name],
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Webull stream unsubscribe failed: %s", e)

    def _do_subscribe(self, symbols: list[str]) -> None:
        if not symbols:
            return
        try:
            from webull.data.common.category import Category
            from webull.data.common.subscribe_type import SubscribeType

            self._client.subscribe(
                symbols, Category.US_STOCK.name,
                [SubscribeType.SNAPSHOT.name, SubscribeType.TICK.name],
            )
            logger.info("Webull stream: subscribed %d symbols", len(symbols))
        except Exception as e:  # noqa: BLE001
            logger.warning("Webull stream subscribe failed: %s", e)

    # -- health ----------------------------------------------------

    def is_live(self, symbol: str) -> bool:
        """True when the connection is up and this symbol produced a message
        within ``streaming_stale_seconds``."""
        if not self._connected:
            return False
        last = self._last_msg_at.get(symbol.upper())
        if last is None:
            return False
        return (time.monotonic() - last) < settings.webull.streaming_stale_seconds

    # -- SDK callbacks (run on the SDK's thread) --------------------------

    def _on_connect(self, client, api_client, session_id) -> None:
        self._connected = True
        logger.info("Webull stream: connected (session %s)", session_id)
        if self.on_status:
            try:
                self.on_status("connected")
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            syms = sorted(self._subscribed)
        self._do_subscribe(syms)

    def _on_subscribe_success(self, client, api_client, session_id) -> None:
        logger.debug("Webull stream: subscribe ack (session %s)", session_id)

    def _on_message(self, client, topic, result) -> None:
        try:
            if topic == "snapshot":
                self._handle_snapshot(result)
            elif topic == "tick":
                self._handle_tick(result)
            elif topic == "notice":
                logger.info("Webull stream notice: %s", result)
        except Exception as e:  # noqa: BLE001 — a bad message must not kill the loop
            logger.warning("Webull stream message handler error (%s): %s", topic, e)

    def _mark(self, symbol: str) -> None:
        self._last_msg_at[symbol] = time.monotonic()

    def _handle_snapshot(self, r) -> None:
        basic = r.get_basic()
        sym = (basic.symbol or "").upper()
        if not sym:
            return
        ts = _epoch_ms_to_ny(basic.timestamp)
        price = _num(r.get_price())
        if price is None:
            return
        self._mark(sym)
        if self.on_snapshot:
            self.on_snapshot(
                sym, price, _num(r.get_volume()), ts,
                _num(r.get_high()), _num(r.get_low()), _num(r.get_open()),
            )

    def _handle_tick(self, r) -> None:
        basic = r.get_basic()
        sym = (basic.symbol or "").upper()
        if not sym:
            return
        ts = _epoch_ms_to_ny(basic.timestamp or r.get_time())
        price = _num(r.get_price())
        if price is None:
            return
        side = _SIDE_MAP.get(r.get_side())
        self._mark(sym)
        if self.on_trade:
            self.on_trade(sym, price, _num(r.get_volume()), ts, side)


# --- module singleton ----------------------------------------------------

_client: WebullStreamClient | None = None


def get_webull_stream_client() -> WebullStreamClient | None:
    """Return the process-wide stream client, or ``None`` when streaming is
    disabled / not credentialed. Idempotent."""
    global _client
    if _client is not None:
        return _client
    wb = settings.webull
    if not (wb.streaming_enabled and wb.app_key and wb.app_secret):
        return None
    _client = WebullStreamClient(
        wb.app_key, wb.app_secret,
        region="us", sandbox=wb.use_sandbox, mqtt_host=wb.streaming_mqtt_host,
    )
    return _client
