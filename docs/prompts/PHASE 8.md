# PHASE 8 — MARKET CONTEXT ENGINE

Implement three independent components.

1. MarketRegimeEngine
2. RelativeStrengthEngine
3. SectorEngine

MARKET REGIME

Analyze:

SPY
QQQ
IWM
VIX

Determine:

RISK_ON
RISK_OFF
NEUTRAL
TRANSITION

Also determine:

- market trend
- volatility state
- momentum
- trend strength

RELATIVE STRENGTH

For each stock calculate performance relative to:

SPY
QQQ
sector ETF

Classify:

STRONG_OUTPERFORMER
OUTPERFORMER
INLINE
UNDERPERFORMER
STRONG_UNDERPERFORMER

SECTOR

Map:

symbol
→ sector
→ sector ETF

Calculate:

stock trend
sector trend
market trend

Create an alignment score.

Example:

Stock bullish
Sector bullish
QQQ bullish
SPY bullish

should have stronger contextual alignment than:

Stock bullish
Sector bearish
SPY bearish

Do not modify the core TrendEngine.

Context should influence confidence/ranking later, not corrupt raw trend calculations.

Add tests.

Then STOP.
