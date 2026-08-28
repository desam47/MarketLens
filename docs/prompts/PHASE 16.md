# PHASE 16 — AI MARKET ANALYSIS

Now connect the optional AI layer to the quantitative engine.

AI must NEVER directly calculate raw indicators if the application already has the calculation.

Instead send structured context:

symbol
price
timestamp
data status
timeframe scores
trend state
market structure
market regime
relative strength
sector alignment
volume
momentum
support/resistance
trend transition
historical signal statistics

Ask AI to:

- summarize trend
- explain why
- identify supporting evidence
- identify conflicts
- identify risks
- explain timeframe disagreement
- summarize recent transition

AI output must be structured.

Example:

{
  summary,
  trend,
  confidence,
  supporting_factors,
  risk_factors,
  timeframe_conflicts,
  key_levels
}

Validate the output.

Never allow AI output to overwrite quantitative truth.

If insufficient data:

return an uncertainty response.

Do not make AI issue trade orders.

Then STOP.
