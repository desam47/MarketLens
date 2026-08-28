# PHASE 7 — MULTI-TIMEFRAME ANALYSIS

Implement MultiTimeframeEngine.

For every symbol calculate:

1m
5m
15m
30m
1h
4h
1d
1w

where data is available.

Create:

TimeframeTrendSnapshot

and:

MultiTimeframeSnapshot

Example:

NVDA

1m   +72
5m   +81
15m  +76
1h   +88
4h   +91
1d   +84

Calculate:

- timeframe alignment
- bullish alignment
- bearish alignment
- conflicting timeframes
- short-term direction
- intermediate direction
- higher-timeframe direction

Create configurable timeframe weighting.

Example:

day trading:

5m
15m
1h
4h
1d

swing:

15m
1h
4h
1d
1w

Do not assume all timeframes are equally important.

Do not create trade signals yet.

Add tests.

Then STOP.