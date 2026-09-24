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
from collections.abc import Callable
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from backend.config.settings import settings

# Importing the REST provider installs its ``ApiClient.set_file_logger``
# monkey-patch (redirects the SDK's hard-coded relative log path into
# ``logs/``), its rate-limit log filter (MD-06), and gives us the shared
# epoch->NY helper. See CLAUDE.md.
from backend.market_data.providers.webull_provider import _epoch_ms_to_ny, _is_rate_limited
from backend.utils.timezone import now_ny

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_STREAM_LOG = _PROJECT_ROOT / "logs" / "webull_data_streaming_sdk.log"

# Same class of bug as ``ApiClient.set_file_logger`` (patched in
# webull_provider.py): ``QuotesClient.set_file_logger`` also writes to a
# bare CWD-relative filename and unconditionally does
# ``logging.getLogger(logger_name).addHandler(...)`` with no dedup guard.
# Passing ``customer_logger=_stream_logger()`` into ``connect_and_loop_start``
# avoids this on a normal connect, but the SDK's own internal retry path
# (seen live: "Protocol not supported" -> internal retry -> "exited due to
# thread terminated") re-invokes ``_init_logger()`` without forwarding our
# customer_logger, which falls through to the hard-coded-path branch and
# writes ``webull_data_streaming_sdk.log`` into the project root (confirmed
# live 2026-09-12) — and would leak one more duplicate handler per retry if
# left unpatched, the same growth pattern already fixed for the REST client.
# Patch at the source so it's safe regardless of which path the SDK takes.
_quotes_file_logger_paths_registered: set[tuple[str, str]] = set()


def _patch_quotes_client_logger() -> None:
    from webull.data.internal.quotes_client import QuotesClient

    orig_set_file_logger = QuotesClient.set_file_logger

    def _patched(
        self,
        path,
        log_level=logging.INFO,
        logger_name="webull.data",
        format_string=None,
        when="H",
        interval=1,
        backup_count=72,
    ):
        abs_path = str(_STREAM_LOG) if Path(path).name == path else path
        key = (logger_name, abs_path)
        if key in _quotes_file_logger_paths_registered:
            return None
        _quotes_file_logger_paths_registered.add(key)
        Path(abs_path).parent.mkdir(exist_ok=True)
        return orig_set_file_logger(
            self, abs_path, log_level, logger_name, format_string, when, interval, backup_count
        )

    QuotesClient.set_file_logger = _patched

    # Same missing-dedup-guard defect on the sibling stream-logger method —
    # ``QuotesClient.set_stream_logger`` unconditionally attaches a fresh
    # ``StreamHandler`` to the process-global ``webull.data`` logger, so the
    # same internal-retry path that leaks a file handler above (see comment
    # block) would also leak one more stdout handler per retry left
    # unpatched.
    orig_set_stream_logger = QuotesClient.set_stream_logger
    _quotes_stream_logger_names_registered: set[str] = set()

    def _patched_stream(
        self, log_level=logging.INFO, logger_name="webull.data", stream=None, format_string=None
    ):
        if logger_name in _quotes_stream_logger_names_registered:
            return None
        _quotes_stream_logger_names_registered.add(logger_name)
        return orig_set_stream_logger(self, log_level, logger_name, stream, format_string)

    QuotesClient.set_stream_logger = _patched_stream


_patch_quotes_client_logger()


# Stopping the SDK client always ends in a fake failure. ``loop_stop()`` sets paho's terminate
# flag, ``loop_forever`` then returns 1, and ``QuotesClient.connect_and_loop_forever`` treats any
# non-zero return as a server error: it logs "exception:loop ack code: 1, msg: Protocol not
# supported" at ERROR, then ``time.sleep(10)`` before it looks at the flag again. ``loop_stop()``
# joins that thread, so every stop blocked ~10 s (a reload took 10 s longer to shut down, and the
# event loop was frozen for it) and left an ERROR line: 1,544 of them in a week of logs, every
# one at a shutdown. Reproduced against real paho and a fake broker; disconnecting first does not
# change it, the SDK sleeps regardless.
_TEARDOWN_WAIT_S = 2.0
_teardowns_lock = threading.Lock()
_teardowns_in_flight = 0

# MD-06: the supervisor reconnected 24/7, including overnight when the market is
# closed and no data is needed. That's also when Webull's own servers 429 the
# hardest — /openapi/config failed 89, 79, and 85 times an hour between 01:00 and
# 04:00 ET (2026-09-24), each one a fresh client build (a token handshake, not
# free). Same extended-session window as ingestion_service._gapfill_1m_loop.
_SESSION_START_HOUR = 4
_SESSION_END_HOUR = 20
# A confirmed 429 means Webull explicitly asked us to slow down — skip the normal
# 5s-doubling backoff and wait this long outright, however small backoff currently is.
_RATE_LIMITED_BACKOFF_S = 300
# How often to recheck the session gate while paused outside it.
_SESSION_GATE_POLL_S = 300


def _in_extended_session(moment) -> bool:
    """True 04:00-20:00 ET, Mon-Fri — the same window ingestion runs in."""
    return moment.weekday() < 5 and _SESSION_START_HOUR <= moment.hour < _SESSION_END_HOUR


class _ExpectedStopFilter(logging.Filter):
    """Demote the SDK's "loop ack code" ERROR to INFO while we are the ones stopping it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.levelno == logging.ERROR
            and _teardowns_in_flight
            and "loop ack code" in record.getMessage()
        ):
            record.levelno, record.levelname = logging.INFO, "INFO"
        return True


logging.getLogger("webull.data").addFilter(_ExpectedStopFilter())

_SnapshotCb = Callable[
    [
        str,
        float,
        float | None,
        "object",
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
        float | None,
    ],
    None,
]
_TradeCb = Callable[[str, float, float | None, "object", str | None], None]
_BboCb = Callable[[str, float | None, float | None, float | None, float | None, "object"], None]
_StatusCb = Callable[[str], None]

# Webull tick "side" codes seen in the wild. 1 = buy-initiated,
# 2 = sell-initiated; anything else (incl. 3 = neutral) → unknown.
_SIDE_MAP = {
    1: "buy",
    2: "sell",
    "1": "buy",
    "2": "sell",
    "BUY": "buy",
    "SELL": "sell",
    "B": "buy",
    "S": "sell",
}


def _stream_logger() -> logging.Logger:
    """A dedicated logger for the SDK so it doesn't write ``webull_data_streaming_sdk.log``
    into the process CWD (the SDK's ``_init_logger`` default)."""
    lg = logging.getLogger("webull.data.streaming")
    if not any(getattr(h, "_marketlens_stream", False) for h in lg.handlers):
        _STREAM_LOG.parent.mkdir(exist_ok=True)
        h = TimedRotatingFileHandler(
            _STREAM_LOG, when="H", interval=1, backupCount=24, encoding="utf-8"
        )
        h.setFormatter(
            logging.Formatter("%(thread)d %(asctime)s %(name)s %(levelname)s %(message)s")
        )
        h._marketlens_stream = True  # type: ignore[attr-defined]
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
    return lg


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _field(obj, *names):
    """Read an SDK field across Webull SDK versions."""
    for name in names:
        value = getattr(obj, name, None)
        try:
            value = value() if callable(value) else value
        except Exception:  # noqa: BLE001
            continue
        if value is not None:
            return value
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
        on_bbo: _BboCb | None = None,
        on_status: _StatusCb | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._region = region
        self._http_host = "api.sandbox.webull.com" if sandbox else "api.webull.com"
        self._mqtt_host = mqtt_host or None
        self.on_snapshot = on_snapshot
        self.on_trade = on_trade
        self.on_bbo = on_bbo
        self.on_status = on_status

        self._lock = threading.Lock()
        self._subscribed: set[str] = set()
        self._last_msg_at: dict[str, float] = {}
        self._topic_counts: dict[str, int] = {}
        self._client = None  # DataStreamingClient, built by the supervisor
        self._connected = False
        self._started = False
        self._stop = threading.Event()
        self._supervisor: threading.Thread | None = None
        # Dedicated token dir so the streaming client's auth handshake
        # doesn't race the REST provider over conf/token.txt.
        self._token_dir = str(_PROJECT_ROOT / "conf" / "token_stream")

    # -- lifecycle ----------------------------------------------------

    def _build_client(self):
        from webull.data.data_streaming_client import DataStreamingClient

        session_id = uuid.uuid4().hex
        client = DataStreamingClient(
            self._app_key,
            self._app_secret,
            self._region,
            session_id,
            http_host=self._http_host,
            mqtt_host=self._mqtt_host,
        )
        try:
            Path(self._token_dir).mkdir(parents=True, exist_ok=True)
            client.set_token_dir(self._token_dir)
        except Exception:  # noqa: BLE001
            pass
        client.on_connect_success = self._on_connect
        client.on_quotes_message = self._on_message
        client.on_subscribe_success = self._on_subscribe_success
        return client

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self._stop.clear()
        self._supervisor = threading.Thread(
            target=self._supervise, name="webull-stream-supervisor", daemon=True
        )
        self._supervisor.start()

    def _sdk_alive(self) -> bool:
        c = self._client
        t = getattr(c, "_thread", None) if c is not None else None
        return bool(t and t.is_alive())

    def _teardown_client(self) -> None:
        global _teardowns_in_flight
        was_connected = self._connected
        c, self._client = self._client, None
        self._connected = False
        if was_connected and self.on_status:
            try:
                self.on_status("disconnected")
            except Exception:  # noqa: BLE001
                pass
        if c is None:
            return

        def _close() -> None:
            global _teardowns_in_flight
            try:
                # disconnect() before loop_stop(): loop_stop() is the call that blocks on the
                # SDK's 10 s sleep, and the broker allows only 5 connections per App Key, so the
                # DISCONNECT must not queue behind it.
                for call in (
                    lambda: c.unsubscribe(unsubscribe_all=True),
                    c.disconnect,
                    c.loop_stop,
                ):
                    try:
                        call()
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                with _teardowns_lock:
                    _teardowns_in_flight -= 1

        with _teardowns_lock:
            _teardowns_in_flight += 1
        # The SDK thread is a daemon and exits by itself once its sleep ends, so wait only briefly.
        closer = threading.Thread(target=_close, name="webull-sdk-teardown", daemon=True)
        closer.start()
        closer.join(timeout=_TEARDOWN_WAIT_S)

    def _supervise(self) -> None:
        """Keep an MQTT connection alive: (re)build the SDK client, wait for
        connect, and rebuild with exponential backoff if it dies or never
        connects. Survives a transient 429 during the token handshake
        instead of dying on it (the bare connect_and_loop_start did).

        Paused outside the extended session (MD-06) — no data is needed then,
        and it's also when reconnects were tripping Webull's rate limit the
        hardest. A confirmed 429 skips the normal backoff ramp and waits
        ``_RATE_LIMITED_BACKOFF_S`` outright.
        """
        backoff = 5
        paused = False
        while not self._stop.is_set():
            if not _in_extended_session(now_ny()):
                if not paused:
                    logger.info("Webull stream: outside the extended session — pausing reconnects")
                    paused = True
                self._stop.wait(_SESSION_GATE_POLL_S)
                continue
            if paused:
                logger.info("Webull stream: extended session started — resuming reconnects")
                paused = False
                backoff = 5

            rate_limited = False
            try:
                self._client = self._build_client()
                self._client.connect_and_loop_start(customer_logger=_stream_logger())
                logger.info("Webull stream: connecting (%d symbols queued)", len(self._subscribed))
            except Exception as e:  # noqa: BLE001
                rate_limited = _is_rate_limited(e)
                logger.warning("Webull stream: connect attempt failed: %s", e)
                self._client = None

            waited = 0
            while (
                not rate_limited and not self._stop.is_set() and not self._connected and waited < 25
            ):
                self._stop.wait(1)
                waited += 1

            if self._connected:
                backoff = 5
                while not self._stop.is_set() and self._connected and self._sdk_alive():
                    self._stop.wait(5)
                if not self._stop.is_set():
                    logger.warning("Webull stream: connection lost — rebuilding")
            elif not rate_limited:
                logger.warning("Webull stream: not connected after 25s — retrying in %ds", backoff)

            self._teardown_client()
            if self._stop.is_set():
                break
            if rate_limited:
                logger.warning(
                    "Webull stream: rate-limited — waiting %ds before retrying",
                    _RATE_LIMITED_BACKOFF_S,
                )
                self._stop.wait(_RATE_LIMITED_BACKOFF_S)
                backoff = 5
            else:
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 300)

        self._teardown_client()
        logger.info("Webull stream: supervisor exited")

    def stop(self) -> None:
        self._stop.set()
        self._teardown_client()
        sup = self._supervisor
        if sup is not None and sup.is_alive():
            sup.join(timeout=6)
        self._started = False
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
                    symbols=sorted(drop),
                    category=Category.US_STOCK.name,
                    sub_types=[
                        SubscribeType.QUOTE.name,
                        SubscribeType.SNAPSHOT.name,
                        SubscribeType.TICK.name,
                    ],
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
                symbols,
                Category.US_STOCK.name,
                [SubscribeType.QUOTE.name, SubscribeType.SNAPSHOT.name, SubscribeType.TICK.name],
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
            topic_key = str(topic).lower()
            count = self._topic_counts.get(topic_key, 0) + 1
            self._topic_counts[topic_key] = count
            if count <= 3:
                logger.info("Webull stream event received: topic=%s", topic_key)
            if topic_key == "snapshot":
                self._handle_snapshot(result)
            elif topic_key == "quote":
                self._handle_quote(result)
            elif topic_key == "tick":
                self._handle_tick(result)
            elif topic_key == "notice":
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
        bid = _num(_field(r, "get_bid", "get_bid_price", "get_buy_price"))
        ask = _num(_field(r, "get_ask", "get_ask_price", "get_sell_price"))
        bid_size = _num(_field(r, "get_bid_size", "get_bid_volume", "get_buy_volume"))
        ask_size = _num(_field(r, "get_ask_size", "get_ask_volume", "get_sell_volume"))
        if self.on_snapshot:
            self.on_snapshot(
                sym,
                price,
                _num(r.get_volume()),
                ts,
                _num(r.get_high()),
                _num(r.get_low()),
                _num(r.get_open()),
                bid,
                ask,
                bid_size,
                ask_size,
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

    def _handle_quote(self, r) -> None:
        basic = r.get_basic()
        sym = (basic.symbol or "").upper()
        if not sym:
            return
        bids = r.get_bids() or []
        asks = r.get_asks() or []
        bid = _num(bids[0].get_price()) if bids else None
        ask = _num(asks[0].get_price()) if asks else None
        bid_size = _num(bids[0].get_size()) if bids else None
        ask_size = _num(asks[0].get_size()) if asks else None
        ts = _epoch_ms_to_ny(basic.timestamp)
        self._mark(sym)
        if self.on_bbo:
            self.on_bbo(sym, bid, ask, bid_size, ask_size, ts)


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
        wb.app_key,
        wb.app_secret,
        region="us",
        sandbox=wb.use_sandbox,
        mqtt_host=wb.streaming_mqtt_host,
    )
    return _client
