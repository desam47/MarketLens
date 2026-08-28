# AI Market Analysis

AI features are **disabled by default** and require no external service to run the quantitative engine. When enabled, an LLM provides a natural-language summary of the market state described by the existing trend, regime, and signal data — it does not compute indicators, fetch prices, or issue trade recommendations.

## Providers

Five provider families are supported. The active provider and its fallback chain are configured via environment variables:

```bash
AI_ENABLED=true
AI_PROVIDER=openai           # primary provider name
AI_FALLBACK_PROVIDERS=ollama,anthropic  # fallback chain (comma-separated)
AI_MODEL=gpt-4o-mini        # model name (provider-specific)
AI_BASE_URL=                 # required for openai_compatible; optional for others
AI_API_KEY=                 # required for openai / anthropic / openrouter
AI_TIMEOUT=30               # request timeout in seconds (default 30)
AI_HEALTH_CHECK_TIMEOUT=2   # health-check timeout in seconds (default 2)
AI_MAX_TOKENS=1000          # default max_tokens (can be overridden per-request)
AI_TEMPERATURE=0.7          # default temperature (can be overridden per-request)
```

| Provider | `AI_PROVIDER` value | API key needed? | `AI_BASE_URL` needed? | Notes |
|---|---|---|---|---|
| OpenAI | `openai` | Yes | No | Default model: `gpt-4o-mini` |
| Anthropic | `anthropic` | Yes | No | Uses Anthropic Messages API; default model: `claude-3-5-sonnet-latest` |
| OpenRouter | `openrouter` | Yes | No | Routes to any OpenRouter model; default model: `openai/gpt-4o-mini` |
| Ollama | `ollama` | No | No | Local; default URL `http://localhost:11434/v1`; default model: `llama3.2` |
| LM Studio | `lm_studio` | No | No | Local; default URL `http://localhost:1234/v1`; default model: `local-model` |
| Generic OpenAI-compatible | `openai_compatible` | Optional | **Yes** | Any server that speaks `/v1/chat/completions` |

The `AIManager` walks the fallback chain in order: if the primary provider is unavailable (connection error, 4xx, timeout), it tries the next one, and so on. If every provider fails, `analyze_symbol()` returns an `UncertaintyResponse` — it never raises an exception.

## Architecture

```
POST /api/ai/analyze
  └── analyze_symbol(symbol, timeframe)
        ├── build_context(symbol, timeframe)   # quant state → AnalysisContext dict
        │     ├── MarketRegimeEngine.get_current_regime()  for this symbol
        │     ├── MultiTimeframeEngine.build_snapshot()   per timeframe
        │     ├── MarketContextEngine.get_current()        market-wide regime
        │     ├── RelativeStrengthEngine.compute()          vs SPY/QQQ/sector ETF
        │     ├── SectorEngine.compute()                   sector alignment
        │     ├── SupportResistanceEngine.detect()         support/resistance levels
        │     ├── TrendTransitionEngine.detect()           recent transitions
        │     └── SignalRecorder.get_summary_stats()       historical signal stats
        ├── ai_manager.complete(prompt, system)           # calls provider chain
        └── parse_ai_reply(raw_text) → AnalysisResponse   # Pydantic validation
```

The AI receives a structured `AnalysisContext` dict (all numbers already computed by the engine). The system prompt tells the model **never** to compute indicators, prices, percentages, or targets — only to describe the data it is given.

## Response Schema

### Success: `AnalysisResponse`

```json
{
  "summary": "AAPL is in a confirmed uptrend with strong medium-term alignment...",
  "trend": "bullish",
  "confidence": 0.78,
  "supporting_factors": [
    "Price above all key moving averages",
    "ADX at 32 confirms a trending market",
    "Sector alignment is positive"
  ],
  "risk_factors": [
    "RSI at 68 suggests limited near-term upside",
    "Market regime is transitioning to neutral"
  ],
  "timeframe_conflicts": [
    "15m shows bearish divergence while daily remains bullish"
  ],
  "key_levels": [
    "Support: 185.50 (50-day SMA)",
    "Resistance: 192.80 (prior high)"
  ]
}
```

`trend` is one of: `bullish | bearish | neutral | mixed | uncertain`
`confidence` is a float in `0.0–1.0`
All list fields are capped at 10 items; empty strings are stripped.

### Failure: `UncertaintyResponse`

When AI is disabled, no quant data is available, all providers are down, or the reply cannot be parsed, the endpoint returns an `UncertaintyResponse` (same JSON shape, `trend="uncertain"`, `confidence=0.0`). The HTTP status is always 200 — callers handle the `is_uncertain` flag.

```json
{
  "is_uncertain": true,
  "summary": "Insufficient data to generate an analysis for AAPL.",
  "trend": "uncertain",
  "confidence": 0.0,
  "supporting_factors": []
}
```

## Hallucination Safeguards

1. **No recomputation.** The system prompt explicitly forbids the model from claiming to compute indicators, prices, or percentages. The engine has already done this work.
2. **Structured output with Pydantic.** `AnalysisResponse.model_validate()` rejects any reply that is missing required fields or has out-of-range values. A `confidence: 1.5` would raise a validation error, triggering an `UncertaintyResponse` fallback.
3. **JSON extraction guard.** The reply is parsed from a fenced code block (` ```json ... ``` `) or the first balanced `{}` in the text. Garbled replies fall back to `UncertaintyResponse`.
4. **AI never overwrites quant truth.** The `AnalysisResponse` does not include the engine's score. Disagreements between the AI narrative and the quantitative signal are logged but do not block the response.
5. **Look-ahead guard.** `build_context()` reads only current engine state — no future bars, no look-ahead possible.

## Security

- **API key handling.** Keys are read from environment variables server-side only. `GET /api/ai/config` returns a frontend-safe payload with `api_key` stripped.
- **No user data sent to the AI.** The prompt contains only: symbol, timeframe, trend scores, regime, and signal counts — no watchlist contents, no user identity.
- **Provider isolation.** A provider failure causes a fallback, not a crash. A bad model name or wrong API key for the primary provider falls through to the fallback.

## Cost Estimation

With OpenAI `gpt-4o-mini` at ~$0.15/1M input + $0.60/1M output tokens, a typical `analyze_symbol` request (~500 input, ~100 output) costs **~$0.00015** per call. Ollama and LM Studio are free when running locally.

## API Reference

### `POST /api/ai/analyze`

Analyze a symbol. Requires at least one completed scan result in the scanner cache.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `symbol` | string (1–10 chars) | required | Ticker symbol, auto upper-cased |
| `timeframe` | string | `1d` | `1m`, `5m`, `15m`, `1h`, `4h`, or `1d` |
| `max_tokens` | int (100–8192) | `AI_MAX_TOKENS` | Maximum output tokens |
| `temperature` | float (0.0–2.0) | `AI_TEMPERATURE` | Output randomness |

**Response:** `AnalyzeResponse` (wraps `AnalysisResponse | UncertaintyResponse`)

### `GET /api/ai/status`

Returns per-provider health. Does not check AI availability.

```json
{
  "providers": [
    { "name": "openai", "healthy": true, "is_primary": true },
    { "name": "ollama", "healthy": false, "is_primary": false, "error": "Connection refused" }
  ]
}
```

### `GET /api/ai/config`

Returns the current AI configuration (API key redacted).

```json
{
  "enabled": true,
  "provider": "openai",
  "model": "gpt-4o-mini",
  "fallback_providers": ["ollama"]
}
```
