# PHASE 13 — HISTORICAL SIGNAL RECORDING

Create a historical signal storage system.

Store every meaningful trend snapshot.

Fields should include:

symbol
timestamp
timeframe
price
trend_score
trend_state
strength
market_regime
relative_strength
sector_alignment
volume_state
momentum
structure
confidence_inputs
strategy_version
data_quality

Create forward-outcome tracking.

For each historical signal calculate later:

5-bar return
10-bar return
20-bar return

and:

maximum favorable excursion
maximum adverse excursion

Do NOT calculate future outcomes until the relevant future data actually exists.

Avoid look-ahead bias.

Create research APIs.

Create a historical signal viewer.

Then STOP.
