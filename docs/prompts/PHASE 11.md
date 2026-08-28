# PHASE 11 — EVENT-DRIVEN REAL-TIME ENGINE

Implement an event-driven architecture.

Events:

QUOTE_UPDATED
BAR_COMPLETED
INDICATOR_UPDATED
TREND_UPDATED
TREND_TRANSITION
MARKET_REGIME_CHANGED
ALERT_TRIGGERED
PROVIDER_STATUS_CHANGED

Do NOT recalculate every symbol and every timeframe after every update.

When a new 1m candle arrives:

Only update affected symbol/timeframe and dependent calculations.

When a 5m candle closes:

Update 5m calculations.

Do not recalculate 1D indicators every second.

Create:

EventBus
MarketEventProcessor
TrendUpdateService

ALERT ENGINE

Support:

- trend crosses +70
- trend crosses -70
- trend direction changes
- trend strengthens
- trend weakens
- full timeframe alignment
- timeframe conflict
- breakout
- breakdown
- volume expansion
- divergence
- market regime change

Add WebSocket support.

Frontend should receive incremental updates.

Add alert history.

Add tests.

Then STOP.
