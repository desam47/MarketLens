# PHASE 4 — TIMEFRAME AND CANDLE ENGINE

Implement the timeframe system.

Default timeframes:

1m
5m
15m
30m
1h
4h
1d
1w

Do not hard-code timeframe logic throughout the application.

Create a Timeframe model/configuration.

The engine must:

- retrieve historical bars
- normalize timestamps
- aggregate lower timeframes where appropriate
- correctly handle market sessions
- handle missing candles
- handle holidays
- handle daylight-saving time
- distinguish premarket/regular/after-hours

Create:

TimeframeEngine

Input:

normalized market bars

Output:

correct timeframe candles

Implement tests for candle aggregation.

Examples:

1m → 5m
1m → 15m
5m → 15m
15m → 1h

Ensure OHLCV aggregation is mathematically correct.

Do NOT calculate trend yet.

Add data-quality checks.

Test:

- missing candles
- duplicate candles
- incomplete candles
- session boundaries
- timezone conversion

Then STOP.