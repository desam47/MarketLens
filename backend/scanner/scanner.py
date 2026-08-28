"""
Market scanner and ranking system
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from ..market_data.services.manager import market_data_manager
from ..models.market_data import Quote
from ..observability import record_scan
from ..trend.trend_engine import TrendEngine

logger = logging.getLogger(__name__)

class ScanResult:
    """Result of scanning a single symbol"""

    def __init__(self, symbol: str, timestamp: datetime):
        self.symbol = symbol
        self.timestamp = timestamp
        self.quote: Quote | None = None
        self.trend_signals: dict[str, Any] = {}
        self.indicator_values: dict[str, Any] = {}
        self.scores: dict[str, float] = {}
        self.rank: int | None = None
        self.signals: list[str] = []

    def add_indicator(self, name: str, value: Any):
        """Add an indicator value"""
        self.indicator_values[name] = value

    def add_trend_signal(self, timeframe: str, signal: Any):
        """Add a trend signal"""
        self.trend_signals[timeframe] = signal

    def add_score(self, name: str, score: float):
        """Add a score (0-100)"""
        self.scores[name] = max(0, min(100, score))  # Clamp to 0-100

    def add_signal(self, signal: str):
        """Add a trading signal"""
        self.signals.append(signal)

    def calculate_total_score(self, weights: dict[str, float] | None = None) -> float:
        """Calculate weighted total score"""
        if not self.scores:
            return 0.0

        if weights is None:
            # Equal weighting if no weights provided
            weights = {name: 1.0 for name in self.scores.keys()}

        total_weight = sum(weights.get(name, 0) for name in self.scores.keys())
        if total_weight == 0:
            return 0.0

        weighted_sum = sum(
            self.scores.get(name, 0) * weights.get(name, 0)
            for name in self.scores.keys()
        )

        return weighted_sum / total_weight

class Scanner:
    """Market scanner that evaluates and ranks symbols"""

    def __init__(self):
        self.scan_results: dict[str, ScanResult] = {}
        self.rankings: list[tuple[str, float]] = []  # (symbol, score)
        self.last_scan_time: datetime | None = None

        # Scoring weights for different factors
        self.score_weights = {
            "trend_strength": 0.25,
            "momentum": 0.20,
            "volatility": 0.15,
            "volume": 0.15,
            "rsi": 0.10,
            "macd": 0.10,
            "adx": 0.05
        }

    def scan_symbol(self, symbol: str) -> ScanResult:
        """Scan a single symbol and return results"""
        result = ScanResult(symbol, datetime.now())

        try:
            # Get current quote
            quote = market_data_manager.get_quote(symbol)
            result.quote = quote

            # Get trend engine for this symbol
            trend_engine = TrendEngine(symbol)

            # Update trend engine with recent data (we'd need historical data in practice)
            # For now, we'll use the quote to update
            if quote:
                trend_engine.update(
                    quote.price,
                    quote.volume or 0,
                    quote.timestamp,
                    quote.provider
                )

                # Get trend signals for multiple timeframes
                timeframes = ["ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE",
                             "ONE_HOUR", "FOUR_HOUR", "ONE_DAY"]
                for tf_str in timeframes:
                    # Get the Timeframe enum
                    tf_module = __import__('backend.engines.timeframe', fromlist=['Timeframe'])
                    tf = getattr(tf_module.Timeframe, tf_str)
                    trend_signal = trend_engine.get_current_trend(tf)
                    if trend_signal:
                        result.add_trend_signal(tf_str, {
                            "direction": trend_signal.direction.value,
                            "strength": trend_signal.strength.value,
                            "confidence": trend_signal.confidence
                        })

            # Calculate technical indicators
            self._calculate_indicators(result, symbol)

            # Calculate scores
            self._calculate_scores(result)

            # Generate trading signals
            self._generate_signals(result)

        except Exception as e:
            logger.error(f"Error scanning symbol {symbol}: {e}")
            # Still return a result, but it may be incomplete

        self.scan_results[symbol] = result
        return result

    def _calculate_indicators(self, result: ScanResult, symbol: str):
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
                # windowed indicators. The manager persists the result
                # for the next call when a DB session is available.
                self._populate_windowed_indicators(result, symbol)

        except Exception as e:
            logger.error(f"Error calculating indicators for {symbol}: {e}")

    def _populate_windowed_indicators(self, result: ScanResult, symbol: str):
        """Compute RSI / MACD / ADX from a bar history window.

        Fetches a 3-month daily history via the market data manager and
        runs the corresponding indicators. On any failure (no history,
        import error, insufficient bars), the indicator is recorded as
        ``None`` so the existing "missing data" branch in
        ``_calculate_scores`` continues to work unchanged.
        """
        bars: list = []
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
            logger.warning(
                f"DB-backed history lookup failed for {symbol}: {e}"
            )
            # Fall back to a non-cached provider call.
            try:
                bars = market_data_manager.get_historical_bars(
                    symbol,
                    timeframe="1d",
                    range_="3mo",
                    use_cache=False,
                )
            except Exception as e2:
                logger.warning(
                    f"Provider history lookup failed for {symbol}: {e2}"
                )
                bars = []

        if not bars:
            result.add_indicator("rsi", None)
            result.add_indicator("macd", None)
            result.add_indicator("adx", None)
            return

        # Convert to the {"close", "high", "low"} dict shape that
        # BaseIndicator.calculate() expects.
        bar_dicts = [
            {"close": b.close, "high": b.high, "low": b.low}
            for b in bars
        ]

        try:
            from backend.indicators import (
                ADXIndicator,
                MACDIndicator,
                RSIIndicator,
            )

            rsi_series = RSIIndicator(period=14).calculate(bar_dicts)
            result.add_indicator("rsi", rsi_series[-1] if rsi_series else None)

            macd_series = MACDIndicator().calculate(bar_dicts)
            # MACD returns the histogram; the "current value" consumers
            # care about is the most recent histogram bar.
            result.add_indicator(
                "macd", macd_series[-1] if macd_series else None
            )

            adx_series = ADXIndicator(period=14).calculate(bar_dicts)
            result.add_indicator("adx", adx_series[-1] if adx_series else None)
        except Exception as e:
            logger.error(
                f"Indicator calculation failed for {symbol}: {e}"
            )
            result.add_indicator("rsi", None)
            result.add_indicator("macd", None)
            result.add_indicator("adx", None)

    def _calculate_scores(self, result: ScanResult):
        """Calculate various scores for the symbol.

        Indicators that are not yet computable (RSI/MACD/ADX) are reported
        as None by `_calculate_indicators`. We skip those scores here so
        the aggregate does not silently use a default of 0/50 and create
        a misleadingly high or neutral ranking.
        """
        try:
            # Trend strength score (based on ADX and trend confidence)
            adx = result.indicator_values.get("adx")
            if adx is not None:
                # Normalize ADX: 0-25 = weak, 25-50 = moderate, 50-75 = strong, 75+ = very strong
                if adx >= 75:
                    trend_score = 90 + (adx - 75) * 0.4  # 90-130, clamped to 100
                elif adx >= 50:
                    trend_score = 60 + (adx - 50) * 1.2   # 60-90
                elif adx >= 25:
                    trend_score = 30 + (adx - 25) * 1.2   # 30-60
                else:
                    trend_score = adx * 1.2               # 0-30
                result.add_score("trend_strength", max(0, min(100, trend_score)))
                result.add_score("adx", max(0, min(100, adx)))
            # else: skip — score will be missing rather than zero

            # Momentum score (based on MACD)
            macd = result.indicator_values.get("macd")
            if macd is not None:
                momentum_score = ((macd + 100) / 200) * 100
                result.add_score("momentum", momentum_score)
                result.add_score("macd", abs(macd))

            # Volatility score based on the most recent bar's true range,
            # normalized to 0–100.
            atr = result.indicator_values.get("atr", 0) or 0
            close_price = result.indicator_values.get("close", 1) or 1
            atr_pct = (atr / close_price) * 100 if close_price else 0
            volatility_score = max(0, min(100, atr_pct * 20))
            result.add_score("volatility", volatility_score)

            # Volume score (based on volume relative to average)
            volume = result.indicator_values.get("volume", 0) or 0
            if volume > 0:
                volume_score = min(100, (volume / 1000000) * 10)  # Rough normalization
            else:
                volume_score = 0
            result.add_score("volume", volume_score)

            # RSI score: deviation from neutral 50, only when RSI was computed.
            rsi = result.indicator_values.get("rsi")
            if rsi is not None:
                rsi_deviation = abs(rsi - 50)
                rsi_score = min(100, rsi_deviation * 2)
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
            # This would need volume history to be meaningful
            # For now, just a placeholder
            if volume > 1000000:  # Arbitrary threshold
                signals.append("HIGH_VOLUME")

            result.signals = signals

        except Exception as e:
            logger.error(f"Error generating signals for {result.symbol}: {e}")

    def scan_symbols(self, symbols: list[str]) -> list[ScanResult]:
        """Scan multiple symbols and return results"""
        results = []
        for symbol in symbols:
            result = self.scan_symbol(symbol)
            results.append(result)

        self.last_scan_time = datetime.now()
        return results

    async def scan_symbols_async(self, symbols: list[str]) -> list[ScanResult]:
        """Scan multiple symbols concurrently via asyncio.to_thread + asyncio.gather.

        Each :meth:`scan_symbol` is a blocking call (HTTP, DB read) so it is run on
        a worker thread. With ``max_workers`` capped by the default executor
        (5*cpu_count on Python 3.12+), this turns N serial HTTP round-trips into
        roughly ``ceil(N / workers)`` round-trips of wall time. Useful for batch
        scan endpoints that are already ``async def``.

        The synchronous :meth:`scan_symbols` is preserved for any caller that
        is not in an event loop.
        """
        if not symbols:
            return []
        start = time.monotonic()
        # asyncio.gather accepts any awaitables; to_thread gives us one per symbol.
        tasks = [asyncio.to_thread(self.scan_symbol, symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks)
        duration_ms = (time.monotonic() - start) * 1000
        record_scan(duration_ms)
        self.last_scan_time = datetime.now()
        return list(results)

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

    def get_top_symbols(self, count: int = 10) -> list[tuple[str, float, int]]:
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
