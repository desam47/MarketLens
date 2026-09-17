"""
Market scanner and ranking system
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from ..api.trend.registry import get_engine as get_trend_engine
from ..engines.timeframe import Timeframe
from ..market_data.services.manager import market_data_manager
from ..models.market_data import Quote
from ..observability import record_scan

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
            self.scores.get(name, 0) * weights.get(name, 0)
            for name in self.scores.keys()
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
        return sum(
            self.scores.get(name, 0) * weights.get(name, 0)
            for name in self.scores.keys()
        ) / total_weight

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

    def scan_symbol(self, symbol: str, historical_bars: list | None = None, quote: Quote | None = None) -> ScanResult:
        """Scan a single symbol and return results"""
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
                trend_engine.update(
                    quote.price,
                    quote.volume or 0,
                    quote.timestamp,
                    quote.provider
                )

                # Get trend signals for multiple timeframes. Phase 3.9.7:
                # hoist the import out of the loop so we don't pay the
                # __import__ cost on every symbol × timeframe.
                timeframes = ["ONE_MINUTE", "FIVE_MINUTE", "FIFTEEN_MINUTE",
                             "ONE_HOUR", "FOUR_HOUR", "ONE_DAY"]
                for tf_str in timeframes:
                    tf = getattr(Timeframe, tf_str)
                    trend_signal = trend_engine.get_current_trend(tf)
                    if trend_signal:
                        result.add_trend_signal(tf_str, {
                            "direction": trend_signal.direction.value,
                            "strength": trend_signal.strength.value,
                            "confidence": trend_signal.confidence
                        })

            # Calculate technical indicators
            self._calculate_indicators(result, symbol, historical_bars)
            self._compute_change(result, historical_bars)

            # Calculate scores
            self._calculate_scores(result)

            # Generate trading signals
            self._generate_signals(result)

        except Exception as e:
            logger.error(f"Error scanning symbol {symbol}: {e}")
            # Still return a result, but it may be incomplete

        self.scan_results[symbol] = result
        return result

    def _calculate_indicators(self, result: ScanResult, symbol: str, historical_bars: list | None = None):
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
                self._populate_windowed_indicators(result, symbol, historical_bars)

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

    def _populate_windowed_indicators(self, result: ScanResult, symbol: str, historical_bars: list | None = None):
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
                    trend_score = 60 + (adx - 50) * 1.2   # 60-90
                elif adx >= 25:
                    trend_score = 30 + (adx - 25) * 1.2   # 30-60
                else:
                    trend_score = adx * 1.2               # 0-30
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
                    rsi_score = (30 - rsi) * 2    # 0..40, positive (bullish)
                elif rsi > 70:
                    rsi_score = (70 - rsi) * 2    # negative (bearish): e.g. 80 → -20
                else:
                    rsi_score = 0                  # neutral zone
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

            # Tape (Time & Sales) order-flow signals — only when the tape
            # subsystem is enabled and streaming (best-effort; a cold
            # engine just returns neutral / zero counts).
            from backend.config.settings import settings as _settings
            if _settings.tape.enabled:
                try:
                    from backend.api.tape.registry import get_tape_engine

                    snap = get_tape_engine(result.symbol).get_snapshot()
                    if snap["pressure"] == "heavy_buy":
                        signals.append("HEAVY_BUY_PRESSURE")
                    elif snap["pressure"] == "heavy_sell":
                        signals.append("HEAVY_SELL_PRESSURE")
                    if snap["block_count_5m"] > 0:
                        signals.append("BLOCK_ACTIVITY")
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
        try:
            with SessionLocal() as db:
                batch_bars = await market_data_manager.get_batch_historical_bars(
                    symbols,
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
        for symbol in symbols:
            # Pass the pre-fetched bars and quote to avoid individual database/provider calls
            result = self.scan_symbol(symbol, historical_bars=batch_bars.get(symbol), quote=batch_quotes.get(symbol))
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
        # blocking call here previously froze every other in-flight
        # request on the server, not just this one).
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
        def _prefetch() -> tuple[dict, dict]:
            # Lazy import: backend.ai.__init__ pulls in analyze → context,
            # which imports this module back — a top-level import here would
            # be circular when scanner is the import entry point.
            from backend.ai.sync_bridge import run_sync

            # Runs on a worker thread (asyncio.to_thread below), so no
            # event loop here — bridge the async batch-bars call.
            bars = run_sync(market_data_manager.get_batch_historical_bars(
                symbols,
                timeframe="1d",
                range_="3mo",
                use_cache=True,
            ))
            quotes = market_data_manager.get_batch_quotes(symbols)
            return bars, quotes

        batch_bars: dict = {}
        batch_quotes: dict = {}
        try:
            batch_bars, batch_quotes = await asyncio.to_thread(_prefetch)
        except Exception as e:
            logger.warning(f"Async scan batch pre-fetch failed, falling back to per-symbol: {e}")

        # Each thread receives pre-fetched data — no DB session opened inside the thread.
        tasks = [
            asyncio.to_thread(
                self.scan_symbol,
                symbol,
                batch_bars.get(symbol),
                batch_quotes.get(symbol),
            )
            for symbol in symbols
        ]
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
