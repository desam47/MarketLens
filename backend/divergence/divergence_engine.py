"""
Divergence detection engine.

A *divergence* is when price makes one extreme (higher high, lower
low) and a momentum indicator makes the opposite extreme. The spec
requires four core types:

- Bullish RSI divergence   : price lower-low, RSI higher-low
- Bearish RSI divergence   : price higher-high, RSI lower-high
- Bullish MACD divergence  : price lower-low, MACD higher-low
- Bearish MACD divergence  : price higher-high, MACD lower-high
- Volume divergence        : price makes a new extreme on declining
                              volume (a meaningful variant of the
                              above — it's an "absence" of confirmation)

The engine uses the same pivot-based scaffolding as the
``SupportResistanceEngine`` (swing high / swing low detection) so that
divergences are anchored on confirmed price pivots, not on every bar.
Historical-only — at every pivot, only data available at that
timestamp is consulted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DivergenceType(StrEnum):
    BULLISH_RSI = "bullish_rsi_divergence"
    BEARISH_RSI = "bearish_rsi_divergence"
    BULLISH_MACD = "bullish_macd_divergence"
    BEARISH_MACD = "bearish_macd_divergence"
    BULLISH_VOLUME = "bullish_volume_divergence"
    BEARISH_VOLUME = "bearish_volume_divergence"


class DivergenceDirection(StrEnum):
    BULLISH = "bullish"  # implies a potential upward reversal
    BEARISH = "bearish"  # implies a potential downward reversal


_TYPE_TO_DIRECTION: dict[DivergenceType, DivergenceDirection] = {
    DivergenceType.BULLISH_RSI: DivergenceDirection.BULLISH,
    DivergenceType.BEARISH_RSI: DivergenceDirection.BEARISH,
    DivergenceType.BULLISH_MACD: DivergenceDirection.BULLISH,
    DivergenceType.BEARISH_MACD: DivergenceDirection.BEARISH,
    DivergenceType.BULLISH_VOLUME: DivergenceDirection.BULLISH,
    DivergenceType.BEARISH_VOLUME: DivergenceDirection.BEARISH,
}


@dataclass(frozen=True)
class Divergence:
    """A single detected divergence.

    ``strength`` is a 0..1 score combining pivot prominence and
    indicator distance; higher = stronger / more trustworthy.
    """

    type: DivergenceType
    direction: DivergenceDirection
    symbol: str
    timeframe: str
    pivot_a_index: int
    pivot_b_index: int
    pivot_a_price: float
    pivot_b_price: float
    pivot_a_indicator: float
    pivot_b_indicator: float
    timestamp: datetime | None
    strength: float

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "direction": self.direction.value,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "pivot_a_index": self.pivot_a_index,
            "pivot_b_index": self.pivot_b_index,
            "pivot_a_price": self.pivot_a_price,
            "pivot_b_price": self.pivot_b_price,
            "pivot_a_indicator": self.pivot_a_indicator,
            "pivot_b_indicator": self.pivot_b_indicator,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "strength": self.strength,
        }


class DivergenceEngine:
    """Detects RSI / MACD / volume divergences on confirmed pivots.

    Parameters
    ----------
    pivot_lookback:
        Bars on either side required to confirm a swing high/low. Mirrors
        the lookback used by ``SupportResistanceEngine`` so both engines
        see the same pivots.
    max_pivots_apart:
        Only consider pivot pairs within this many bars of each other
        when scanning for divergences. Keeps the engine from
        accidentally matching very old pivots to recent ones.
    min_price_delta_pct:
        Minimum percentage move between the two pivots required for a
        divergence to register (e.g. ``0.5`` = 0.5%). Filters out noise.
    """

    def __init__(
        self,
        pivot_lookback: int = 2,
        max_pivots_apart: int = 60,
        min_price_delta_pct: float = 0.5,
    ) -> None:
        if pivot_lookback < 1:
            raise ValueError("pivot_lookback must be >= 1")
        if max_pivots_apart < 2:
            raise ValueError("max_pivots_apart must be >= 2")
        if min_price_delta_pct < 0:
            raise ValueError("min_price_delta_pct must be >= 0")
        self.pivot_lookback = pivot_lookback
        self.max_pivots_apart = max_pivots_apart
        self.min_price_delta_pct = min_price_delta_pct

    # --- public API ---

    def detect(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        volumes: Sequence[float] | None = None,
        rsi: Sequence[float] | None = None,
        macd: Sequence[float] | None = None,
        timestamps: Sequence[datetime] | None = None,
        symbol: str = "",
        timeframe: str = "",
    ) -> list[Divergence]:
        """Scan for divergences.

        ``highs`` / ``lows`` / ``closes`` / ``volumes`` must all be
        index-aligned. ``rsi`` and ``macd`` are optional — only the
        divergence types whose input is provided are reported.
        """
        n = len(closes)
        if n != len(highs) or n != len(lows):
            raise ValueError("highs, lows, closes must align 1:1")
        if n < 2 * self.pivot_lookback + 2:
            return []
        if timestamps is not None and len(timestamps) != n:
            raise ValueError("timestamps must align 1:1 with closes")

        # Confirm pivots first. A swing high at index ``i`` means highs[i]
        # is greater than the ``pivot_lookback`` highs on either side.
        swing_highs = self._swing_high_indices(highs)
        swing_lows = self._swing_low_indices(lows)

        out: list[Divergence] = []
        if rsi is not None:
            out.extend(
                self._scan_rsi(
                    highs, lows, rsi, swing_highs, swing_lows, timestamps, symbol, timeframe
                )
            )
        if macd is not None:
            out.extend(
                self._scan_macd(
                    highs, lows, macd, swing_highs, swing_lows, timestamps, symbol, timeframe
                )
            )
        if volumes is not None:
            out.extend(
                self._scan_volume(
                    highs,
                    lows,
                    closes,
                    volumes,
                    swing_highs,
                    swing_lows,
                    timestamps,
                    symbol,
                    timeframe,
                )
            )
        # Sort by the *later* pivot index so the latest divergences are last
        out.sort(key=lambda d: d.pivot_b_index)
        return out

    # --- internals ---

    def _swing_high_indices(self, highs: Sequence[float]) -> list[int]:
        n = len(highs)
        out: list[int] = []
        lb = self.pivot_lookback
        for i in range(lb, n - lb):
            window_max = max(highs[i - lb : i + lb + 1])
            if highs[i] == window_max:
                # Use a uniqueness guard: a "flat top" counts as one pivot
                if not out or i - out[-1] > lb:
                    out.append(i)
        return out

    def _swing_low_indices(self, lows: Sequence[float]) -> list[int]:
        n = len(lows)
        out: list[int] = []
        lb = self.pivot_lookback
        for i in range(lb, n - lb):
            window_min = min(lows[i - lb : i + lb + 1])
            if lows[i] == window_min:
                if not out or i - out[-1] > lb:
                    out.append(i)
        return out

    def _scan_rsi(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        rsi: Sequence[float],
        swing_highs: Sequence[int],
        swing_lows: Sequence[int],
        timestamps: Sequence[datetime] | None,
        symbol: str,
        timeframe: str,
    ) -> list[Divergence]:
        out: list[Divergence] = []
        # Bearish: price higher-high, RSI lower-high
        for a, b in self._consecutive_pairs(swing_highs):
            if b - a > self.max_pivots_apart:
                continue
            if not self._price_moved_enough(highs[a], highs[b]):
                continue
            if highs[b] > highs[a] and rsi[b] < rsi[a]:
                out.append(
                    self._make(
                        DivergenceType.BEARISH_RSI,
                        a,
                        b,
                        highs[a],
                        highs[b],
                        rsi[a],
                        rsi[b],
                        timestamps[b] if timestamps else None,
                        symbol,
                        timeframe,
                    )
                )
        # Bullish: price lower-low, RSI higher-low
        for a, b in self._consecutive_pairs(swing_lows):
            if b - a > self.max_pivots_apart:
                continue
            if not self._price_moved_enough(lows[a], lows[b]):
                continue
            if lows[b] < lows[a] and rsi[b] > rsi[a]:
                out.append(
                    self._make(
                        DivergenceType.BULLISH_RSI,
                        a,
                        b,
                        lows[a],
                        lows[b],
                        rsi[a],
                        rsi[b],
                        timestamps[b] if timestamps else None,
                        symbol,
                        timeframe,
                    )
                )
        return out

    def _scan_macd(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        macd: Sequence[float],
        swing_highs: Sequence[int],
        swing_lows: Sequence[int],
        timestamps: Sequence[datetime] | None,
        symbol: str,
        timeframe: str,
    ) -> list[Divergence]:
        out: list[Divergence] = []
        for a, b in self._consecutive_pairs(swing_highs):
            if b - a > self.max_pivots_apart:
                continue
            if not self._price_moved_enough(highs[a], highs[b]):
                continue
            if highs[b] > highs[a] and macd[b] < macd[a]:
                out.append(
                    self._make(
                        DivergenceType.BEARISH_MACD,
                        a,
                        b,
                        highs[a],
                        highs[b],
                        macd[a],
                        macd[b],
                        timestamps[b] if timestamps else None,
                        symbol,
                        timeframe,
                    )
                )
        for a, b in self._consecutive_pairs(swing_lows):
            if b - a > self.max_pivots_apart:
                continue
            if not self._price_moved_enough(lows[a], lows[b]):
                continue
            if lows[b] < lows[a] and macd[b] > macd[a]:
                out.append(
                    self._make(
                        DivergenceType.BULLISH_MACD,
                        a,
                        b,
                        lows[a],
                        lows[b],
                        macd[a],
                        macd[b],
                        timestamps[b] if timestamps else None,
                        symbol,
                        timeframe,
                    )
                )
        return out

    def _scan_volume(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        volumes: Sequence[float],
        swing_highs: Sequence[int],
        swing_lows: Sequence[int],
        timestamps: Sequence[datetime] | None,
        symbol: str,
        timeframe: str,
    ) -> list[Divergence]:
        out: list[Divergence] = []
        # Volume divergence is the "absence" of confirmation: price makes
        # a new extreme on declining volume. We use closes (not highs/lows)
        # as the price reference for the pair.
        for a, b in self._consecutive_pairs(swing_highs):
            if b - a > self.max_pivots_apart:
                continue
            if not self._price_moved_enough(closes[a], closes[b]):
                continue
            if closes[b] > closes[a] and volumes[b] < volumes[a]:
                out.append(
                    self._make(
                        DivergenceType.BEARISH_VOLUME,
                        a,
                        b,
                        closes[a],
                        closes[b],
                        volumes[a],
                        volumes[b],
                        timestamps[b] if timestamps else None,
                        symbol,
                        timeframe,
                    )
                )
        for a, b in self._consecutive_pairs(swing_lows):
            if b - a > self.max_pivots_apart:
                continue
            if not self._price_moved_enough(closes[a], closes[b]):
                continue
            if closes[b] < closes[a] and volumes[b] < volumes[a]:
                out.append(
                    self._make(
                        DivergenceType.BULLISH_VOLUME,
                        a,
                        b,
                        closes[a],
                        closes[b],
                        volumes[a],
                        volumes[b],
                        timestamps[b] if timestamps else None,
                        symbol,
                        timeframe,
                    )
                )
        return out

    def _consecutive_pairs(self, indices: Sequence[int]) -> list[tuple[int, int]]:
        return [(indices[i], indices[i + 1]) for i in range(len(indices) - 1)]

    def _price_moved_enough(self, a: float, b: float) -> bool:
        if a == 0:
            return False
        return abs(b - a) / abs(a) * 100.0 >= self.min_price_delta_pct

    def _make(
        self,
        t: DivergenceType,
        a: int,
        b: int,
        price_a: float,
        price_b: float,
        ind_a: float,
        ind_b: float,
        ts: datetime | None,
        symbol: str,
        timeframe: str,
    ) -> Divergence:
        # Strength: combine the relative price move and the relative
        # indicator distance; cap to [0, 1].
        price_pct = abs(price_b - price_a) / abs(price_a) if price_a else 0.0
        if ind_a:
            ind_pct = abs(ind_b - ind_a) / abs(ind_a)
        else:
            ind_pct = 0.0
        raw = 0.5 * min(price_pct, 0.20) / 0.20 + 0.5 * min(ind_pct, 1.0)
        strength = max(0.0, min(1.0, raw))
        return Divergence(
            type=t,
            direction=_TYPE_TO_DIRECTION[t],
            symbol=symbol,
            timeframe=timeframe,
            pivot_a_index=a,
            pivot_b_index=b,
            pivot_a_price=price_a,
            pivot_b_price=price_b,
            pivot_a_indicator=ind_a,
            pivot_b_indicator=ind_b,
            timestamp=ts,
            strength=strength,
        )


__all__ = [
    "DivergenceType",
    "DivergenceDirection",
    "Divergence",
    "DivergenceEngine",
]
