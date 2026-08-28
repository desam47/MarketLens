# PHASE 6 — CORE TREND ENGINE

Now build the core quantitative trend engine.

Create:

TrendEngine

For every symbol/timeframe calculate:

- trend direction
- trend score
- trend strength
- momentum
- structure
- confidence inputs
- market state

Trend score:

-100 to +100

Classifications:

+70 to +100 = STRONG_BULLISH
+30 to +69 = BULLISH
+10 to +29 = WEAK_BULLISH
-9 to +9 = NEUTRAL
-10 to -29 = WEAK_BEARISH
-30 to -69 = BEARISH
-70 to -100 = STRONG_BEARISH

Also support:

NO_SIGNAL

when data is insufficient.

Initial configurable scoring components:

EMA structure
Market structure
SuperTrend
MACD
ADX
RSI
Volume
Momentum

Make all weights configurable.

Do NOT claim that the weights are statistically optimal.

Create a TrendSnapshot model.

Example:

{
  symbol,
  timeframe,
  timestamp,
  direction,
  score,
  strength,
  momentum,
  structure,
  data_quality,
  strategy_version
}

Separate:

direction

from:

strength

from:

confidence

Do not automatically convert bullish trend into a BUY signal.

Add extensive tests.

Test:

- strong bullish
- weak bullish
- neutral
- weak bearish
- strong bearish
- insufficient data
- conflicting indicators

Then STOP.