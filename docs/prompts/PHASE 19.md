# PHASE 19 — STRATEGY LAB

Build a research interface for testing the trend model.

Allow users to adjust:

EMA parameters
RSI parameters
MACD parameters
ADX parameters
ATR parameters
SuperTrend multiplier
indicator weights
timeframe weights
trend thresholds

Run historical experiments.

Compare configurations.

Show:

Win rate
Average return
Median return
Profit factor
Max drawdown
Sharpe
Signal count
Signal frequency
Performance by market regime

Include:

in-sample
validation
out-of-sample

Warn if a parameter combination appears overfit.

Store experiments.

Each experiment should have:

experiment_id
strategy_version
parameters
date range
symbols
results
created_at

Then STOP.
