"""
TapeEngine — rolling per-symbol Time & Sales analytics.

Fed one trade print at a time by ``engine_registry.dispatch_trade``
(kind ``"trade"``), from the Webull MQTT stream. Raw prints live in a
time-bounded deque; each closed 1-second bucket is buffered for
``tape_repository.upsert_tape_bars`` (drained on ``get_snapshot`` and
when the buffer gets large — no dedicated flush thread).

``get_snapshot()`` derives, over the configured windows:
  - buy / sell / signed volume, buy ratio, trade count, VWAP
  - tape speed (prints/sec) and acceleration (fast window ÷ main window)
  - block count + last block (a print over the notional or size threshold)
  - a ``pressure`` bucket: heavy_buy | buy | neutral | sell | heavy_sell

Nothing here raises for bad input — a malformed print is dropped.
"""
from __future__ import annotations

import statistics
import threading
from datetime import datetime, timezone

from backend.config.settings import settings
from backend.utils.timezone import NY

_INF = float("inf")
_MAX_PENDING = 900  # hard cap so a persistent DB failure can't OOM us


def _to_epoch_s(ts) -> float:
    """datetime (naive = NY) / aware datetime / epoch number -> epoch seconds."""
    if ts is None:
        return datetime.now(timezone.utc).timestamp()
    if isinstance(ts, (int, float)):
        # heuristic: ms vs s
        return float(ts) / 1000.0 if ts > 1e11 else float(ts)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=NY)
        return ts.timestamp()
    return datetime.now(timezone.utc).timestamp()


def _epoch_s_to_naive_ny(sec: float) -> datetime:
    return datetime.fromtimestamp(sec, tz=timezone.utc).astimezone(NY).replace(tzinfo=None)


class TapeEngine:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.upper()
        cfg = settings.tape
        self._long_w = cfg.long_window_seconds
        self._main_w = cfg.window_seconds
        self._fast_w = cfg.fast_window_seconds
        self._lock = threading.Lock()

        # (ts_epoch_s, price, size, side) — pruned to _long_w
        self._prints: list[tuple[float, float, int, str]] = []
        self._last_price: float | None = None
        # Timestamp of whichever event (trade print or L1 snapshot) most
        # recently set ``_last_price`` — lets note_price() reject a
        # snapshot that's older than a trade we've already applied,
        # instead of unconditionally clobbering the price.
        self._last_price_ts: float | None = None
        self._last_trade_ts: float | None = None

        # current 1-second accumulator
        self._bucket_sec: int | None = None
        self._bucket: dict | None = None
        # rolling per-second signed-volume, for the pressure z-score
        self._sv_history: list[float] = []
        # closed 1s bars waiting to be persisted
        self._pending: list[dict] = []

    # -- ingest --------------------------------------------------------

    def update(self, symbol=None, price=None, size=None, timestamp=None, side=None, **_) -> None:
        try:
            if price is None:
                return
            price = float(price)
            size = int(size or 0)
        except (TypeError, ValueError):
            return
        ts_s = _to_epoch_s(timestamp)
        with self._lock:
            s = side if side in ("buy", "sell") else self._tick_rule(price)
            if self._last_price_ts is None or ts_s >= self._last_price_ts:
                self._last_price = price
                self._last_price_ts = ts_s
            self._last_trade_ts = ts_s
            self._prints.append((ts_s, price, size, s))
            cutoff = ts_s - self._long_w
            if self._prints[0][0] < cutoff:
                self._prints = [p for p in self._prints if p[0] >= cutoff]
            self._accumulate(int(ts_s), price, size, s)

    def seed(self, prints) -> None:
        """Replay historical (ts, price, size, side) tuples (oldest first)."""
        for ts, price, size, side in prints:
            self.update(price=price, size=size, timestamp=ts, side=side)

    def note_price(self, price, timestamp=None) -> None:
        """Update the last-seen price from an L1 snapshot (no trade).

        Keeps ``last_price`` fresh during a gap between prints and gives
        the tick rule a reference — but adds NO print, so buy/sell
        volume, pressure and tape speed are unaffected.
        """
        try:
            p = float(price)
        except (TypeError, ValueError):
            return
        ts = _to_epoch_s(timestamp)
        with self._lock:
            if self._last_price_ts is None or ts >= self._last_price_ts:
                self._last_price = p
                self._last_price_ts = ts
            if self._last_trade_ts is None or ts > self._last_trade_ts:
                self._last_trade_ts = ts

    def _tick_rule(self, price: float) -> str:
        if self._last_price is None or price >= self._last_price:
            return "buy"
        return "sell"

    def _accumulate(self, sec: int, price: float, size: int, side: str) -> None:
        if self._bucket_sec is None:
            self._open_bucket(sec)
        elif sec > self._bucket_sec:
            self._close_bucket()
            self._open_bucket(sec)
        b = self._bucket
        if b["open"] is None:
            b["open"] = price
        b["close"] = price
        b["high"] = max(b["high"], price)
        b["low"] = min(b["low"], price)
        b["volume"] += size
        b["trade_count"] += 1
        b["notional"] += price * size
        if side == "buy":
            b["buy_volume"] += size
        else:
            b["sell_volume"] += size
        if price * size >= settings.tape.block_notional or size >= settings.tape.block_size:
            b["block_count"] += 1

    def _open_bucket(self, sec: int) -> None:
        self._bucket_sec = sec
        self._bucket = {
            "open": None, "high": -_INF, "low": _INF, "close": None,
            "volume": 0, "buy_volume": 0, "sell_volume": 0,
            "trade_count": 0, "block_count": 0, "notional": 0.0,
        }

    def _close_bucket(self) -> None:
        b, sec = self._bucket, self._bucket_sec
        if b is None or sec is None or b["trade_count"] == 0:
            return
        signed = b["buy_volume"] - b["sell_volume"]
        self._sv_history.append(float(signed))
        if len(self._sv_history) > self._long_w:
            self._sv_history = self._sv_history[-self._long_w:]
        row = {
            "symbol": self.symbol,
            "timestamp": _epoch_s_to_naive_ny(sec),
            "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
            "volume": b["volume"], "buy_volume": b["buy_volume"],
            "sell_volume": b["sell_volume"], "signed_volume": signed,
            "trade_count": b["trade_count"], "block_count": b["block_count"],
            "vwap": (b["notional"] / b["volume"]) if b["volume"] else None,
        }
        self._pending.append(row)
        if len(self._pending) > _MAX_PENDING:
            self._pending = self._pending[-_MAX_PENDING:]

    def drain_pending(self) -> list[dict]:
        """Pop the closed 1-second bars accumulated so far (for persistence)."""
        with self._lock:
            # roll the current bucket if it's a full second in the past
            if self._bucket_sec is not None and self._last_trade_ts is not None:
                if int(self._last_trade_ts) > self._bucket_sec:
                    self._close_bucket()
                    self._bucket_sec = None
                    self._bucket = None
            out, self._pending = self._pending, []
        return out

    # -- read --------------------------------------------------------

    def _window(self, prints, now_s: float, w: int):
        lo = now_s - w
        return [p for p in prints if p[0] >= lo]

    def get_snapshot(self, now_s: float | None = None) -> dict:
        with self._lock:
            prints = list(self._prints)
            last_price = self._last_price
            last_ts = self._last_trade_ts
            sv_hist = list(self._sv_history)
        now_s = now_s if now_s is not None else (last_ts or _to_epoch_s(None))

        # Windows are nested (fast_w <= main_w <= long_w, enforced by
        # TapeSettings' cross-field validator) so each narrower window is
        # filtered from the previous one instead of re-scanning the full
        # ``prints`` list three times — this endpoint is polled every
        # ~15s per open symbol page.
        long_ = self._window(prints, now_s, self._long_w)
        main = self._window(long_, now_s, self._main_w)
        fast = self._window(main, now_s, self._fast_w)

        # Single pass over ``main`` for all of its derived stats, rather
        # than four separate full scans (buy_v, sell_v, notional, largest).
        buy_v = 0
        sell_v = 0
        notional = 0.0
        largest = 0
        for _, pr, sz, s in main:
            if s == "buy":
                buy_v += sz
            else:
                sell_v += sz
            notional += pr * sz
            if sz > largest:
                largest = sz
        tot_v = buy_v + sell_v
        signed_v = buy_v - sell_v
        buy_ratio = (buy_v / tot_v) if tot_v else None
        vwap = (notional / tot_v) if tot_v else None

        speed_main = len(main) / self._main_w
        speed_fast = len(fast) / self._fast_w
        accel = (speed_fast / speed_main) if speed_main else None

        blocks = [
            (ts, pr, sz, s) for ts, pr, sz, s in long_
            if pr * sz >= settings.tape.block_notional or sz >= settings.tape.block_size
        ]
        last_block = None
        if blocks:
            bt, bp, bs, bside = blocks[-1]
            last_block = {"price": bp, "size": bs, "side": bside,
                          "notional": round(bp * bs, 2),
                          "age_s": round(now_s - bt, 1)}

        pressure = self._pressure(signed_v, buy_ratio, len(main), sv_hist)

        return {
            "symbol": self.symbol,
            "last_price": last_price,
            "pressure": pressure,
            "window_s": self._main_w,
            "buy_volume": buy_v,
            "sell_volume": sell_v,
            "signed_volume": signed_v,
            "buy_ratio": round(buy_ratio, 3) if buy_ratio is not None else None,
            "trade_count": len(main),
            "vwap": round(vwap, 4) if vwap is not None else None,
            "largest_print": largest,
            "tape_speed": round(speed_main, 2),
            "tape_accel": round(accel, 2) if accel is not None else None,
            "block_count_5m": len(blocks),
            "last_block": last_block,
        }

    def _pressure(self, signed_v, buy_ratio, n_trades, sv_hist) -> str:
        if n_trades < 5 or buy_ratio is None:
            return "neutral"
        # z-score of the current window's signed volume vs recent 1s samples,
        # when we have a real baseline; otherwise fall back to the ratio.
        z = None
        if len(sv_hist) >= 20:
            mu = statistics.fmean(sv_hist)
            sd = statistics.pstdev(sv_hist)
            if sd > 0:
                # sv_hist holds one signed-volume sample per closed 1s
                # bucket, but signed_v is summed over the whole main
                # window (self._main_w seconds) — comparing them directly
                # inflates z by ~sqrt(main_w) (e.g. ~8x for a 60s window),
                # tripping heavy_pressure_z on almost any nonzero net flow.
                # Scale the per-second baseline up to the same window: mean
                # scales linearly with the number of samples summed, stdev
                # scales with its square root (for roughly independent
                # per-second samples). Cap the sample count at
                # ``len(sv_hist)``, not just ``self._main_w`` — a
                # freshly-started engine (or one with sparse trading) may
                # not yet have main_w seconds of *active* history, and
                # scaling by the full configured window would overstate
                # the expected baseline sum, producing a spurious signal.
                n = min(len(sv_hist), self._main_w)
                mu_w = mu * n
                sd_w = sd * (n ** 0.5)
                if sd_w > 0:
                    z = (signed_v - mu_w) / sd_w
        thr = settings.tape.heavy_pressure_z
        if z is not None:
            if z >= thr:
                return "heavy_buy"
            if z <= -thr:
                return "heavy_sell"
        if buy_ratio >= 0.68:
            return "heavy_buy"
        if buy_ratio >= 0.57:
            return "buy"
        if buy_ratio <= 0.32:
            return "heavy_sell"
        if buy_ratio <= 0.43:
            return "sell"
        return "neutral"
