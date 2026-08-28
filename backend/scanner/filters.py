"""
Composable filter system for the MarketLens scanner.

Filters select a subset of ScanResult objects based on arbitrary conditions.
They are composable: individual filters can be combined with AND/OR/NOT
operators to build complex expressions.

Usage
-----
    from backend.scanner.filters import FilterRegistry, TrendScoreGt

    registry = FilterRegistry()
    f = registry.build({"type": "trend_score_gt", "params": {"threshold": 70}})
    if f.matches(scan_result):
        # pass
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeVar

from backend.engines.timeframe import Timeframe

if TYPE_CHECKING:
    from backend.scanner.scanner import ScanResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

T = TypeVar("T", bound="ScanResult")


class Filter(ABC):
    """Abstract base for all scanner filters."""

    @abstractmethod
    def matches(self, result: ScanResult) -> bool:
        """Return True if the result satisfies this filter's condition."""
        raise NotImplementedError

    @abstractmethod
    def describe(self) -> str:
        """Human-readable description of the filter."""
        raise NotImplementedError

    def __and__(self, other: Filter) -> AndFilter:
        return AndFilter([self, other])

    def __or__(self, other: Filter) -> OrFilter:
        return OrFilter([self, other])

    def __invert__(self) -> NotFilter:
        return NotFilter(self)


class AndFilter(Filter):
    """All sub-filters must match."""

    def __init__(self, filters: list[Filter]):
        self.filters = filters

    def matches(self, result: ScanResult) -> bool:
        return all(f.matches(result) for f in self.filters)

    def describe(self) -> str:
        return " AND ".join(f"({f.describe()})" for f in self.filters)


class OrFilter(Filter):
    """At least one sub-filter must match."""

    def __init__(self, filters: list[Filter]):
        self.filters = filters

    def matches(self, result: ScanResult) -> bool:
        return any(f.matches(result) for f in self.filters)

    def describe(self) -> str:
        return " OR ".join(f"({f.describe()})" for f in self.filters)


class NotFilter(Filter):
    """Invert the result of a sub-filter."""

    def __init__(self, f: Filter):
        self.filter = f

    def matches(self, result: ScanResult) -> bool:
        return not self.filter.matches(result)

    def describe(self) -> str:
        return f"NOT ({self.filter.describe()})"


# ---------------------------------------------------------------------------
# Concrete filters
# ---------------------------------------------------------------------------

class TrendScoreGt(Filter):
    """Total score greater than threshold."""

    def __init__(self, threshold: float):
        self.threshold = threshold

    def matches(self, result: ScanResult) -> bool:
        return result.calculate_total_score() > self.threshold

    def describe(self) -> str:
        return f"total_score > {self.threshold}"


class TrendScoreLt(Filter):
    """Total score less than threshold."""

    def __init__(self, threshold: float):
        self.threshold = threshold

    def matches(self, result: ScanResult) -> bool:
        return result.calculate_total_score() < self.threshold

    def describe(self) -> str:
        return f"total_score < {self.threshold}"


class TimeframeDirection(Filter):
    """Trend direction for a specific timeframe matches expected direction."""

    def __init__(self, timeframe: str, direction: str, min_confidence: float = 0.0):
        self.tf_raw = timeframe
        self.direction = direction.lower()
        self.min_confidence = min_confidence

    def _resolve(self, tf_raw: str) -> Timeframe | None:
        mapping = {
            "1m": Timeframe.ONE_MINUTE,
            "5m": Timeframe.FIVE_MINUTE,
            "15m": Timeframe.FIFTEEN_MINUTE,
            "30m": Timeframe.THIRTY_MINUTE,
            "1h": Timeframe.ONE_HOUR,
            "4h": Timeframe.FOUR_HOUR,
            "1d": Timeframe.ONE_DAY,
            "1w": Timeframe.ONE_WEEK,
            "1_minute": Timeframe.ONE_MINUTE,
            "five_minute": Timeframe.FIVE_MINUTE,
            "fifteen_minute": Timeframe.FIFTEEN_MINUTE,
            "thirty_minute": Timeframe.THIRTY_MINUTE,
            "one_hour": Timeframe.ONE_HOUR,
            "four_hour": Timeframe.FOUR_HOUR,
            "one_day": Timeframe.ONE_DAY,
            "one_week": Timeframe.ONE_WEEK,
        }
        return mapping.get(tf_raw)

    def matches(self, result: ScanResult) -> bool:
        tf = self._resolve(self.tf_raw)
        if tf is None:
            return False
        tf_key = tf.name
        signal = result.trend_signals.get(tf_key)
        if signal is None:
            return False
        return (
            signal.get("direction", "").lower() == self.direction
            and signal.get("confidence", 0.0) >= self.min_confidence
        )

    def describe(self) -> str:
        return f"{self.tf_raw} = {self.direction} (conf ≥ {self.min_confidence})"


class DailyBullish(TimeframeDirection):
    """Daily trend is bullish (uptrend, confidence ≥ threshold)."""

    def __init__(self, min_confidence: float = 0.5):
        super().__init__("1d", "uptrend", min_confidence)


class DailyBearish(TimeframeDirection):
    """Daily trend is bearish (downtrend, confidence ≥ threshold)."""

    def __init__(self, min_confidence: float = 0.5):
        super().__init__("1d", "downtrend", min_confidence)


class MinTimeframeBullish(Filter):
    """At least N timeframes are bullish with minimum confidence."""

    def __init__(self, min_count: int = 3, min_confidence: float = 0.5):
        self.min_count = min_count
        self.min_confidence = min_confidence

    def matches(self, result: ScanResult) -> bool:
        count = 0
        for signal in result.trend_signals.values():
            if (
                signal.get("direction", "").lower() == "uptrend"
                and signal.get("confidence", 0.0) >= self.min_confidence
            ):
                count += 1
        return count >= self.min_count

    def describe(self) -> str:
        return f"≥{self.min_count} bullish timeframes"


class MinTimeframeBearish(Filter):
    """At least N timeframes are bearish with minimum confidence."""

    def __init__(self, min_count: int = 3, min_confidence: float = 0.5):
        self.min_count = min_count
        self.min_confidence = min_confidence

    def matches(self, result: ScanResult) -> bool:
        count = 0
        for signal in result.trend_signals.values():
            if (
                signal.get("direction", "").lower() == "downtrend"
                and signal.get("confidence", 0.0) >= self.min_confidence
            ):
                count += 1
        return count >= self.min_count

    def describe(self) -> str:
        return f"≥{self.min_count} bearish timeframes"


class MTFAlignment(Filter):
    """All available timeframes agree on the same direction."""

    def __init__(self, min_timeframes: int = 3, min_confidence: float = 0.5):
        self.min_timeframes = min_timeframes
        self.min_confidence = min_confidence

    def matches(self, result: ScanResult) -> bool:
        if not result.trend_signals:
            return False
        direction = None
        count = 0
        for signal in result.trend_signals.values():
            if signal.get("confidence", 0.0) < self.min_confidence:
                continue
            d = signal.get("direction", "").lower()
            if d not in ("uptrend", "downtrend"):
                continue
            if direction is None:
                direction = d
            elif d != direction:
                return False  # Disagreement found
            count += 1
        return count >= self.min_timeframes

    def describe(self) -> str:
        return (
            f"all timeframes aligned (≥{self.min_timeframes} confirmed, "
            f"conf ≥ {self.min_confidence})"
        )


class RSIOversold(Filter):
    """RSI is below threshold (oversold)."""

    def __init__(self, threshold: float = 30.0):
        self.threshold = threshold

    def matches(self, result: ScanResult) -> bool:
        rsi = result.indicator_values.get("rsi")
        return rsi is not None and rsi < self.threshold

    def describe(self) -> str:
        return f"RSI < {self.threshold}"


class RSIOverbought(Filter):
    """RSI is above threshold (overbought)."""

    def __init__(self, threshold: float = 70.0):
        self.threshold = threshold

    def matches(self, result: ScanResult) -> bool:
        rsi = result.indicator_values.get("rsi")
        return rsi is not None and rsi > self.threshold

    def describe(self) -> str:
        return f"RSI > {self.threshold}"


class MACDBullish(Filter):
    """MACD histogram is positive."""

    def matches(self, result: ScanResult) -> bool:
        macd = result.indicator_values.get("macd")
        return macd is not None and macd > 0

    def describe(self) -> str:
        return "MACD > 0"


class MACDBearish(Filter):
    """MACD histogram is negative."""

    def matches(self, result: ScanResult) -> bool:
        macd = result.indicator_values.get("macd")
        return macd is not None and macd < 0

    def describe(self) -> str:
        return "MACD < 0"


class HighVolume(Filter):
    """Volume is above a given threshold."""

    def __init__(self, min_volume: int = 1_000_000):
        self.min_volume = min_volume

    def matches(self, result: ScanResult) -> bool:
        vol = result.indicator_values.get("volume")
        return vol is not None and vol >= self.min_volume

    def describe(self) -> str:
        return f"volume ≥ {self.min_volume:,}"


class SignalPresent(Filter):
    """Result contains at least one of the given signal names."""

    def __init__(self, signals: list[str]):
        self.signals = [s.upper() for s in signals]

    def matches(self, result: ScanResult) -> bool:
        result_signals = [s.upper() for s in result.signals]
        return any(s in result_signals for s in self.signals)

    def describe(self) -> str:
        return f"signal in {self.signals}"


class PriceAbove(Filter):
    """Last price is above a threshold."""

    def __init__(self, price: float):
        self.price = price

    def matches(self, result: ScanResult) -> bool:
        p = result.indicator_values.get("price") or (
            result.quote.price if result.quote else None
        )
        return p is not None and p > self.price

    def describe(self) -> str:
        return f"price > {self.price}"


class PriceBelow(Filter):
    """Last price is below a threshold."""

    def __init__(self, price: float):
        self.price = price

    def matches(self, result: ScanResult) -> bool:
        p = result.indicator_values.get("price") or (
            result.quote.price if result.quote else None
        )
        return p is not None and p < self.price

    def describe(self) -> str:
        return f"price < {self.price}"


class ADXStrong(Filter):
    """ADX is above a threshold (strong trend)."""

    def __init__(self, threshold: float = 25.0):
        self.threshold = threshold

    def matches(self, result: ScanResult) -> bool:
        adx = result.indicator_values.get("adx")
        return adx is not None and adx > self.threshold

    def describe(self) -> str:
        return f"ADX > {self.threshold}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_FILTER_REGISTRY: dict[str, type[Filter]] = {
    "trend_score_gt": TrendScoreGt,
    "trend_score_lt": TrendScoreLt,
    "timeframe_direction": TimeframeDirection,
    "daily_bullish": DailyBullish,
    "daily_bearish": DailyBearish,
    "min_timeframe_bullish": MinTimeframeBullish,
    "min_timeframe_bearish": MinTimeframeBearish,
    "mtf_alignment": MTFAlignment,
    "rsi_oversold": RSIOversold,
    "rsi_overbought": RSIOverbought,
    "macd_bullish": MACDBullish,
    "macd_bearish": MACDBearish,
    "high_volume": HighVolume,
    "signal_present": SignalPresent,
    "price_above": PriceAbove,
    "price_below": PriceBelow,
    "adx_strong": ADXStrong,
}


class FilterRegistry:
    """
    Builds :class:`Filter` instances from plain dict configs.

    Example
    -------
        registry = FilterRegistry()
        f = registry.build({
            "type": "daily_bullish",
            "params": {"min_confidence": 0.6}
        })
    """

    def __init__(self, registry: dict[str, type[Filter]] | None = None):
        self._registry = registry or _FILTER_REGISTRY

    def list_types(self) -> list[str]:
        """Return all registered filter type names."""
        return list(self._registry.keys())

    def build(self, config: dict[str, Any]) -> Filter:
        """
        Construct a filter from a config dict.

        Expected shape::

            {
                "type": "daily_bullish",          # required
                "params": {"min_confidence": 0.6} # optional
            }

        Raises ``ValueError`` for unknown types.
        """
        ftype = config.get("type")
        if not ftype:
            raise ValueError("Filter config must include 'type'")
        ftype = ftype.lower()
        if ftype not in self._registry:
            raise ValueError(f"Unknown filter type: {ftype!r}. Available: {self.list_types()}")

        cls = self._registry[ftype]
        params = config.get("params", {})

        try:
            return cls(**params)
        except TypeError as exc:
            raise ValueError(
                f"Invalid params for filter '{ftype}': {exc}"
            ) from exc

    def build_conjunction(
        self, expressions: list[dict[str, Any]]
    ) -> Filter:
        """
        Build an AND conjunction from a list of filter configs.

        ``[{"type": "daily_bullish"}, {"type": "high_volume"}]``
        → ``DailyBullish() & HighVolume()``
        """
        return AndFilter([self.build(expr) for expr in expressions])

    def build_disjunction(
        self, expressions: list[dict[str, Any]]
    ) -> Filter:
        """Build an OR disjunction from a list of filter configs."""
        return OrFilter([self.build(expr) for expr in expressions])


# Default instance used by the scanner router
default_registry = FilterRegistry()


# ---------------------------------------------------------------------------
# Helper: apply a filter to a list of ScanResults
# ---------------------------------------------------------------------------

def apply_filter(
    results: list[ScanResult],
    f: Filter,
) -> list[ScanResult]:
    """Return only the results matching the given filter."""
    return [r for r in results if f.matches(r)]
