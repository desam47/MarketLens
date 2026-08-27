"""Backtesting subsystem.

The v1 backtester is a *signal-replay* engine: it iterates stored daily
bars day-by-day, builds a synthetic ``ScanResult`` for each bar, calls
``Scanner._generate_signals`` to get the live signal list, and records
each fired signal as a trade with forward 1d/5d/20d returns.

Exports the process-wide ``BacktestEngine`` singleton imported by the
API router.
"""
from .engine import BacktestEngine, backtest_engine

__all__ = ["BacktestEngine", "backtest_engine"]
