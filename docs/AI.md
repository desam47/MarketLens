# AI Integration

AI features are optional and disabled by default. When enabled, MarketLens uses OpenAI's API to generate narrative insights from quantitative signals.

## Configuration

```bash
AI_ENABLED=true
AI_API_KEY=sk-...          # OpenAI API key
AI_PROVIDER=openai         # Currently only openai is supported
AI_MODEL=gpt-4o-mini        # Model to use (default: gpt-4o-mini)
AI_MAX_TOKENS=1000         # Max response tokens
AI_TEMPERATURE=0.7          # Response variability (0.0–2.0)
```

## Features

### Strategy Summaries

When `AI_ENABLED=true`, the `/api/strategy/{symbol}` endpoint includes an optional `ai_summary` field generated from the strategy's signals, trend, and regime data. The summary is generated via a structured prompt that includes:

- Current trend direction and confidence
- Active regime classification
- Signal strength for each timeframe
- Position recommendation (buy/sell/hold)

### Structured Output

AI responses are constrained to structured formats to minimize hallucination risk:

```python
class AIStrategySummary(BaseModel):
    headline: str          # One-sentence market summary
    signals: List[str]    # Key quantitative signals driving the recommendation
    recommendation: Literal["buy", "sell", "hold"]
    confidence: int        # 0–100 confidence score
    risks: List[str]       # Notable risk factors
    timeframe: str         # Recommended holding period
```

The model is prompted to only reference data provided in the request — no external data, no speculative claims.

### Hallucination Safeguards

1. **No external data** — The prompt only includes data available in the request. The model cannot invent price data or fundamentals.
2. **Constrained format** — Response is parsed as `AIStrategySummary`. Malformed responses fall back to a generic "AI summary unavailable" message.
3. **No quantitative claims** — The prompt instructs the model to only describe direction and regime, not produce exact price targets.
4. **Timeout and retry** — AI calls have a 10-second timeout and 1 retry with exponential backoff. Failures are logged but do not block the strategy endpoint from returning raw signals.

### Provider Failure Handling

If the AI provider returns an error (rate limit, auth failure, timeout), the strategy endpoint still returns the full quantitative result. The `ai_summary` field is set to `None` and a warning is logged:

```json
{
  "symbol": "AAPL",
  "recommendation": "buy",
  "ai_summary": null,
  "ai_error": "Rate limit exceeded"
}
```

## Security

- **API key storage** — The `AI_API_KEY` environment variable is read server-side only. It is never sent to the frontend or logged.
- **No user data sent to AI** — The prompt includes only symbol, timeframe, and quantitative signals — no user identity, no watchlist contents.
- **Cost controls** — `AI_MAX_TOKENS=1000` limits response size. `AI_TEMPERATURE=0.7` provides consistent, factual responses.

## Cost Estimation

With `gpt-4o-mini` at ~$0.15/1M input tokens and ~$0.60/1M output tokens, a single strategy summary (~500 input tokens, ~100 output tokens) costs approximately $0.00015. At 100 symbols/hour, that's ~$0.015/hour.

With `gpt-4o`, costs are ~10× higher. Consider `gpt-4o-mini` for production cost efficiency.
