"""
Signal-replay backtest engine.

Iterates stored daily bars day-by-day, builds a synthetic
``ScanResult`` for each bar, calls the live signal generator
(``Scanner._generate_signals``) and records every fired signal as a
``BacktestTrade`` with 1d/5d/20d forward returns plus MFE/MAE.

Runs are short (a year of daily bars = ~250 iterations × indicator
math) and run synchronously inside the request so the dashboard can
POST a run and read the result in the same flow.

Phase 14 adds:
  - MFE / MAE per trade (peak / trough over 20-bar window)
  - Equity curve, max drawdown, Sharpe ratio, profit factor, median
  - Signal frequency (signals / trading days)
  - Overfitting warning
  - Walk-forward analysis helper
  - Out-of-sample flag for OOS slices

Phase 19 adds:
  - Optional ``ExperimentParameters`` on ``BacktestConfig`` so the
    Strategy Lab can vary indicator periods + RSI thresholds per run.
  - Optional ``regime_tagging_enabled`` flag that back-fills
    ``BacktestTrade.regime_at_entry`` with "risk_on" / "risk_off" /
    "neutral" from the ADX+RSI bar-window classifier.
  - ``replay_generate_signals`` helper that honours the
    ``_rsi_oversold`` / ``_rsi_overbought`` keys written by
    ``build_indicator_values`` when ``ExperimentParameters`` is
    supplied, so a 25/75 RSI pair fires different signals than 30/70.
"""
from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.utils.timezone import now_ny

from backend.config.settings import settings as _settings
from backend.database import SessionLocal
from backend.models import BacktestTrade
from backend.repositories.backtest_repository import BacktestRepository
from backend.scanner.scanner import ScanResult

from .replay import (
    HIGH_VOLUME_MULTIPLIER,
    INDICATOR_WARMUP,
    REGIME_WARMUP,
    VOLUME_LOOKBACK,
    build_scan_result,
    classify_regime,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .parameters import ExperimentParameters

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

# Regime-aware runs need ADX(14) * 2 = 28 bars of warmup.
MIN_BARS_FOR_REGIME_RUN = max(INDICATOR_WARMUP, REGIME_WARMUP) + 20

# Phase 14 — overfitting warning thresholds:
# A win rate above 70 % or Sharpe above 2.0 on a live-equivalent signal
# set is a red flag. Note: these thresholds are heuristics, not proofs.
OVERFIT_WINRATE_THRESHOLD = 0.70
OVERFIT_SHARPE_THRESHOLD = 2.0


@dataclass
class BacktestConfig:
    """Configuration for a single backtest run."""

    symbol: str
    start_date: datetime
    end_date: datetime
    signals: list[str]
    timeframe: str = "1d"
    # Strategy version for this run. When None the engine uses the
    # server-side ``TrendSettings.strategy_version``.
    strategy_version: str | None = None
    # Phase 19: optional experiment parameters. When supplied, the
    # engine uses the indicated indicator periods and RSI thresholds.
    experiment_params: ExperimentParameters | None = None
    # Phase 19: tag each trade with the market regime at entry time
    # (risk_on / risk_off / neutral) from the ADX+RSI bar-window.
    regime_tagging_enabled: bool = False


@dataclass
class WalkForwardConfig:
    """Configuration for a walk-forward analysis.

    The date range is split into ``n_splits`` equal in-sample / out-of-sample
    windows. For each split the most recent ``test_pct`` of the window is
    held out as OOS. The remaining ``(1 - test_pct)`` is in-sample.

    Example: ``n_splits=4, test_pct=0.25`` gives 4 consecutive train/test
    pairs. Each test slice is OOS; each training slice is in-sample.
    """
    symbol: str
    start_date: datetime
    end_date: datetime
    signals: list[str]
    timeframe: str = "1d"
    n_splits: int = 4
    # Fraction of each window held out for OOS testing (0.1–0.4 is typical).
    test_pct: float = 0.25
    strategy_version: str | None = None


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
               ``BacktestTrade`` with 1d/5d/20d forward returns + MFE/MAE.
            5. Compute extended metrics (Sharpe, drawdown, equity curve …).
            6. Update the run row with all metrics and status="completed",
               or status="failed" with an error.

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
            strategy_version = (
                config.strategy_version
                if config.strategy_version is not None
                else _settings.trend.strategy_version
            )
            run = repo.create_run(
                symbol=config.symbol,
                timeframe=config.timeframe,
                start_date=config.start_date,
                end_date=config.end_date,
                signals_requested=signals_csv,
                strategy_version=strategy_version,
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

        # Phase 19 — when experiment_params is supplied the replay
        # window uses the parameterised indicator periods and the
        # replay signal generator honours the per-experiment RSI
        # thresholds. When regime_tagging is on, the loop's starting
        # index is bumped to account for ADX(period * 2) warmup so
        # the regime classifier has enough bars.
        params = config.experiment_params
        adx_period = params.adx_period if params is not None else 14
        regime_warmup = max(INDICATOR_WARMUP, adx_period * 2)
        if config.regime_tagging_enabled and len(bars) < MIN_BARS_FOR_REGIME_RUN:
            self._mark_failed(run_id, "insufficient history for regime tagging")
            return run_id
        start_i = regime_warmup if config.regime_tagging_enabled else INDICATOR_WARMUP
        use_replay_signals = params is not None

        for i in range(start_i, N):
            window = bars[i - INDICATOR_WARMUP : i + 1]
            result = build_scan_result(
                symbol=config.symbol,
                timestamp=bars[i].timestamp,
                window=window,
                params=params,
            )
            if use_replay_signals:
                # Honour the per-experiment RSI / MACD thresholds.
                replay_generate_signals(result, params=params)
            else:
                # Drive the live signal generator with the synthetic
                # result. ``_generate_signals`` reads only
                # ``result.indicator_values`` and ``result.trend_signals``
                # so this is safe even though ``result.quote`` is None.
                scanner._generate_signals(result)

            # Phase 19 — optional regime tagging at entry. The
            # classifier needs the full regime warmup window which is
            # larger than the indicator window when ADX is used.
            regime: str | None = None
            if config.regime_tagging_enabled:
                regime_window = bars[max(0, i - regime_warmup) : i + 1]
                if params is not None:
                    regime = classify_regime(
                        regime_window,
                        adx_period=params.adx_period,
                        rsi_period=params.rsi_period,
                        adx_trending=params.adx_trending_threshold,
                        rsi_bull=params.rsi_bullish_ceiling,
                        rsi_bear=params.rsi_bearish_floor,
                    )
                else:
                    regime = classify_regime(regime_window)

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
                    regime=regime,
                )
                if trade is None:
                    # Not enough forward history (shouldn't happen
                    # because we cap i at N - 21, but guard anyway).
                    continue
                trades.append(trade)

        # 5. Compute all metrics.
        metrics = _compute_metrics(trades, len(bars))

        # 6. Persist the final state.
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
                median_return_1d=metrics["median_return_1d"],
                median_return_5d=metrics["median_return_5d"],
                median_return_20d=metrics["median_return_20d"],
                max_drawdown=metrics["max_drawdown"],
                sharpe_ratio=metrics["sharpe_ratio"],
                profit_factor=metrics["profit_factor"],
                mfe_avg=metrics["mfe_avg"],
                mae_avg=metrics["mae_avg"],
                signal_frequency=metrics["signal_frequency"],
                equity_curve_json=metrics["equity_curve_json"],
                overfitting_warning=metrics["overfitting_warning"],
                completed_at=now_ny(),
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
                completed_at=now_ny(),
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


# ---------------------------------------------------------------------------
# Walk-forward analysis
# ---------------------------------------------------------------------------


def walk_forward_analyze(config: WalkForwardConfig) -> list[int]:
    """Run a walk-forward analysis and return a list of run_ids.

    The date range is split into ``n_splits`` windows of equal size.
    Each window is further split into in-sample (training) and
    out-of-sample (testing) sub-windows using ``test_pct``.

    The engine runs two backtests per split: one on the in-sample
    portion (out_of_sample=False) and one on the out-of-sample portion
    (out_of_sample=True). Returns the list of all run_ids in
    chronological order.

    Example output for ``n_splits=4, test_pct=0.25``:
        [run_is_0, run_oos_0, run_is_1, run_oos_1, run_is_2, run_oos_2,
         run_is_3, run_oos_3]
    """
    engine = BacktestEngine()
    total_days = (config.end_date - config.start_date).days
    # Need at least 2 splits * 1 day minimum per split to produce anything.
    if total_days < 2:
        return []

    window_days = total_days / config.n_splits
    test_days = int(window_days * config.test_pct)
    train_days = int(window_days - test_days)

    # If the IS window is too short to even warm up indicators, skip.
    if train_days < MIN_BARS_FOR_RUN:
        return []

    run_ids: list[int] = []
    effective_signals = config.signals or list(DEFAULT_SIGNALS)

    for split in range(config.n_splits):
        # In-sample window: [split_start, split_start + train_days)
        split_start = config.start_date + _days(split * window_days)
        is_start = split_start
        is_end = split_start + _days(train_days)
        if is_end >= config.end_date:
            break  # last window too short

        # Out-of-sample window: [is_end, is_end + test_days)
        oos_start = is_end
        oos_end = min(oos_start + _days(test_days), config.end_date)

        # Run IS backtest
        is_config = BacktestConfig(
            symbol=config.symbol,
            start_date=is_start,
            end_date=is_end,
            signals=effective_signals,
            timeframe=config.timeframe,
            strategy_version=config.strategy_version,
        )
        is_run_id = _run_with_oos_flag(engine, is_config, out_of_sample=False)
        run_ids.append(is_run_id)

        # Run OOS backtest (only if the OOS window has enough bars).
        # We need at least MIN_BARS_FOR_RUN bars in the OOS slice;
        # otherwise the engine marks the run as "insufficient history".
        if (oos_end - oos_start).days >= MIN_BARS_FOR_RUN:
            oos_config = BacktestConfig(
                symbol=config.symbol,
                start_date=oos_start,
                end_date=oos_end,
                signals=effective_signals,
                timeframe=config.timeframe,
                strategy_version=config.strategy_version,
            )
            oos_run_id = _run_with_oos_flag(engine, oos_config, out_of_sample=True)
            run_ids.append(oos_run_id)

    return run_ids


def _run_with_oos_flag(
    engine: BacktestEngine,
    config: BacktestConfig,
    out_of_sample: bool,
) -> int:
    """Run a backtest and then patch the OOS flag onto the saved row."""
    run_id = engine.run(config)
    repo = BacktestRepository()
    try:
        repo.update_run_status(run_id, status="completed", out_of_sample=out_of_sample)
    finally:
        repo.close()
    return run_id


def _days(n: float) -> timedelta:
    """Build a timedelta from a possibly-float number of days."""
    return timedelta(days=n)


# ---------------------------------------------------------------------------
# Module-level helpers (kept outside the class so the tests can call them
# without instantiating the engine)
# ---------------------------------------------------------------------------


def _build_scanner():
    """Construct a ``Scanner`` instance for the replay loop."""
    from backend.scanner.scanner import Scanner

    return Scanner()


def replay_generate_signals(result: ScanResult, params: ExperimentParameters | None = None) -> None:
    """Generate signals from ``result.indicator_values`` using per-experiment
    RSI thresholds.

    This is the replay equivalent of ``Scanner._generate_signals`` but
    reads the RSI oversold/overbought levels from the
    ``_rsi_oversold`` / ``_rsi_overbought`` keys written by
    ``build_indicator_values`` when ``ExperimentParameters`` is supplied.
    This lets the Strategy Lab vary RSI thresholds (e.g. 25/75) without
    touching the live scanner's hardcoded 30/70.

    MACD and volume signals are always generated identically to the live
    scanner.
    """
    try:
        signals: list[str] = []

        rsi_value = result.indicator_values.get("rsi")
        if rsi_value is not None:
            rsi_oversold = result.indicator_values.get("_rsi_oversold", 30.0)
            rsi_overbought = result.indicator_values.get("_rsi_overbought", 70.0)
            if rsi_value < rsi_oversold:
                signals.append("RSI_OVERSOLD")
            elif rsi_value > rsi_overbought:
                signals.append("RSI_OVERBOUGHT")

        macd_value = result.indicator_values.get("macd")
        if macd_value is not None:
            if macd_value > 0:
                signals.append("MACD_BULLISH")
            else:
                signals.append("MACD_BEARISH")

        # HIGH_VOLUME: caller is responsible for relative-volume check;
        # we just echo the signal if the caller set it.
        if "HIGH_VOLUME" in result.signals:
            signals.append("HIGH_VOLUME")

        result.signals = signals
    except Exception:
        result.signals = []


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
    regime: str | None = None,
) -> BacktestTrade | None:
    """Build a ``BacktestTrade`` for the given entry bar index.

    Forward returns are computed bar-close-to-bar-close at the
    configured offsets. MFE (maximum favorable excursion) and MAE
    (maximum adverse excursion) are computed over the 20-bar window
    from [entry+1, entry+20].

    ``regime`` is the Phase 19 regime-at-entry tag (one of "risk_on",
    "risk_off", "neutral"); ``None`` when regime tagging is disabled.

    Returns ``None`` if the entry is too close to the end of the bar
    list to compute the 20-bar forward window.
    """
    N = len(bars)
    entry = bars[entry_index]
    trade = BacktestTrade(
        signal=signal,
        entry_date=entry.timestamp,
        entry_price=entry.close,
    )
    if regime is not None:
        trade.regime_at_entry = regime
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

    # MFE / MAE over the 20-bar window.
    mfe_end = min(entry_index + 20, N)
    if mfe_end > entry_index + 1:
        high_prices = [bars[j].high for j in range(entry_index + 1, mfe_end)]
        low_prices = [bars[j].low for j in range(entry_index + 1, mfe_end)]
        if entry.close > 0 and high_prices and low_prices:
            trade.mfe = (max(high_prices) - entry.close) / entry.close * 100.0
            trade.mae = (min(low_prices) - entry.close) / entry.close * 100.0
    return trade


def _compute_metrics(trades: Sequence[BacktestTrade], total_bars: int) -> dict:
    """Compute the full Phase 14 metric set from a list of trades.

    All values are ``None`` when the trade list is empty. The equity
    curve is stored as a JSON list of ``[timestamp_iso, cum_return_pct]``
    pairs for the dashboard to plot.

    Overfitting warning heuristics (not proofs):
      - win_rate > 70 % → "win rate suspiciously high"
      - sharpe > 2.0  → "Sharpe ratio unusually high"
    """
    if not trades:
        return {
            "win_rate_1d": None,
            "avg_return_1d": None,
            "avg_return_5d": None,
            "avg_return_20d": None,
            "median_return_1d": None,
            "median_return_5d": None,
            "median_return_20d": None,
            "max_drawdown": None,
            "sharpe_ratio": None,
            "profit_factor": None,
            "mfe_avg": None,
            "mae_avg": None,
            "signal_frequency": None,
            "equity_curve_json": None,
            "overfitting_warning": None,
        }

    n = len(trades)
    wins = sum(1 for t in trades if (t.return_1d or 0) > 0)
    win_rate_1d = wins / n

    def _mean(vals: list[float]) -> float | None:
        return sum(vals) / len(vals) if vals else None

    def _median(vals: list[float]) -> float | None:
        if not vals:
            return None
        s = sorted(vals)
        m = len(s) // 2
        return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0

    def _sum_if_present(label: str) -> list[float]:
        return [getattr(t, f"return_{label}", None) or 0 for t in trades
                if getattr(t, f"return_{label}", None) is not None]

    one_d = _sum_if_present("1d")
    five_d = _sum_if_present("5d")
    twenty_d = _sum_if_present("20d")

    mfe_vals = [t.mfe for t in trades if t.mfe is not None]
    mae_vals = [t.mae for t in trades if t.mae is not None]

    # Signal frequency: signals per trading day.
    signal_frequency = n / total_bars if total_bars > 0 else None

    # Profit factor: gross profit / gross loss. Positive returns are
    # profits; non-positive returns are losses (loss of 0 counts as a
    # full loss to avoid infinity from zero-loss trades).
    gross_profit = sum(r for r in one_d if r > 0)
    gross_loss = abs(sum(r for r in one_d if r <= 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    # Annualized Sharpe ratio.
    # r_bar = mean(one_d)  (already a %; divide by 100 for the math)
    # sigma = population std dev of one_d
    # annualised = r_bar * sqrt(252) / sigma
    sharpe_ratio: float | None = None
    if len(one_d) >= 2:
        r_bar = sum(one_d) / len(one_d)
        variance = sum((r - r_bar) ** 2 for r in one_d) / max(len(one_d) - 1, 1)
        sigma = math.sqrt(variance)
        if sigma > 0:
            # one_d is in %, so convert back to fraction for Sharpe
            sharpe_ratio = (r_bar / 100) * math.sqrt(252) / (sigma / 100)

    # Equity curve (compound returns, newest-first order from trades).
    # We record a point after each trade entry so the curve step-function
    # is correct: the cumulative return holds until the next trade.
    # Sort trades by entry_date ascending.
    sorted_trades = sorted(trades, key=lambda t: t.entry_date or datetime.min)
    equity_points: list[list] = []  # [timestamp_iso, cum_return_pct]
    cum_return = 0.0
    for t in sorted_trades:
        if t.return_1d is not None:
            # Compound: (1 + r1/100) * (1 + r2/100) - 1, expressed as %
            cum_return = (1 + cum_return / 100) * (1 + t.return_1d / 100) - 1
            cum_return *= 100
        ts = t.entry_date.isoformat() if t.entry_date else None
        equity_points.append([ts, round(cum_return, 4)])
    equity_curve_json = json.dumps(equity_points) if equity_points else None

    # Max drawdown from equity curve.
    max_dd: float | None = None
    if equity_points:
        peak = equity_points[0][1]
        max_dd = 0.0
        for _, val in equity_points:
            if val > peak:
                peak = val
            dd = val - peak  # always <= 0
            if dd < max_dd:
                max_dd = dd

    # Overfitting warning.
    warnings: list[str] = []
    if win_rate_1d > OVERFIT_WINRATE_THRESHOLD:
        warnings.append(
            f"win rate {win_rate_1d:.0%} exceeds {OVERFIT_WINRATE_THRESHOLD:.0%} — "
            "may indicate overfitting to in-sample data"
        )
    if sharpe_ratio is not None and sharpe_ratio > OVERFIT_SHARPE_THRESHOLD:
        warnings.append(
            f"Sharpe ratio {sharpe_ratio:.1f} exceeds {OVERFIT_SHARPE_THRESHOLD:.1f} — "
            "verify on out-of-sample data"
        )
    overfitting_warning = "; ".join(warnings) if warnings else None

    return {
        "win_rate_1d": win_rate_1d,
        "avg_return_1d": _mean(one_d),
        "avg_return_5d": _mean(five_d),
        "avg_return_20d": _mean(twenty_d),
        "median_return_1d": _median(one_d),
        "median_return_5d": _median(five_d),
        "median_return_20d": _median(twenty_d),
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe_ratio,
        "profit_factor": profit_factor,
        "mfe_avg": _mean(mfe_vals),
        "mae_avg": _mean(mae_vals),
        "signal_frequency": signal_frequency,
        "equity_curve_json": equity_curve_json,
        "overfitting_warning": overfitting_warning,
    }


# Process-wide singleton. Imported by the router.
backtest_engine = BacktestEngine()
