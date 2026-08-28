# PHASE 15 — OPTIONAL AI PROVIDER SYSTEM

Implement AI as an optional abstraction.

Create:

AIProvider
AIManager

Support provider adapters for:

Ollama
LM Studio / OpenAI-compatible
OpenAI-compatible APIs
OpenAI
Anthropic
OpenRouter

Do not require any AI provider.

Default:

AI_ENABLED=false

Configuration must support:

provider
model
base_url
API key through environment variables
timeout
temperature
max tokens

Never expose API keys to the frontend.

The quantitative engine must continue working if AI is completely disabled.

Implement provider health checks.

Implement fallback provider support.

Example:

Local AI
→ unavailable
→ fallback cloud AI
→ unavailable
→ quantitative-only mode

Add tests.

Then STOP.
