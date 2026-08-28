# PHASE 9 — ADVANCED PRICE ANALYSIS

Implement:

1. TrendTransitionEngine
2. DivergenceEngine
3. SupportResistanceEngine

TREND TRANSITIONS

Detect:

- bullish acceleration
- bullish weakening
- bearish acceleration
- bearish weakening
- bullish reversal
- bearish reversal

Example:

+35 → +68

BULLISH_ACCELERATION

+84 → +61

BULLISH_WEAKENING

DIVERGENCE

Detect:

Bullish RSI divergence
Bearish RSI divergence
Bullish MACD divergence
Bearish MACD divergence
Volume divergence where meaningful

SUPPORT/RESISTANCE

Detect:

- swing highs
- swing lows
- pivot highs
- pivot lows
- previous day high/low
- previous week high/low
- consolidation levels

Every level should have:

price
type
timeframe
strength
touch_count
age
distance_from_price

Do not use future information.

Historical calculations must use only information available at that timestamp.

Add tests.

Then STOP.
