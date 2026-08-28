# PHASE 14 — BACKTESTING AND VALIDATION

Build a proper backtesting engine.

Requirements:

- no look-ahead bias
- no future leakage
- configurable timeframe
- configurable symbols
- configurable date range
- configurable strategy version
- configurable parameters

Metrics:

- number of signals
- win rate
- average return
- median return
- profit factor
- max drawdown
- Sharpe where appropriate
- maximum favorable excursion
- maximum adverse excursion
- signal frequency

Implement:

Historical backtest
Walk-forward testing
Out-of-sample testing

Allow comparing:

strategy-v1.0
strategy-v1.1
etc.

Do not optimize only for win rate.

Warn about overfitting.

Add tests specifically designed to detect look-ahead bias.

Then STOP.
