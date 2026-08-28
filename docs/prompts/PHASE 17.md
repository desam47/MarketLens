# PHASE 17 — NATURAL LANGUAGE MARKET SEARCH

Create a natural-language query layer.

Users should be able to ask:

"Show me the strongest bullish stocks."

"Which stocks have bullish daily trends but bearish 5-minute trends?"

"Which stocks just transitioned bullish?"

"Which stocks are outperforming QQQ?"

"Which stocks have strong trend and volume confirmation?"

"Which stocks are bullish while SPY is bearish?"

Convert natural language into structured scanner filters.

Do NOT let the AI directly query the database arbitrarily.

Create a controlled query schema.

Example:

{
  trend_min: 70,
  timeframe: "15m",
  direction: "bullish",
  relative_strength_min: 50
}

Validate filters before execution.

Return deterministic scanner results.

AI may explain results afterward.

Then STOP.
