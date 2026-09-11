/**
 * AIProviderBadge — small header pill showing which AI provider/model
 * is currently configured to answer AI Hub requests.
 *
 * Self-contained: fetches its own data (GET /api/ai/config), same
 * template as DigestCard/AlertsCard. Polls every 10s so it reflects a
 * live change (someone flips the AI on/off toggle, or the config is
 * reloaded) without a page refresh — safe_config() is a pure in-memory
 * read (no provider I/O), unlike GET /api/ai/status which makes real
 * health-check HTTP calls, so this is cheap enough to poll that often.
 *
 * Shows config.last_provider/last_model when available — the actual
 * provider/model that answered the most recent successful call
 * anywhere in the app (analysis, digest, templates), same value
 * AIAnalysisPanel's own per-call badge shows. config.model can be a
 * gateway-side alias ("static-best-free") that only resolves to a real
 * name ("openai/gpt-oss-120b") once a request is actually made, so
 * last_model is preferred whenever it's known; falls back to the raw
 * configured provider/model before the first successful call since
 * server start (last_provider/last_model are both null then).
 */
import React, { useEffect, useState } from 'react';
import api, { AIConfig } from '../services/api';

const POLL_MS = 10_000;

// Same map AIAnalysisPanel.tsx uses for provider ids — kept in sync
// deliberately rather than importing across files for one small map.
function providerLabel(provider: string): string {
  const map: Record<string, string> = {
    ollama: 'Ollama',
    lm_studio: 'LM Studio',
    openai: 'OpenAI',
    openai_compatible: 'Custom gateway',
    openrouter: 'OpenRouter',
    anthropic: 'Anthropic',
    disabled: 'AI disabled',
    none: 'AI unavailable',
    unknown: 'AI',
  };
  return map[provider] || provider;
}

export function AIProviderBadge() {
  const [config, setConfig] = useState<AIConfig | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchConfig = () => {
      api.getAIConfig()
        .then(cfg => { if (!cancelled) setConfig(cfg); })
        .catch(() => { /* config endpoint may be down; keep showing the last-known value */ });
    };
    fetchConfig();
    const interval = setInterval(fetchConfig, POLL_MS);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  if (!config) return null; // avoid a layout flash before the first fetch resolves

  if (!config.enabled) {
    return (
      <span className="ai-provider-tag" title="Enable AI from the Analysis section below">
        🤖 AI disabled
      </span>
    );
  }

  const fallbackNote = config.fallback_providers.length > 0
    ? ` (fallback: ${config.fallback_providers.map(providerLabel).join(', ')})`
    : '';
  const resolved = config.last_provider !== null && config.last_model !== null;
  const provider = resolved ? config.last_provider! : config.provider;
  const model = resolved ? config.last_model! : config.model;
  const title = resolved
    ? `Actually answered by ${providerLabel(provider)}, model: ${model}`
    : `Configured provider chain${fallbackNote} — not yet resolved (no successful call since server start)`;

  return (
    <span className="ai-provider-tag" title={title}>
      🤖 {providerLabel(provider)} · {model}
    </span>
  );
}

export default AIProviderBadge;
