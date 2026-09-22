"""Process-local 1-minute OHLCV aggregation for streamed trade prints.

The Webull stream delivers individual trades, while the chart channel speaks
in candles.  This module keeps a small, thread-safe rolling window per symbol
so the current 1-minute candle can be pushed immediately without another
provider request. Completed candles are handed to the background persistence
writer; REST history remains the authoritative fallback and can replace a
stream-derived row later.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from threading import RLock

from backend.utils.timezone import ensure_aware_ny

_ONE_MINUTE = timedelta(minutes=1)
_MAX_LATE_BUCKETS = 2
_MAX_BARS_PER_SYMBOL = 5
_MAX_SEEN_TRADES_PER_SYMBOL = 5_000


@dataclass(frozen=True)
class LiveBar:
    """A locally aggregated, currently forming or recently closed candle."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    timeframe: str = "1m"
    provider: str = "webull_stream"
    data_status: str = "LIVE"
    source: str = "local_trade_aggregation"

    def as_payload(self) -> dict[str, object]:
        """Return the bar fields used by the realtime channel."""
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "timestamp": self.timestamp,
            "timeframe": self.timeframe,
            "provider": self.provider,
            "data_status": self.data_status,
            "source": self.source,
        }


@dataclass(frozen=True)
class LiveBarUpdate:
    """Result of accepting one trade print."""

    current: LiveBar
    completed: tuple[LiveBar, ...] = ()
    revised_closed: tuple[LiveBar, ...] = ()


@dataclass
class _MutableBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    def add(self, price: float, size: int) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += size

    def freeze(self) -> LiveBar:
        return LiveBar(
            symbol=self.symbol,
            timestamp=self.timestamp,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


@dataclass
class _SymbolState:
    bars: OrderedDict[datetime, _MutableBar]
    current_bucket: datetime | None = None
    last_completed_bucket: datetime | None = None
    seen_trades: OrderedDict[tuple[datetime, float, int], None] | None = None


class LiveBarAggregator:
    """Bounded, thread-safe 1-minute aggregator for trade events.

    A late print can update one of the last two buckets. Older prints are
    ignored so a reconnect cannot rewrite arbitrary historical chart data.
    Exact duplicate prints are ignored before they can inflate volume.
    """

    def __init__(self) -> None:
        self._states: dict[str, _SymbolState] = {}
        self._lock = RLock()

    @staticmethod
    def _bucket(timestamp: datetime) -> datetime:
        ts = ensure_aware_ny(timestamp)
        return ts.replace(second=0, microsecond=0)

    @staticmethod
    def _size(value: float | int | None) -> int:
        try:
            return max(0, int(round(float(value or 0))))
        except (TypeError, ValueError):
            return 0

    def update(
        self,
        symbol: str,
        price: float,
        size: float | int | None,
        timestamp: datetime,
    ) -> LiveBarUpdate | None:
        """Accept one trade and return the current bar plus newly closed bars."""
        symbol = symbol.upper()
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None
        if not symbol or not isfinite(price):
            return None

        ts = ensure_aware_ny(timestamp)
        bucket = self._bucket(ts)
        volume = self._size(size)

        with self._lock:
            state = self._states.setdefault(
                symbol,
                _SymbolState(bars=OrderedDict(), seen_trades=OrderedDict()),
            )
            assert state.seen_trades is not None
            trade_key = (ts, price, volume)
            if trade_key in state.seen_trades:
                return None
            state.seen_trades[trade_key] = None
            while len(state.seen_trades) > _MAX_SEEN_TRADES_PER_SYMBOL:
                state.seen_trades.popitem(last=False)

            current = state.current_bucket
            if current is not None and bucket < current - (_ONE_MINUTE * _MAX_LATE_BUCKETS):
                return None

            completed: list[LiveBar] = []
            if current is not None and bucket > current:
                for completed_bucket in sorted(state.bars):
                    bar = state.bars[completed_bucket]
                    if completed_bucket < bucket and (
                        state.last_completed_bucket is None
                        or completed_bucket > state.last_completed_bucket
                    ):
                        completed.append(bar.freeze())
                if completed:
                    state.last_completed_bucket = completed[-1].timestamp

            bar = state.bars.get(bucket)
            if bar is None:
                bar = _MutableBar(
                    symbol=symbol,
                    timestamp=bucket,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=volume,
                )
                state.bars[bucket] = bar
            else:
                bar.add(price, volume)
            state.current_bucket = max(current, bucket) if current is not None else bucket

            while len(state.bars) > _MAX_BARS_PER_SYMBOL:
                oldest_bucket = min(state.bars)
                state.bars.pop(oldest_bucket, None)

            frozen = bar.freeze()
            # A late print may revise a candle that was already emitted and
            # persisted on rollover. Keep engine dispatch one-shot, but hand
            # the revised candle back to persistence so durable OHLCV/volume
            # converges on the stream.
            revised_closed = (
                (frozen,)
                if state.last_completed_bucket is not None and bucket <= state.last_completed_bucket
                else ()
            )
            return LiveBarUpdate(
                current=frozen,
                completed=tuple(completed),
                revised_closed=revised_closed,
            )

    def finalize_elapsed(self, as_of: datetime) -> tuple[LiveBar, ...]:
        """Emit candles whose minute has elapsed, even without a later print.

        Thinly traded symbols may not produce a tick in the following minute.
        Relying only on rollover would leave their last candle in memory until
        another trade arrives. The persistence timer calls this method, while
        ``last_completed_bucket`` keeps it idempotent with tick-driven rollover.
        """
        cutoff = self._bucket(as_of)
        completed: list[LiveBar] = []
        with self._lock:
            for state in self._states.values():
                state_completed: list[LiveBar] = []
                for bucket in sorted(state.bars):
                    if bucket >= cutoff:
                        break
                    if state.last_completed_bucket is None or bucket > state.last_completed_bucket:
                        state_completed.append(state.bars[bucket].freeze())
                if state_completed:
                    state.last_completed_bucket = state_completed[-1].timestamp
                    completed.extend(state_completed)
        return tuple(completed)

    def reset(self) -> None:
        """Clear all process-local state (used by tests and restart hooks)."""
        with self._lock:
            self._states.clear()


live_bar_aggregator = LiveBarAggregator()
