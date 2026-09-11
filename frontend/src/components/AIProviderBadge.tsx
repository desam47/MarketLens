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
 * Deliberately does NOT reflect "which provider actually answered the
 * last request" (that can differ from config.provider when the primary
 * is down and a fallback served it instead) — that per-call attribution
 * already lives in AIAnalysisPanel's own badge, next to the specific
 * analysis it belongs to. This one is page-level: "what's configured to
 * run," not "what just ran."
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

  return (
    <span className="ai-provider-tag" title={`Configured provider chain${fallbackNote}`}>
      🤖 {providerLabel(config.provider)} · {config.model}
    </span>
  );
}

export default AIProviderBadge;
