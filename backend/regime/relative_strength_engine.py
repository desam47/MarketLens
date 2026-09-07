"""
Relative Strength Engine — classifies a symbol's performance vs benchmarks.

Phase 8 spec: classify each stock relative to SPY, QQQ, and a sector ETF
into one of STRONG_OUTPERFORMER / OUTPERFORMER / INLINE /
UNDERPERFORMER / STRONG_UNDERPERFORMER.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from ..config.settings import settings
from ..trend.trend_engine import TrendEngine
from ..repositories.bar_repository import get_bars as _get_db_bars

logger = logging.getLogger(__name__)


class RelativeStrengthClassification(StrEnum):
    STRONG_OUTPERFORMER = "strong_outperformer"
    OUTPERFORMER = "outperformer"
    INLINE = "inline"
    UNDERPERFORMER = "underperformer"
    STRONG_UNDERPERFORMER = "strong_underperformer"
    UNKNOWN = "unknown"


@dataclass
class RelativeStrengthSignal:
    """Alpha vs a single benchmark over the configured lookback window."""
    symbol: str
    benchmark: str           # e.g. "SPY", "QQQ", "XLK"
    rs_pct: float            # (symbol_return - benchmark_return) as a fraction
    classification: RelativeStrengthClassification
    symbol_return_pct: float
    benchmark_return_pct: float
    lookback_days: int
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "benchmark": self.benchmark,
            "rs_pct": round(self.rs_pct * 100, 2),            # percent for readability
            "classification": self.classification.value,
            "symbol_return_pct": round(self.symbol_return_pct * 100, 2),
            "benchmark_return_pct": round(self.benchmark_return_pct * 100, 2),
            "lookback_days": self.lookback_days,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class RelativeStrengthEngine:
    """
    Computes relative strength vs configurable benchmarks (default: SPY, QQQ)
    and the symbol's sector ETF.

    Uses the same TrendEngine for all price streams; the relative
    performance is the difference in return over the configured lookback
    window.
    """

    def __init__(self, symbol: str, lookback_days: int | None = None,
                 trend_engines: dict[str, TrendEngine] | None = None):
        self.symbol = symbol.upper()
        self.lookback_days = lookback_days or settings.relative_strength.lookback_days
        self._cfg = settings.relative_strength

        # Phase 3.9.2: accept injected shared TrendEngines (one per price
        # stream — the stock plus its benchmarks). Default to building
        # fresh engines so unit tests and one-off scripts still work.
        if trend_engines is not None:
            self._engines: dict[str, TrendEngine] = dict(trend_engines)
        else:
            self._engines = {
                sym: TrendEngine(sym)
                for sym in self._all_symbols()
            }

        self._price_history: dict[str, list[tuple[datetime, float]]] = {
            sym: [] for sym in self._all_symbols()
        }
        self._signals: list[RelativeStrengthSignal] = []

    def _all_symbols(self) -> list[str]:
        """Symbol list for the stock + its configured benchmarks."""
        return [self.symbol, *self._cfg.benchmark_list()]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self,
               price: float,
               volume: float,
               timestamp: datetime,
               symbol: str) -> None:
        """
        Feed a new price tick into the appropriate TrendEngine.

        Args:
            price: current price
            volume: tick volume
            timestamp: tick timestamp
            symbol: which symbol this tick belongs to (stock or benchmark)
        """
        symbol = symbol.upper()
        if symbol not in self._price_history:
            logger.warning(f"RelativeStrengthEngine: unknown symbol {symbol}, ignoring")
            return

        self._engines[symbol].update(price, volume, timestamp, provider="internal")
        self._price_history[symbol].append((timestamp, price))
        self._prune_history(symbol)

    def update_all(self,
                   prices: dict[str, float],
                   volume: float,
                   timestamp: datetime) -> None:
        """Feed a price for every tracked symbol at once (e.g. from a quote bundle)."""
        for sym, price in prices.items():
            self.update(price, volume, timestamp, sym)

    def get_signals(self) -> list[RelativeStrengthSignal]:
        """Return signals for the configured benchmarks. Call compute() first."""
        benchmarks = set(self._cfg.benchmark_list())
        return [s for s in self._signals if s.benchmark in benchmarks]

    def _ensure_historical_data(self) -> None:
        """
        Seed _price_history from the database for any symbol that has no data yet.

        Called lazily in compute() so that the watchlist page (which creates a
        fresh engine per API request) gets real RS values instead of always
        returning UNKNOWN because the live-ingestion pipeline hasn't fed prices yet.
        """
        for sym in self._all_symbols():
            if self._price_history[sym]:
                continue  # already seeded from live ingestion
            try:
                # Lazy import to avoid a circular dependency at module load time.
                from backend.database import SessionLocal
                db = SessionLocal()
                try:
                    bars = _get_db_bars(
                        db,
                        sym,
                        "1d",
                        limit=self.lookback_days * 2,
                    )
                    for bar in bars:
                        self._price_history[sym].append((bar.timestamp, bar.close))
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"Failed to seed historical 1d bars for {sym}: {e}")

    def compute(self, timestamp: datetime | None = None) -> list[RelativeStrengthSignal]:
        """
        Compute relative-strength signals vs all configured benchmarks.

        Returns one RelativeStrengthSignal per configured benchmark plus
        the sector ETF signal if the engine was seeded with sector ETF data.
        """
        self._ensure_historical_data()
        ts = timestamp or datetime.now(timezone.utc)
        signals = []

        for benchmark in self._cfg.benchmark_list():
            sig = self._compute_vs_benchmark(benchmark, ts)
            if sig:
                signals.append(sig)

        # Also compute vs sector ETF if a third engine was registered
        sector_sig = self._compute_vs_benchmark("SECTOR", ts)
        if sector_sig:
            signals.append(sector_sig)

        self._signals = signals
        return signals

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_vs_benchmark(self,
                              benchmark: str,
                              ts: datetime) -> RelativeStrengthSignal | None:
        """Compute alpha vs one benchmark."""
        sym_hist = self._price_history[self.symbol]
        bmk_hist = self._price_history.get(benchmark, [])

        if not self._enough_history(sym_hist) or not self._enough_history(bmk_hist):
            return RelativeStrengthSignal(
                symbol=self.symbol,
                benchmark=benchmark,
                rs_pct=0.0,
                classification=RelativeStrengthClassification.UNKNOWN,
                symbol_return_pct=0.0,
                benchmark_return_pct=0.0,
                lookback_days=self.lookback_days,
                timestamp=ts,
            )

        sym_ret = self._return_over_lookback(sym_hist)
        bmk_ret = self._return_over_lookback(bmk_hist) if benchmark != "SECTOR" else sym_ret
        rs = sym_ret - bmk_ret

        cls = self._classify(rs)

        return RelativeStrengthSignal(
            symbol=self.symbol,
            benchmark=benchmark,
            rs_pct=rs,
            classification=cls,
            symbol_return_pct=sym_ret,
            benchmark_return_pct=bmk_ret,
            lookback_days=self.lookback_days,
            timestamp=ts,
        )

    def _enough_history(self, hist: list) -> bool:
        return len(hist) >= self.lookback_days

    def _return_over_lookback(self, hist: list[tuple[datetime, float]]) -> float:
        """Simple return: (last_price - price_N_days_ago) / price_N_days_ago."""
        if len(hist) < 2:
            return 0.0
        lookback = min(len(hist), self.lookback_days)
        old_price = hist[-lookback][1]
        if old_price <= 0:
            return 0.0
        return (hist[-1][1] - old_price) / old_price

    def _classify(self, rs_pct: float) -> RelativeStrengthClassification:
        cfg = self._cfg
        if rs_pct > cfg.strong_outperformer_threshold:
            return RelativeStrengthClassification.STRONG_OUTPERFORMER
        if rs_pct > cfg.outperformer_threshold:
            return RelativeStrengthClassification.OUTPERFORMER
        if rs_pct < cfg.strong_underperformer_threshold:
            return RelativeStrengthClassification.STRONG_UNDERPERFORMER
        if rs_pct < cfg.underperformer_threshold:
            return RelativeStrengthClassification.UNDERPERFORMER
        return RelativeStrengthClassification.INLINE

    def _prune_history(self, symbol: str) -> None:
        """Keep at most lookback_days * 2 entries per symbol."""
        hist = self._price_history[symbol]
        max_len = self.lookback_days * 3
        if len(hist) > max_len:
            self._price_history[symbol] = hist[-max_len:]
