"""
Signal-replay backtest engine.

Iterates stored daily bars day-by-day, builds a synthetic
``ScanResult`` for each bar, calls the live signal generator
(``Scanner._generate_signals``) and records every fired signal as a
``BacktestTrade`` with 1d/5d/20d forward returns.

Runs are short (a year of daily bars = ~250 iterations × indicator
math) and run synchronously inside the request so the dashboard can
POST a run and read the result in the same flow.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from backend.database import SessionLocal
from backend.models import BacktestRun, BacktestTrade
from backend.repositories.backtest_repository import BacktestRepository

from .replay import (
    HIGH_VOLUME_MULTIPLIER,
    INDICATOR_WARMUP,
    VOLUME_LOOKBACK,
    build_scan_result,
)

logger = logging.getLogger(__name__)

# Default signal set used when a request doesn't specify one. Mirrors
# the live scanner's signal surface minus the MTF pair (out of scope
# for v1).
DEFAULT_SIGNALS: tuple[str, ...] = (
    "RSI_OVERSOLD",
    "RSI_OVERBOUGHT",
    "MACD_BULLISH",
    "MACD_BEARISH",
    "HIGH_VOLUME",
)

# Forward-return windows in *bar counts*. A year of daily bars is
# ~252 trading days, so 20 bars = ~1 calendar month.
FORWARD_WINDOWS: tuple[tuple[str, int], ...] = (
    ("1d", 1),
    ("5d", 5),
    ("20d", 20),
)

# Minimum bars needed for a run to produce meaningful results:
# 14 for RSI warmup + 20 to compute the 20d forward return for the
# last possible entry. Anything less and we mark the run failed.
MIN_BARS_FOR_RUN = INDICATOR_WARMUP + 20


@dataclass
class BacktestConfig:
    """Configuration for a single backtest run."""

    symbol: str
    start_date: datetime
    end_date: datetime
    signals: list[str]
    timeframe: str = "1d"


class BacktestEngine:
    """Process-wide signal-replay backtest engine."""

    def run(self, config: BacktestConfig) -> int:
        """Execute a full backtest and persist the run + trades.

        Steps:
            1. Insert a ``BacktestRun`` row with status="running".
            2. Load bars from ``BarRepository`` and filter to
               ``[start, end)``.
            3. Iterate bar-by-bar; for each, build a synthetic
               ``ScanResult`` and call ``Scanner._generate_signals``.
            4. For each fired signal in the requested set, record a
               ``BacktestTrade`` with 1d/5d/20d forward returns.
            5. Update the run row with the summary metrics and
               status="completed", or status="failed" with an error.

        Returns the integer ``run_id`` of the persisted run. The caller
        is responsible for re-fetching it on an open session; this
        method closes its internal sessions before returning so the
        ORM object would be detached.
        """
        # 1. Persist the run row up-front so it exists with status
        #    "running" even if the engine crashes mid-iteration.
        repo = BacktestRepository()
        try:
            signals_csv = ",".join(config.signals)
            run = repo.create_run(
                symbol=config.symbol,
                timeframe=config.timeframe,
                start_date=config.start_date,
                end_date=config.end_date,
                signals_requested=signals_csv,
                status="running",
            )
            run_id = run.id
        finally:
            repo.close()

        # 2. Load bars in a fresh session so the long iteration
        #    doesn't hold a transaction open.
        bars = self._load_bars(
            config.symbol, config.timeframe,
            config.start_date, config.end_date,
        )

        if len(bars) < MIN_BARS_FOR_RUN:
            self._mark_failed(run_id, "insufficient history")
            return run_id

        # 3-4. Iterate bar-by-bar, generate signals, record trades.
        trades: list[BacktestTrade] = []
        scanner = _build_scanner()
        signals_set = set(config.signals)
        N = len(bars)

        for i in range(INDICATOR_WARMUP, N):
            window = bars[i - INDICATOR_WARMUP : i + 1]
            result = build_scan_result(
                symbol=config.symbol,
                timestamp=bars[i].timestamp,
                window=window,
            )
            # Drive the live signal generator with the synthetic
            # result. ``_generate_signals`` reads only
            # ``result.indicator_values`` and ``result.trend_signals``
            # so this is safe even though ``result.quote`` is None.
            scanner._generate_signals(result)

            for sig in result.signals:
                if sig not in signals_set:
                    continue
                # HIGH_VOLUME rule in replay uses a relative
                # threshold (2x 20-bar mean) instead of the live
                # 1,000,000 placeholder. Recompute it here from the
                # window and skip the trade if the rule doesn't fire.
                if sig == "HIGH_VOLUME":
                    rel = _relative_volume(
                        current=bars[i].volume,
                        history=bars[max(0, i - VOLUME_LOOKBACK) : i + 1],
                    )
                    if rel < HIGH_VOLUME_MULTIPLIER:
                        continue

                trade = _build_trade(
                    signal=sig,
                    bars=bars,
                    entry_index=i,
                )
                if trade is None:
                    # Not enough forward history (shouldn't happen
                    # because we cap i at N - 21, but guard anyway).
                    continue
                trades.append(trade)

        # 5. Compute summary metrics and persist the final state.
        metrics = _compute_metrics(trades)

        repo = BacktestRepository()
        try:
            repo.update_run_status(
                run_id,
                status="completed",
                total_bars=len(bars),
                total_signals=len(trades),
                win_rate_1d=metrics["win_rate_1d"],
                avg_return_1d=metrics["avg_return_1d"],
                avg_return_5d=metrics["avg_return_5d"],
                avg_return_20d=metrics["avg_return_20d"],
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            if trades:
                repo.add_trades(run_id, trades)
        finally:
            repo.close()

        return run_id

    # --- helpers --------------------------------------------------------

    def _mark_failed(self, run_id: int, error: str) -> None:
        repo = BacktestRepository()
        try:
            repo.update_run_status(
                run_id,
                status="failed",
                error=error,
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        finally:
            repo.close()

    def _load_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list:
        """Load bars for ``(symbol, timeframe)`` filtered to ``[start, end)``.

        ``BarRepository.get_bars`` doesn't support a date range so the
        filter is applied in Python. For v1's expected scale (a year
        of daily bars = ~250 rows) the in-memory filter is fine.
        """
        from backend.repositories.bar_repository import get_bars

        db = SessionLocal()
        try:
            all_bars = get_bars(db, symbol, timeframe)
        finally:
            db.close()
        return [b for b in all_bars if start <= b.timestamp < end]


# --- module-level helpers (kept outside the class so the tests can
# call them without instantiating the engine) ----------------------


def _build_scanner():
    """Construct a ``Scanner`` instance for the replay loop.

    Pulled into a helper so tests can monkeypatch it to a stub
    scanner.
    """
    from backend.scanner.scanner import Scanner

    return Scanner()


def _relative_volume(current: float, history: Sequence) -> float:
    """``current / mean(volume[-N:])`` with N=20 by default."""
    if not history:
        return 0.0
    lookback = [b.volume for b in history[-VOLUME_LOOKBACK:]]
    if not lookback:
        return 0.0
    mean = sum(lookback) / len(lookback)
    if mean <= 0:
        return 0.0
    return current / mean


def _build_trade(
    *,
    signal: str,
    bars: Sequence,
    entry_index: int,
) -> BacktestTrade | None:
    """Build a ``BacktestTrade`` for the given entry bar index.

    Forward returns are computed bar-close-to-bar-close at the
    configured offsets. Returns ``None`` if any of the forward
    offsets would land past the available history.
    """
    N = len(bars)
    entry = bars[entry_index]
    trade = BacktestTrade(
        signal=signal,
        entry_date=entry.timestamp,
        entry_price=entry.close,
    )
    for label, offset in FORWARD_WINDOWS:
        idx = entry_index + offset
        if idx >= N:
            return None
        exit_bar = bars[idx]
        if entry.close <= 0:
            return None
        ret = (exit_bar.close - entry.close) / entry.close * 100.0
        if label == "1d":
            trade.exit_date_1d = exit_bar.timestamp
            trade.exit_price_1d = exit_bar.close
            trade.return_1d = ret
        elif label == "5d":
            trade.exit_date_5d = exit_bar.timestamp
            trade.exit_price_5d = exit_bar.close
            trade.return_5d = ret
        elif label == "20d":
            trade.exit_date_20d = exit_bar.timestamp
            trade.exit_price_20d = exit_bar.close
            trade.return_20d = ret
    return trade


def _compute_metrics(trades: Sequence[BacktestTrade]) -> dict:
    """Compute summary metrics from a list of trades.

    ``win_rate_1d`` is the fraction of trades with a positive 1d
    return. ``avg_return_*`` is the arithmetic mean return across
    the trades, expressed as a percentage. All four are ``None``
    when the trade list is empty.
    """
    if not trades:
        return {
            "win_rate_1d": None,
            "avg_return_1d": None,
            "avg_return_5d": None,
            "avg_return_20d": None,
        }
    n = len(trades)
    wins = sum(1 for t in trades if (t.return_1d or 0) > 0)
    one_d = [t.return_1d for t in trades if t.return_1d is not None]
    five_d = [t.return_5d for t in trades if t.return_5d is not None]
    twenty_d = [t.return_20d for t in trades if t.return_20d is not None]
    return {
        "win_rate_1d": wins / n,
        "avg_return_1d": sum(one_d) / len(one_d) if one_d else None,
        "avg_return_5d": sum(five_d) / len(five_d) if five_d else None,
        "avg_return_20d": sum(twenty_d) / len(twenty_d) if twenty_d else None,
    }


# Process-wide singleton. Imported by the router.
backtest_engine = BacktestEngine()
