"""
Market scanner and ranking system
"""

import asyncio
import inspect
import logging
import math
import time
from datetime import datetime
from typing import Any

from ..api.trend.registry import get_engine as get_trend_engine
from ..engines.timeframe import Timeframe
from ..market_data.services.manager import market_data_manager
from ..models.market_data import Quote
from ..observability import record_scan
from .explanation import build_signal_explanation

logger = logging.getLogger(__name__)


class ScanResult:
    """Result of scanning a single symbol"""

    def __init__(self, symbol: str, timestamp: datetime):
        self.symbol = symbol
        self.timestamp = timestamp
        self.quote: Quote | None = None
        self.change: float | None = None
        self.change_pct: float | None = None
        self.trend_signals: dict[str, Any] = {}
        self.indicator_values: dict[str, Any] = {}
        self.scores: dict[str, float] = {}
        self.rank: int | None = None
        self.signals: list[str] = []
        self.explanation: dict[str, Any] = {}

    def add_indicator(self, name: str, value: Any):
        """Add an indicator value"""
        self.indicator_values[name] = value

    def add_trend_signal(self, timeframe: str, signal: Any):
        """Add a trend signal"""
        self.trend_signals[timeframe] = signal

    def add_score(self, name: str, score: float):
        """Add a signed score. Callers are responsible for passing meaningful signs."""
        self.scores[name] = score

    def add_signal(self, signal: str):
        """Add a trading signal"""
        self.signals.append(signal)

    def calculate_total_score(self, weights: dict[str, float] | None = None) -> float:
        """Calculate weighted total score (unsigned magnitude — for display only)."""
        if not self.scores:
            return 0.0

        if weights is None:
            # Equal weighting if no weights provided
            weights = {name: 1.0 for name in self.scores.keys()}

        total_weight = sum(abs(weights.get(name, 0)) for name in self.scores.keys())
        if total_weight == 0:
            return 0.0

        weighted_sum = sum(
            self.scores.get(name, 0) * weights.get(name, 0) for name in self.scores.keys()
        )

        # Normalise to a 0-100 scale by dividing by total weight and scaling.
        # Signed average is divided by abs-sum so direction is preserved.
        return weighted_sum / total_weight

    def calculate_signed_total_score(self, weights: dict[str, float] | None = None) -> float:
        """Signed weighted average: positive = bullish, negative = bearish.

        Used by the ranking engine so 'strongest_bullish' ranks genuinely
        bullish stocks above neutral/negative ones, and 'strongest_bearish'
        ranks genuinely bearish stocks below neutral/positive ones.
        """
        if not self.scores:
            return 0.0
        if weights is None:
            # Default to directional-only weights to ensure a genuine bullish/bearish signal.
            # Magnitude-only factors (trend_strength, adx, volatility, volume) are excluded.
            # "macd" (the raw histogram) is also excluded: it's the same signal as
            # "momentum" (see _calculate_scores), just unnormalized by price, so
            # including both here would double-count one signal at a price-dependent
            # scale rather than adding independent information.
            weights = {
                "momentum": 1.0,
                "rsi": 1.0,
            }
        total_weight = sum(abs(weights.get(name, 0)) for name in self.scores.keys())
        if total_weight == 0:
            return 0.0
        return (
            sum(self.scores.get(name, 0) * weights.get(name, 0) for name in self.scores.keys())
            / total_weight
        )


class Scanner:
    """Market scanner that evaluates and ranks symbols"""

    def __init__(self):
        self.scan_results: dict[str, ScanResult] = {}
        self.rankings: list[tuple[str, float]] = []  # (symbol, score)
        self.last_scan_time: datetime | None = None

        # Tape snapshot cache per symbol — tape data updates slowly
        # and get_snapshot() drains the repository on every call,
        # so re-fetching it for every scan tick wastes resources.
        # TTL: 10 seconds.
        self._tape_cache: dict[str, tuple[float, dict]] = {}
        self._tape_cache_ttl: float = 10.0

        # Benchmark bars are shared by every symbol in a watchlist scan.
        # Keeping a short process-local cache prevents an RS filter from
        # turning a 30-second watchlist scan into one provider request per
        # symbol and benchmark.
        self._benchmark_bars_cache: dict[str, tuple[float, list]] = {}
        self._benchmark_bars_ttl: float = 300.0

        # Scoring weights for different factors
        self.score_weights = {
            "trend_strength": 0.25,
            "momentum": 0.20,
            "volatility": 0.15,
            "volume": 0.15,
            "rsi": 0.10,
            "macd": 0.10,
            "adx": 0.05,
        }

    def scan_symbol(
        self,
        symbol: str,
        historical_bars: list | None = None,
        quote: Quote | None = None,
        benchmark_bars: dict[str, list] | None = None,
    ) -> ScanResult:
        """Scan a single symbol and return results"""
        previous_result = self.scan_results.get(symbol) or self.scan_results.get(symbol.upper())
        result = ScanResult(symbol, datetime.now())

        try:
            # Get current quote - use provided quote if available, otherwise fetch
            if quote is not None:
                result.quote = quote
            else:
                quote = market_data_manager.get_quote(symbol)
                result.quote = quote

            # Get the shared trend engine for this symbol (Phase 3.9.6)
            # — was creating a fresh TrendEngine per scan, never seeded, so
            # all signals were 'unknown'. The registry returns a warmed-up,
            # live-fed engine.
            trend_engine = get_trend_engine(symbol)

            # Update trend engine with recent data (we'd need historical data in practice)
            # For now, we'll use the quote to update
            if quote:
                trend_engine.update(quote.price, quote.volume or 0, quote.timestamp, quote.provider)

                # Get trend signals for multiple timeframes. Phase 3.9.7:
                # hoist the import out of the loop so we don't pay the
                # __import__ cost on every symbol × timeframe.
                timeframes = [
                    "ONE_MINUTE",
                    "FIVE_MINUTE",
                    "FIFTEEN_MINUTE",
                    "ONE_HOUR",
                    "FOUR_HOUR",
                    "ONE_DAY",
                ]
                for tf_str in timeframes:
                    tf = getattr(Timeframe, tf_str)
                    trend_signal = trend_engine.get_current_trend(tf)
                    if trend_signal:
                        result.add_trend_signal(
                            tf_str,
                            {
                                "direction": trend_signal.direction.value,
                                "strength": trend_signal.strength.value,
                                "confidence": trend_signal.confidence,
                            },
                        )

            # Calculate technical indicators
            self._calculate_indicators(result, symbol, historical_bars, benchmark_bars)
            self._compute_change(result, historical_bars)

            # Calculate scores
            self._calculate_scores(result)

            # Generate trading signals
            self._generate_signals(result)
            result.explanation = build_signal_explanation(result, previous_result)

        except Exception as e:
            logger.error(f"Error scanning symbol {symbol}: {e}")
            # Still return a result, but it may be incomplete

        if not result.explanation:
            try:
                result.explanation = build_signal_explanation(result, previous_result)
            except Exception as explanation_error:  # pragma: no cover - defensive fallback
                logger.debug(
                    "Could not build scanner explanation for %s: %s",
                    symbol,
                    explanation_error,
                )
                result.explanation = {}

        self.scan_results[symbol] = result
        return result

    def _calculate_indicators(
        self,
        result: ScanResult,
        symbol: str,
        historical_bars: list | None = None,
        benchmark_bars: dict[str, list] | None = None,
    ):
        """Calculate technical indicators for the symbol.

        When a real bar is available from the latest timeframe, we use
        bar-derived values (close, high, low, volume) instead of hash-based
        placeholders. RSI / MACD / ADX pull from a 3-month daily bar
        history fetched via the market data manager (with optional DB
        caching); the manager returns ``[]`` on failure, in which case
        the indicator is recorded as ``None`` so downstream code can
        detect missing data rather than act on fake values.
        """
        try:
            if result.quote:
                price = result.quote.price
                result.add_indicator("price", price)
                result.add_indicator("volume", result.quote.volume or 0)

                # Try to enrich with the most recent bar's OHLC. RSI/MACD/ADX
                # still need history; we mark them as None so downstream code
                # can detect missing data rather than act on fake values.
                # Use the last bar from historical data if available (from batch fetch),
                # otherwise fall back to individual latest bar call.
                latest_bar = None
                if historical_bars and len(historical_bars) > 0:
                    # Use the most recent bar from the historical data we already fetched
                    latest_bar = historical_bars[-1]

                if latest_bar:
                    result.add_indicator("close", latest_bar.close)
                    result.add_indicator("high", latest_bar.high)
                    result.add_indicator("low", latest_bar.low)
                    # True range proxy for ATR; full ATR still needs history.
                    true_range = max(
                        latest_bar.high - latest_bar.low,
                        abs(latest_bar.high - price),
                        abs(latest_bar.low - price),
                    )
                    result.add_indicator("atr", true_range)
                else:
                    # Fall back to individual latest bar call if historical data not available
                    latest_bar = market_data_manager.get_latest_bar(symbol, "1d")
                    if latest_bar:
                        result.add_indicator("close", latest_bar.close)
                        result.add_indicator("high", latest_bar.high)
                        result.add_indicator("low", latest_bar.low)
                        # True range proxy for ATR; full ATR still needs history.
                        true_range = max(
                            latest_bar.high - latest_bar.low,
                            abs(latest_bar.high - price),
                            abs(latest_bar.low - price),
                        )
                        result.add_indicator("atr", true_range)
                    else:
                        result.add_indicator("close", price)
                        result.add_indicator("atr", 0.0)

                # Pull a 3-month daily history and feed it to the
                # windowed indicators. Use pre-fetched bars if available,
                # otherwise fetch via the market data manager.
                self._populate_windowed_indicators(result, symbol, historical_bars, benchmark_bars)

        except Exception as e:
            logger.error(f"Error calculating indicators for {symbol}: {e}")

    def _compute_change(self, result: ScanResult, historical_bars: list | None) -> None:
        """Populate change/change_pct: live quote price vs prior close.

        Mirrors the prev-close convention used elsewhere (see
        ``_with_change`` in ``backend/api/analysis/router.py``) — compares
        against the close of the bar immediately before the most recent
        one, not that bar's own open, matching "Today's Change" semantics
        rather than an intraday open->price move. ``historical_bars`` is
        ascending (oldest -> newest, see ``_calculate_indicators``), so the
        prior close is the second-to-last entry.
        """
        if not result.quote or not historical_bars or len(historical_bars) < 2:
            return
        prev_close = historical_bars[-2].close
        if not prev_close:
            return
        result.change = result.quote.price - prev_close
        result.change_pct = (result.change / prev_close) * 100

    def _benchmark_symbols(self) -> tuple[str, ...]:
        """Return configured benchmark symbols without importing settings at startup."""
        try:
            from backend.config.settings import settings

            return tuple(symbol.upper() for symbol in settings.relative_strength.benchmark_list())
        except Exception:  # pragma: no cover - defensive configuration fallback
            return ("SPY", "QQQ")

    def _get_benchmark_bars(self, benchmark: str) -> list:
        """Load one benchmark history with a short process-local TTL."""
        now = time.monotonic()
        cached = self._benchmark_bars_cache.get(benchmark)
        if cached is not None and now - cached[0] < self._benchmark_bars_ttl:
            return cached[1]
        try:
            bars = market_data_manager.get_historical_bars(
                benchmark,
                timeframe="1d",
                range_="3mo",
                use_cache=True,
            )
            if not isinstance(bars, list):
                bars = []
        except Exception as exc:  # pragma: no cover - provider dependent
            logger.debug("Benchmark history unavailable for %s: %s", benchmark, exc)
            bars = []
        self._benchmark_bars_cache[benchmark] = (now, bars)
        return bars

    @staticmethod
    def _bar_values(bars: list) -> tuple[list[float], list[float], list[float], list[float]]:
        """Extract valid close/high/low/volume arrays from provider bars."""
        closes: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        volumes: list[float] = []
        for bar in bars:
            try:
                close = float(bar.close)
                high = float(bar.high)
                low = float(bar.low)
                volume = float(bar.volume or 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if close <= 0 or high <= 0 or low <= 0:
                continue
            closes.append(close)
            highs.append(high)
            lows.append(low)
            volumes.append(max(0.0, volume))
        return closes, highs, lows, volumes

    @staticmethod
    def _mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    @staticmethod
    def _ema(values: list[float], period: int) -> float | None:
        if len(values) < period:
            return None
        alpha = 2.0 / (period + 1)
        value = sum(values[:period]) / period
        for price in values[period:]:
            value = alpha * price + (1 - alpha) * value
        return value

    @staticmethod
    def _stddev(values: list[float]) -> float | None:
        if not values:
            return None
        mean = sum(values) / len(values)
        return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))

    def _set_unavailable_derived_indicators(self, result: ScanResult) -> None:
        """Keep the scanner response shape stable when history is unavailable."""
        for key in (
            "rsi",
            "rsi_previous",
            "rsi_delta",
            "macd",
            "adx",
            "price_change_pct",
            "volume_ratio",
            "volatility_ratio",
            "volatility_5_pct",
            "volatility_20_pct",
            "relative_strength",
            "vwap_20", "ema_9", "ema_20", "ema_50", "ema_alignment", "ema_crossover",
        ):
            result.add_indicator(key, None)
        for period in (10, 20, 50):
            result.add_indicator(f"breakout_{period}", False)
            result.add_indicator(f"breakdown_{period}", False)
            result.add_indicator(f"highest_high_{period}", None)
            result.add_indicator(f"lowest_low_{period}", None)
            result.add_indicator(f"breakout_pct_{period}", None)
            result.add_indicator(f"breakdown_pct_{period}", None)
        for period in (20, 50, 200):
            result.add_indicator(f"sma_{period}", None)
            result.add_indicator(f"price_vs_sma_{period}_pct", None)
        for benchmark in self._benchmark_symbols():
            result.add_indicator(f"rs_pct_{benchmark}", None)
            result.add_indicator(f"symbol_return_pct_{benchmark}", None)
            result.add_indicator(f"benchmark_return_pct_{benchmark}", None)
        for group in (
            "sma",
            "highest_high",
            "lowest_low",
            "rs_pct",
            "symbol_return_pct",
            "benchmark_return_pct",
        ):
            result.add_indicator(group, {})

    def _populate_derived_indicators(
        self,
        result: ScanResult,
        symbol: str,
        bars: list,
        benchmark_bars: dict[str, list] | None,
    ) -> None:
        """Populate scanner-specific windows used by the filter builder."""
        closes, highs, lows, volumes = self._bar_values(bars)
        if not closes:
            self._set_unavailable_derived_indicators(result)
            return

        price = result.indicator_values.get("price")
        if not isinstance(price, (int, float)) or price <= 0:
            price = closes[-1]
        price = float(price)

        # Moving averages are retained both as flattened keys and as a map so
        # filters can request a supported window without another data fetch.
        moving_averages: dict[str, float] = {}
        for period in (20, 50, 200):
            if len(closes) >= period:
                average = sum(closes[-period:]) / period
                moving_averages[str(period)] = average
                result.add_indicator(f"sma_{period}", average)
                result.add_indicator(f"price_vs_sma_{period}_pct", (price / average - 1.0) * 100.0)
            else:
                result.add_indicator(f"sma_{period}", None)
                result.add_indicator(f"price_vs_sma_{period}_pct", None)
        result.add_indicator("sma", moving_averages)

        for period in (9, 20, 50):
            result.add_indicator(f"ema_{period}", self._ema(closes, period))
        ema9, ema20, ema50 = (result.indicator_values.get(f"ema_{p}") for p in (9, 20, 50))
        alignment = "neutral"
        if all(isinstance(v, (int, float)) for v in (ema9, ema20, ema50)):
            alignment = "bullish" if ema9 > ema20 > ema50 else "bearish" if ema9 < ema20 < ema50 else "neutral"
        result.add_indicator("ema_alignment", alignment)
        result.add_indicator("ema_crossover", "bullish" if isinstance(ema9, (int, float)) and isinstance(ema20, (int, float)) and ema9 > ema20 else "bearish" if isinstance(ema9, (int, float)) and isinstance(ema20, (int, float)) and ema9 < ema20 else "neutral")
        vwap_bars = bars[-min(20, len(bars)):]
        notional = sum(((float(b.high) + float(b.low) + float(b.close)) / 3.0) * max(0.0, float(b.volume or 0)) for b in vwap_bars)
        total_volume = sum(max(0.0, float(b.volume or 0)) for b in vwap_bars)
        result.add_indicator("vwap_20", notional / total_volume if total_volume else None)

        # Breakout/breakdown levels exclude the current bar. This avoids
        # making every symbol with a new all-time high look like a breakout
        # merely because the current bar is included in its own reference.
        highest_high: dict[str, float] = {}
        lowest_low: dict[str, float] = {}
        for period in (10, 20, 50):
            if len(highs) < period + 1:
                result.add_indicator(f"highest_high_{period}", None)
                result.add_indicator(f"lowest_low_{period}", None)
                result.add_indicator(f"breakout_{period}", False)
                result.add_indicator(f"breakdown_{period}", False)
                continue
            prior_high = max(highs[-period - 1 : -1])
            prior_low = min(lows[-period - 1 : -1])
            highest_high[str(period)] = prior_high
            lowest_low[str(period)] = prior_low
            result.add_indicator(f"highest_high_{period}", prior_high)
            result.add_indicator(f"lowest_low_{period}", prior_low)
            result.add_indicator(f"breakout_pct_{period}", (price / prior_high - 1.0) * 100.0)
            result.add_indicator(f"breakdown_pct_{period}", (price / prior_low - 1.0) * 100.0)
            result.add_indicator(f"breakout_{period}", price > prior_high)
            result.add_indicator(f"breakdown_{period}", price < prior_low)
        result.add_indicator("highest_high", highest_high)
        result.add_indicator("lowest_low", lowest_low)

        # Relative volume compares against the previous 20 bars, excluding
        # the current bar so an expansion can actually be detected.
        if len(volumes) >= 2:
            average_volume = self._mean(volumes[-21:-1])
            current_volume = volumes[-1]
            result.add_indicator("volume_avg_20", average_volume)
            result.add_indicator(
                "volume_ratio",
                current_volume / average_volume if average_volume and average_volume > 0 else None,
            )
        else:
            result.add_indicator("volume_avg_20", None)
            result.add_indicator("volume_ratio", None)

        # Realized volatility uses close-to-close returns. The ratio is the
        # central squeeze/expansion metric: <1 means contracting, >1 means
        # expanding relative to the 20-bar baseline.
        returns = [
            ((closes[i] / closes[i - 1]) - 1.0) * 100.0
            for i in range(1, len(closes))
            if closes[i - 1] > 0
        ]
        vol5 = self._stddev(returns[-5:]) if len(returns) >= 5 else None
        vol20 = self._stddev(returns[-20:]) if len(returns) >= 20 else None
        result.add_indicator("volatility_5_pct", vol5)
        result.add_indicator("volatility_20_pct", vol20)
        result.add_indicator(
            "volatility_ratio", vol5 / vol20 if vol5 is not None and vol20 and vol20 > 0 else None
        )

        # Price confirmation for an oversold reversal and the previous RSI
        # are computed alongside the existing RSI series below.
        if len(closes) >= 2 and closes[-2] > 0:
            result.add_indicator("price_change_pct", (price / closes[-2] - 1.0) * 100.0)
        else:
            result.add_indicator("price_change_pct", None)

        # Benchmark-relative return in percentage points. The configured
        # benchmarks are fetched once per process TTL and can be injected by
        # batch callers to avoid any per-symbol requests.
        benchmark_bars = benchmark_bars or {}
        primary_rs: float | None = None
        try:
            from backend.config.settings import settings

            lookback = int(settings.relative_strength.lookback_days)
        except Exception:  # pragma: no cover - defensive fallback
            lookback = 20
        for benchmark in self._benchmark_symbols():
            bench = benchmark_bars.get(benchmark)
            if not bench:
                bench = (
                    bars
                    if benchmark.upper() == symbol.upper()
                    else self._get_benchmark_bars(benchmark)
                )
            benchmark_closes, _, _, _ = self._bar_values(bench or [])
            if len(closes) >= lookback + 1 and len(benchmark_closes) >= lookback + 1:
                symbol_return = (closes[-1] / closes[-lookback - 1] - 1.0) * 100.0
                benchmark_return = (
                    benchmark_closes[-1] / benchmark_closes[-lookback - 1] - 1.0
                ) * 100.0
                rs_pct = symbol_return - benchmark_return
                if primary_rs is None:
                    primary_rs = rs_pct
                result.add_indicator(f"rs_pct_{benchmark}", rs_pct)
                result.add_indicator(f"symbol_return_pct_{benchmark}", symbol_return)
                result.add_indicator(f"benchmark_return_pct_{benchmark}", benchmark_return)
            else:
                result.add_indicator(f"rs_pct_{benchmark}", None)
                result.add_indicator(f"symbol_return_pct_{benchmark}", None)
                result.add_indicator(f"benchmark_return_pct_{benchmark}", None)
        result.add_indicator("relative_strength", primary_rs)

    def _populate_windowed_indicators(
        self,
        result: ScanResult,
        symbol: str,
        historical_bars: list | None = None,
        benchmark_bars: dict[str, list] | None = None,
    ):
        """Compute RSI / MACD / ADX from a bar history window.

        If historical_bars is provided, use it. Otherwise, fetch via the market data manager.
        On any failure (no history, import error, insufficient bars), the indicator is recorded as
        ``None`` so the existing "missing data" branch in
        ``_calculate_scores`` continues to work unchanged.
        """
        bars: list = []
        if historical_bars is not None:
            bars = historical_bars
        else:
            try:
                from backend.database import SessionLocal

                with SessionLocal() as db:
                    bars = market_data_manager.get_historical_bars(
                        symbol,
                        timeframe="1d",
                        range_="3mo",
                        use_cache=True,
                        db=db,
                    )
            except Exception as e:
                logger.warning(f"DB-backed history lookup failed for {symbol}: {e}")
                # Fall back to a non-cached provider call.
                try:
                    bars = market_data_manager.get_historical_bars(
                        symbol,
                        timeframe="1d",
                        range_="3mo",
                        use_cache=False,
                    )
                except Exception as e2:
                    logger.warning(f"Provider history lookup failed for {symbol}: {e2}")
                    bars = []

        if not bars:
            self._set_unavailable_derived_indicators(result)
            return

        self._populate_derived_indicators(result, symbol, bars, benchmark_bars)

        # Convert to the {"close", "high", "low"} dict shape that
        # BaseIndicator.calculate() expects.
        bar_dicts = [{"close": b.close, "high": b.high, "low": b.low} for b in bars]

        try:
            from backend.indicators import (
                ADXIndicator,
                MACDIndicator,
                RSIIndicator,
            )

            rsi_series = RSIIndicator(period=14).calculate(bar_dicts)
            result.add_indicator("rsi", rsi_series[-1] if rsi_series else None)
            result.add_indicator("rsi_previous", rsi_series[-2] if len(rsi_series) > 1 else None)
            if len(rsi_series) > 1:
                result.add_indicator("rsi_delta", rsi_series[-1] - rsi_series[-2])
            else:
                result.add_indicator("rsi_delta", None)

            macd_series = MACDIndicator().calculate(bar_dicts)
            # MACD returns the histogram; the "current value" consumers
            # care about is the most recent histogram bar.
            result.add_indicator("macd", macd_series[-1] if macd_series else None)

            adx_series = ADXIndicator(period=14).calculate(bar_dicts)
            result.add_indicator("adx", adx_series[-1] if adx_series else None)
        except Exception as e:
            logger.error(f"Indicator calculation failed for {symbol}: {e}")
            result.add_indicator("rsi", None)
            result.add_indicator("macd", None)
            result.add_indicator("adx", None)

    def _calculate_scores(self, result: ScanResult):
        """Calculate various scores for the symbol.

        Indicators that are not yet computable (RSI/MACD/ADX) are reported
        as None by `_calculate_indicators`. We skip those scores here so
        the aggregate does not silently use a default of 0/50 and create
        a misleadingly high or neutral ranking.

        Scores carry signs to preserve direction:
          - Positive: bullish (rising momentum, oversold bounce, strong uptrend)
          - Negative: bearish (falling momentum, overbought decline, strong downtrend)
          - Zero / near-zero: neutral or indeterminate
        The sum (via ``calculate_signed_total_score``) is the ranking signal.
        """
        try:
            # Trend strength score (based on ADX — magnitude only; direction
            # comes from the trend_signals dict which the ranking engine reads
            # separately via _total_trend_confidence / _total_bearish_confidence).
            adx = result.indicator_values.get("adx")
            if adx is not None:
                # Normalize ADX: 0-25 = weak, 25-50 = moderate, 50-75 = strong, 75+ = very strong
                if adx >= 75:
                    trend_score = 90 + (adx - 75) * 0.4  # 90-130, clamped to 100
                elif adx >= 50:
                    trend_score = 60 + (adx - 50) * 1.2  # 60-90
                elif adx >= 25:
                    trend_score = 30 + (adx - 25) * 1.2  # 30-60
                else:
                    trend_score = adx * 1.2  # 0-30
                result.add_score("trend_strength", trend_score)
                result.add_score("adx", adx)
            # else: skip — score will be missing rather than zero

            # Momentum score (based on MACD). Positive MACD = bullish momentum;
            # negative MACD = bearish momentum. The raw histogram is in
            # price units (an EMA difference), so a fixed /2.0 scale meant
            # for a ~[-100, 100] histogram left momentum near-zero for any
            # normally-priced stock (e.g. a $500 stock's histogram sits
            # around single digits, not hundreds) — total_score, and the
            # Confidence % derived from it, ended up pinned near its floor
            # for virtually every symbol. Express MACD as a % of price
            # first so the score scales the same way regardless of the
            # symbol's price level, then apply the same ATR-style ×20
            # (see the volatility score below) and clamp to [-50, 50].
            macd = result.indicator_values.get("macd")
            close_price = result.indicator_values.get("close") or 0
            if macd is not None and close_price:
                macd_pct = (macd / close_price) * 100
                momentum_score = max(-50.0, min(50.0, macd_pct * 20))
                result.add_score("momentum", momentum_score)
                # Raw histogram, kept only for the score-breakdown display —
                # deliberately excluded from calculate_signed_total_score's
                # default weights since it's the same unnormalized signal
                # momentum already represents on a comparable scale.
                result.add_score("macd", macd)

            # Volatility score: magnitude only (0-100), no direction signal.
            atr = result.indicator_values.get("atr", 0) or 0
            close_price = result.indicator_values.get("close", 1) or 1
            atr_pct = (atr / close_price) * 100 if close_price else 0
            volatility_score = min(100, atr_pct * 20)
            result.add_score("volatility", volatility_score)

            # Volume score (based on volume relative to average). Magnitude only.
            volume = result.indicator_values.get("volume", 0) or 0
            if volume > 0:
                volume_score = min(100, (volume / 1000000) * 10)  # Rough normalization
            else:
                volume_score = 0
            result.add_score("volume", volume_score)

            # RSI score: oversold (< 30) = positive (bullish bounce potential);
            # overbought (> 70) = negative (bearish reversal risk); neutral zone = 0.
            # Scale: |rsi - 50| * 2 maps 30 → 40 (oversold) and 70 → -40 (overbought).
            rsi = result.indicator_values.get("rsi")
            if rsi is not None:
                if rsi < 30:
                    rsi_score = (30 - rsi) * 2  # 0..40, positive (bullish)
                elif rsi > 70:
                    rsi_score = (70 - rsi) * 2  # negative (bearish): e.g. 80 → -20
                else:
                    rsi_score = 0  # neutral zone
                result.add_score("rsi", rsi_score)

        except Exception as e:
            logger.error(f"Error calculating scores for {result.symbol}: {e}")

    def _generate_signals(self, result: ScanResult):
        """Generate trading signals based on indicator values"""
        try:
            signals = []

            # RSI signals
            rsi_value = result.indicator_values.get("rsi")
            if rsi_value is not None:
                if rsi_value < 30:
                    signals.append("RSI_OVERSOLD")
                elif rsi_value > 70:
                    signals.append("RSI_OVERBOUGHT")
            # A reversal requires the prior RSI to have been oversold, the
            # current RSI to be rising, and price to have turned higher. It
            # is intentionally separate from RSI_OVERSOLD so scans can
            # distinguish a falling knife from a confirmed bounce attempt.
            rsi_previous = result.indicator_values.get("rsi_previous")
            rsi_delta = result.indicator_values.get("rsi_delta")
            price_change_pct = result.indicator_values.get("price_change_pct")
            if (
                isinstance(rsi_previous, (int, float))
                and isinstance(rsi_value, (int, float))
                and isinstance(rsi_delta, (int, float))
                and isinstance(price_change_pct, (int, float))
                and rsi_previous <= 35
                and rsi_delta >= 2
                and price_change_pct > 0
            ):
                signals.append("RSI_OVERSOLD_REVERSAL")

            # MACD signals
            macd_value = result.indicator_values.get("macd")
            if macd_value is not None:
                if macd_value > 0:
                    signals.append("MACD_BULLISH")
                else:
                    signals.append("MACD_BEARISH")

            # Trend signals from multiple timeframes
            bullish_count = 0
            bearish_count = 0
            for _tf_str, signal_data in result.trend_signals.items():
                direction = signal_data.get("direction")
                confidence = signal_data.get("confidence", 0)
                if direction == "uptrend" and confidence > 0.6:
                    bullish_count += 1
                elif direction == "downtrend" and confidence > 0.6:
                    bearish_count += 1

            if bullish_count >= 3:
                signals.append("MULTI_TIMEFRAME_BULLISH")
            elif bearish_count >= 3:
                signals.append("MULTI_TIMEFRAME_BEARISH")

            # Volume spike signal
            volume = result.indicator_values.get("volume", 0)
            volume_ratio = result.indicator_values.get("volume_ratio")
            if isinstance(volume_ratio, (int, float)) and volume_ratio >= 2.0:
                signals.append("VOLUME_SPIKE")
            # Keep the original absolute-volume signal for compatibility with
            # existing alerts and clients that do not have bar history.
            if volume > 1000000:  # Arbitrary threshold
                signals.append("HIGH_VOLUME")

            # Price-pattern and volatility state signals are derived from the
            # same daily bar window used by the scanner filters.
            if result.indicator_values.get("breakout_20") is True:
                signals.append("BREAKOUT")
            if result.indicator_values.get("breakdown_20") is True:
                signals.append("BREAKDOWN")
            volatility_ratio = result.indicator_values.get("volatility_ratio")
            if isinstance(volatility_ratio, (int, float)):
                if volatility_ratio <= 0.75:
                    signals.append("VOLATILITY_CONTRACTION")
                elif volatility_ratio >= 1.25:
                    signals.append("VOLATILITY_EXPANSION")

            # Expose a simple primary-benchmark signal for clients that want
            # a readable label instead of interpreting rs_pct_* themselves.
            relative_strength = result.indicator_values.get("relative_strength")
            if isinstance(relative_strength, (int, float)):
                if relative_strength >= 1.0:
                    signals.append("RELATIVE_STRENGTH_OUTPERFORMER")
                elif relative_strength <= -1.0:
                    signals.append("RELATIVE_STRENGTH_UNDERPERFORMER")

            if result.indicator_values.get("ema_alignment") == "bullish":
                signals.append("EMA_BULLISH_ALIGNMENT")
            elif result.indicator_values.get("ema_alignment") == "bearish":
                signals.append("EMA_BEARISH_ALIGNMENT")
            if result.indicator_values.get("ema_crossover") == "bullish":
                signals.append("EMA_BULLISH_CROSSOVER")
            elif result.indicator_values.get("ema_crossover") == "bearish":
                signals.append("EMA_BEARISH_CROSSOVER")
            vwap = result.indicator_values.get("vwap_20")
            current_price = result.indicator_values.get("price")
            if isinstance(current_price, (int, float)) and isinstance(vwap, (int, float)):
                signals.append("ABOVE_VWAP" if current_price >= vwap else "BELOW_VWAP")

            # Tape (Time & Sales) order-flow signals — only when the tape
            # subsystem is enabled and streaming (best-effort; a cold
            # engine just returns neutral / zero counts).
            # Tape snapshot is cached per symbol for 10s — get_snapshot()
            # drains the repository on every call so repeated fetches waste
            # resources and can starve the live stream.
            from backend.config.settings import settings as _settings

            if _settings.tape.enabled:
                try:
                    import time as _time

                    from backend.api.tape.registry import get_tape_engine

                    now = _time.monotonic()
                    cached = self._tape_cache.get(result.symbol)
                    if cached is not None and (now - cached[0]) < self._tape_cache_ttl:
                        snap = cached[1]
                    else:
                        snap = get_tape_engine(result.symbol, seed=False).get_snapshot()
                        self._tape_cache[result.symbol] = (now, snap)
                    # Persist live metrics on the scan result so the
                    # composable filter endpoint can evaluate the same shared
                    # microstructure snapshot without additional stream work.
                    result.indicator_values.update({
                        "tape_pressure": snap.get("pressure", "neutral"),
                        "tape_block_count": snap.get("block_count_5m", 0),
                        "tape_acceleration": snap.get("tape_accel"),
                        "tape_trade_velocity": snap.get("trade_velocity"),
                        "tape_volume_acceleration": snap.get("volume_accel"),
                        "tape_recent_buy_ratio": snap.get("recent_buy_ratio"),
                    })
                    if snap.get("pressure") == "heavy_buy":
                        signals.append("HEAVY_BUY_PRESSURE")
                    elif snap.get("pressure") == "heavy_sell":
                        signals.append("HEAVY_SELL_PRESSURE")
                    if snap.get("block_count_5m", 0) > 0:
                        signals.append("BLOCK_ACTIVITY")
                    if isinstance(snap.get("tape_accel"), (int, float)) and snap["tape_accel"] >= 1.5:
                        signals.append("TRADE_RATE_SPIKE")
                    if isinstance(snap.get("volume_accel"), (int, float)) and snap["volume_accel"] >= 1.5:
                        signals.append("LIVE_VOLUME_ACCELERATION")
                except Exception:  # noqa: BLE001
                    pass

            # BBO metrics are supplied by the shared live quote cache.  A
            # missing or stale BBO simply leaves these absent, making the
            # corresponding filters safely not match rather than guessing.
            try:
                from backend.market_data.streaming.live_quotes import live_quote_cache

                live_quote = live_quote_cache.get(result.symbol)
                if live_quote is not None:
                    for key in ("spread_bps", "spread_change_bps"):
                        value = live_quote.get(key)
                        if isinstance(value, (int, float)):
                            result.indicator_values[key] = value
                    bid_size, ask_size = live_quote.get("bid_size"), live_quote.get("ask_size")
                    if (
                        isinstance(bid_size, (int, float))
                        and isinstance(ask_size, (int, float))
                        and bid_size + ask_size > 0
                    ):
                        result.indicator_values["bid_ask_imbalance"] = round(
                            (bid_size - ask_size) / (bid_size + ask_size), 3
                        )
            except Exception:  # noqa: BLE001
                pass

            result.signals = signals

        except Exception as e:
            logger.error(f"Error generating signals for {result.symbol}: {e}")

    async def scan_symbols(self, symbols: list[str]) -> list[ScanResult]:
        """Scan multiple symbols and return results"""
        # Fetch historical bars and quotes for all symbols in batch to eliminate N+1 query problem
        from backend.database import SessionLocal

        batch_bars = {}
        batch_quotes = {}
        benchmark_symbols = self._benchmark_symbols()
        symbols_with_benchmarks = list(dict.fromkeys([*symbols, *benchmark_symbols]))
        try:
            with SessionLocal() as db:
                batch_bars = await market_data_manager.get_batch_historical_bars(
                    symbols_with_benchmarks,
                    timeframe="1d",
                    range_="3mo",
                    use_cache=True,
                    db=db,
                )
                # to_thread: get_batch_quotes is synchronous (provider
                # batch call + serial per-symbol fallback on partial
                # failure) — called directly it would block this
                # coroutine's event loop, same class of bug
                # scan_symbols_async's _prefetch() docstring already
                # documents ("a blocking call here previously froze every
                # other in-flight request on the server, not just this
                # one").
                batch_quotes = await asyncio.to_thread(
                    market_data_manager.get_batch_quotes, symbols
                )
        except Exception as e:
            logger.warning(f"Batch lookup failed: {e}")
            # Fall back to individual calls if batch fails
            batch_bars = {}
            batch_quotes = {}

        results = []
        benchmark_bars = {
            benchmark: batch_bars.get(benchmark, []) for benchmark in benchmark_symbols
        }
        for symbol in symbols:
            # Pass the pre-fetched bars and quote to avoid individual database/provider calls
            scan_kwargs = {"benchmark_bars": benchmark_bars} if any(benchmark_bars.values()) else {}
            result = self.scan_symbol(
                symbol,
                historical_bars=batch_bars.get(symbol),
                quote=batch_quotes.get(symbol),
                **scan_kwargs,
            )
            results.append(result)

        self._notify_alerts(results)
        self.last_scan_time = datetime.now()
        return results

    async def scan_symbols_async(
        self, symbols: list[str], max_concurrent: int = 8
    ) -> list[ScanResult]:
        """Scan multiple symbols concurrently via asyncio.to_thread + asyncio.gather.

        Each :meth:`scan_symbol` is a blocking call (HTTP, DB read) so it is run on
        a worker thread. With ``max_workers`` capped by the default executor
        (5*cpu_count on Python 3.12+), this turns N serial HTTP round-trips into
        roughly ``ceil(N / workers)`` round-trips of wall time. Useful for batch
        scan endpoints that are already ``async def``.

        ``max_concurrent`` caps the number of symbols scanned in parallel
        (default 8) — without it, scanning 50+ symbols spawns 50 threads
        simultaneously, which can overwhelm provider rate limits and the DB
        connection pool. The semaphore bounds in-flight ``asyncio.to_thread``
        calls regardless of the executor's max_workers.

        Bars and quotes are batch pre-fetched before spawning threads — the same
        pattern used by :meth:`scan_symbols` — so each thread
        receives its data already in memory instead of opening its own DB session
        and provider call. This converts N×DB-session + N×provider-call into a
        single batch DB query + single batch provider call.

        Both :meth:`scan_symbols` and this method are async; sync callers
        bridge through ``backend.ai.sync_bridge.run_sync``.
        """
        if not symbols:
            return []
        start = time.monotonic()

        # Batch pre-fetch bars and quotes. On a Redis cache hit this is
        # cheap, but on a miss both calls fall through to blocking provider
        # HTTP requests (yfinance/webull/alpaca) — and Alpaca in particular
        # can take 15-20s to time out. Run on a worker thread so a slow
        # provider call stalls this scan, not the entire event loop (a
        # blocking call here previously froze every other in-flight request
        # on the server, not just this one).
        #
        # No ``db`` session is passed to ``get_batch_historical_bars``: that
        # parameter only enables a secondary DB-backed cache read *below*
        # Redis, but the caller checks it out for the entire call —
        # including every slow provider fallback inside it — so one cold
        # scan could hold a pooled connection hostage for a minute while the
        # background ingestion pipeline (which needs the same pool) backed
        # up behind it, stalling unrelated requests server-wide. Redis is
        # enabled and is the primary cache here, so skipping the DB tier
        # only matters on a Redis miss, and any bars fetched still get
        # written back to Redis for next time.
        benchmark_symbols = self._benchmark_symbols()
        symbols_with_benchmarks = list(dict.fromkeys([*symbols, *benchmark_symbols]))

        def _prefetch() -> tuple[dict, dict, dict[str, list]]:
            # Lazy import: backend.ai.__init__ pulls in analyze → context,
            # which imports this module back — a top-level import here would
            # be circular when scanner is the import entry point.
            from backend.ai.sync_bridge import run_sync

            # Runs on a worker thread (asyncio.to_thread below), so no
            # event loop here — bridge the async batch-bars call.
            bars = run_sync(
                market_data_manager.get_batch_historical_bars(
                    symbols_with_benchmarks,
                    timeframe="1d",
                    range_="3mo",
                    use_cache=True,
                )
            )
            quotes = market_data_manager.get_batch_quotes(symbols)
            benchmarks = {benchmark: bars.get(benchmark, []) for benchmark in benchmark_symbols}
            return bars, quotes, benchmarks

        batch_bars: dict = {}
        batch_quotes: dict = {}
        benchmark_bars: dict[str, list] = {}
        try:
            batch_bars, batch_quotes, benchmark_bars = await asyncio.to_thread(_prefetch)
        except Exception as e:
            logger.warning(f"Async scan batch pre-fetch failed, falling back to per-symbol: {e}")

        # Semaphore caps concurrent in-flight scans regardless of the
        # default executor's worker count — prevents overwhelming providers
        # and the DB pool when scanning a large watchlist.
        sem = asyncio.Semaphore(max_concurrent)
        try:
            accepts_benchmark_bars = (
                "benchmark_bars" in inspect.signature(self.scan_symbol).parameters
            )
        except (TypeError, ValueError):
            accepts_benchmark_bars = True

        async def _throttled_scan(symbol: str) -> ScanResult:
            async with sem:
                scan_kwargs = {}
                # Keep compatibility with lightweight test/consumer scanner
                # doubles that implement the historical three-argument
                # signature. If no benchmark data was prefetched, the real
                # scanner will lazily use its own short-lived cache.
                if accepts_benchmark_bars and any(benchmark_bars.values()):
                    scan_kwargs["benchmark_bars"] = benchmark_bars
                return await asyncio.to_thread(
                    self.scan_symbol,
                    symbol,
                    batch_bars.get(symbol),
                    batch_quotes.get(symbol),
                    **scan_kwargs,
                )

        tasks = [_throttled_scan(s) for s in symbols]
        results = await asyncio.gather(*tasks)
        self._notify_alerts(results)
        duration_ms = (time.monotonic() - start) * 1000
        record_scan(duration_ms)
        self.last_scan_time = datetime.now()
        return list(results)

    @staticmethod
    def _notify_alerts(results: list[ScanResult]) -> None:
        """Send completed batch scans to the alert engine.

        Kept as a lazy import so the scanner remains usable in isolation and
        avoids importing the alert/database stack during module initialization.
        The single-symbol API path performs the same notification explicitly.
        """
        try:
            from backend.alerts.engine import alerts_engine

            for result in results:
                alerts_engine.evaluate_scan_result(result)
        except Exception:
            logger.debug("Alert evaluation unavailable for batch scan", exc_info=True)

    def rank_symbols(self, symbols: list[str] | None = None) -> list[tuple[str, float]]:
        """Rank symbols by their total score"""
        if symbols is None:
            symbols = list(self.scan_results.keys())

        # Scan any symbols we haven't scanned yet
        for symbol in symbols:
            if symbol not in self.scan_results:
                self.scan_symbol(symbol)

        # Calculate total scores and rank
        ranked = []
        for symbol in symbols:
            result = self.scan_results.get(symbol)
            if result:
                total_score = result.calculate_total_score(self.score_weights)
                ranked.append((symbol, total_score))

        # Sort by score descending
        ranked.sort(key=lambda x: x[1], reverse=True)

        # Assign ranks
        for i, (symbol, _score) in enumerate(ranked):
            if symbol in self.scan_results:
                self.scan_results[symbol].rank = i + 1

        self.rankings = ranked
        return ranked

    def get_top_symbols(self, count: int = 10) -> list[tuple[str, float, int | None]]:
        """Get top N symbols by rank"""
        if not self.rankings:
            self.rank_symbols()

        top_symbols = []
        for symbol, score in self.rankings[:count]:
            result = self.scan_results.get(symbol)
            rank = result.rank if result else None
            top_symbols.append((symbol, score, rank))

        return top_symbols

    def get_scan_result(self, symbol: str) -> ScanResult | None:
        """Get scan result for a symbol"""
        return self.scan_results.get(symbol)

    def get_signals_for_symbol(self, symbol: str) -> list[str]:
        """Get trading signals for a symbol"""
        result = self.scan_results.get(symbol)
        return result.signals if result else []


# Global scanner instance
market_scanner = Scanner()
