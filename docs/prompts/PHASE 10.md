# PHASE 10 — SCANNER AND RANKING ENGINE

Build a flexible scanner.

It must operate on user watchlists.

Support conditions such as:

trend > 70

daily trend bullish

daily bullish AND 15m bullish

daily bullish AND 5m bearish

trend improving

trend deteriorating

outperforming QQQ

volume > average

strong multi-timeframe alignment

bullish transition

bearish transition

strongest bullish

strongest bearish

Do not hard-code individual scanner queries.

Create composable filter objects.

Create RankingEngine.

Rank by:

- trend score
- confidence
- timeframe alignment
- relative strength
- sector alignment
- volume confirmation
- momentum
- historical reliability when available

Produce rankings:

Strongest Bullish
Strongest Bearish
Strongest Momentum
Biggest Improvement
Biggest Deterioration
Best Multi-Timeframe Alignment
Strongest Relative Strength

Add API endpoints.

Add frontend scanner page.

Then STOP.
