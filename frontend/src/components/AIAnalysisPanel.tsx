/**
 * AIAnalysisPanel — Phase 16 AI symbol analysis card.
 *
 * Renders the structured output of /api/ai/analyze as a card on the
 * Symbol page. When AI is disabled, the panel shows a neutral info state
 * explaining how to enable it. The card surfaces:
 *   - Trend direction + confidence badge
 *   - AI summary
 *   - Supporting factors vs. risk factors
 *   - Timeframe conflicts
 *   - Key levels
 *   - Provider/model attribution
 */
import React, { useEffect, useState, useCallback, useRef } from 'react';
import api, { AIAnalysisResult, AIConfig, AIJobStatusResponse } from '../services/api';
import { DEFAULT_TIMEFRAME } from '../utils/timeframeUtils';

interface AIAnalysisPanelProps {
  symbol: string;
  timeframe?: string;
}

function trendColor(trend: string): string {
  if (trend === 'bullish') return '#10b981';
  if (trend === 'bearish') return '#ef4444';
  return '#9ca3af';
}

function trendIcon(trend: string): string {
  if (trend === 'bullish') return '🐂';
  if (trend === 'bearish') return '🐻';
  if (trend === 'uncertain') return '❓';
  return '➡️';
}

function confidenceLabel(c: number): string {
  return `${Math.round(c * 100)}%`;
}

function recColor(rec: string): string {
  if (rec === 'buy') return '#10b981';
  if (rec === 'sell') return '#ef4444';
  return '#9ca3af'; // hold / avoid
}

const money = (n: number): string =>
  '$' + n.toLocaleString(undefined, { maximumFractionDigits: 2 });

function providerLabel(provider: string): string {
  // Translate internal provider ids to user-visible names.
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

export function AIAnalysisPanel({ symbol, timeframe = DEFAULT_TIMEFRAME }: AIAnalysisPanelProps) {
  const [analysis, setAnalysis] = useState<AIAnalysisResult | null>(null);
  const [config, setConfig] = useState<AIConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRun, setHasRun] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [backgroundJobId, setBackgroundJobId] = useState<string | null>(null);
  const [backgroundStatus, setBackgroundStatus] = useState<string | null>(null);
  const [backgroundError, setBackgroundError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  // Fetch AI config on mount so the user knows whether the feature is available.
  useEffect(() => {
    let cancelled = false;
    api.getAIConfig()
      .then(cfg => { if (!cancelled) setConfig(cfg); })
      .catch(() => { /* config endpoint may be down; non-fatal */ });
    return () => { cancelled = true; };
  }, []);

  const runAnalysis = useCallback(async () => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    // Clear any stale background job state
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setBackgroundJobId(null);
    setBackgroundStatus(null);
    setBackgroundError(null);
    try {
      const result = await api.analyzeSymbol(symbol, timeframe);
      setAnalysis(result);
      setHasRun(true);
    } catch (e: any) {
      setError(e.message || 'Analysis failed');
      setHasRun(true);
    } finally {
      setLoading(false);
    }
  }, [symbol, timeframe]);

  // ── Background job runner ─────────────────────────────────────────────

  const runBackground = useCallback(async (templateId?: number) => {
    if (!symbol) return;
    setBackgroundError(null);
    setBackgroundStatus('queued');
    try {
      const { job_id } = await api.enqueueAIJob({
        symbol,
        timeframe,
        template_id: templateId,
      });
      setBackgroundJobId(job_id);
      // Start polling
      const poll = async () => {
        try {
          const status: AIJobStatusResponse = await api.getAIJob(job_id);
          setBackgroundStatus(status.status);
          if (status.status === 'finished' && status.result) {
            setAnalysis(status.result as AIAnalysisResult);
            setHasRun(true);
            setBackgroundStatus('done');
            clearInterval(pollRef.current!);
            pollRef.current = null;
          } else if (status.status === 'failed') {
            setBackgroundError(status.error || 'Job failed');
            setBackgroundStatus('failed');
            clearInterval(pollRef.current!);
            pollRef.current = null;
          }
        } catch (_) {
          // Keep polling; worker may not have started yet
        }
      };
      pollRef.current = window.setInterval(poll, 2000) as unknown as number;
    } catch (e: any) {
      setBackgroundError(e?.message || 'Failed to enqueue job');
      setBackgroundStatus(null);
    }
  }, [symbol, timeframe]);

  // Expose runBackground via a data attribute so AITemplatesPanel can trigger it.
  // (Simple cross-component communication without context or prop-drilling.)
  useEffect(() => {
    const el = document.getElementById('ai-analysis-panel');
    if (el) {
      (el as any).runBackground = runBackground;
    }
    return () => {
      if (el) delete (el as any).runBackground;
    };
  }, [runBackground]);

  // Auto-run on mount when AI is enabled and the symbol changes.
  useEffect(() => {
    if (config?.enabled) {
      runAnalysis();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, timeframe, config?.enabled]);

  const aiDisabled = config ? !config.enabled : undefined;

  const toggleAI = useCallback(async () => {
    if (!config) return;
    setToggling(true);
    setError(null);
    try {
      const updated = await api.setAIEnabled(!config.enabled);
      setConfig(updated);
      // Clear stale analysis when AI is turned off.
      if (!updated.enabled) {
        setAnalysis(null);
        setHasRun(false);
      }
    } catch (e: any) {
      setError(e.message || 'Failed to update AI setting');
    } finally {
      setToggling(false);
    }
  }, [config]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  // The primary provider is unhealthy/rate-limited right now if the
  // answer actually came from something other than what's configured
  // as primary (config.provider). Found live 2026-09-10: the backend
  // used to always report the configured primary regardless of who
  // really answered — fixed there; this is the visible half, so a
  // fallback-served analysis doesn't quietly look identical to a
  // primary-served one.
  const usingFallback = !!(
    analysis && config &&
    analysis.provider !== config.provider &&
    analysis.provider !== 'disabled' &&
    analysis.provider !== 'none'
  );

  return (
    <div id="ai-analysis-panel" className="card ai-analysis-card">
      <div className="ai-header-row">
        <h2>
          <span className="ai-icon">🤖</span> AI Analysis
          {analysis && (
            <span
              className={`ai-provider-tag ${usingFallback ? 'ai-provider-fallback' : ''}`}
              title={
                usingFallback
                  ? `Fallback — primary '${providerLabel(config!.provider)}' was unavailable. `
                    + `Answered by ${providerLabel(analysis.provider)}, model: ${analysis.model}`
                  : `model: ${analysis.model}`
              }
            >
              {providerLabel(analysis.provider)}
              {usingFallback && ' ⚠ fallback'}
            </span>
          )}
        </h2>
        {config && (
          <label className="ai-toggle" title="Enable or disable AI at runtime">
            <input
              type="checkbox"
              checked={config.enabled}
              onChange={toggleAI}
              disabled={toggling}
            />
            <span className={`ai-toggle-pill ${config.enabled ? 'on' : 'off'}`}>
              {toggling ? '…' : config.enabled ? 'On' : 'Off'}
            </span>
          </label>
        )}
        {backgroundStatus === 'queued' || backgroundStatus === 'started' ? (
          <button
            className="btn btn-small btn-loading"
            disabled
            title="Background analysis in progress"
          >
            ⏳ {backgroundStatus === 'queued' ? 'Queued…' : 'Running…'}
          </button>
        ) : (
          <button
            className={`btn btn-small ${loading ? 'btn-loading' : ''}`}
            onClick={runAnalysis}
            disabled={loading || aiDisabled}
            title={aiDisabled ? 'AI is disabled' : 'Re-run AI analysis'}
          >
            {loading ? '⟳ Analyzing…' : hasRun ? '↻ Re-run' : '✨ Analyze'}
          </button>
        )}
      </div>

      {backgroundStatus === 'queued' && (
        <div className="ai-loading">
          <p>⏳ Job queued — polling for result…</p>
          <p className="info-text">Poll every 2 seconds until done</p>
        </div>
      )}
      {backgroundStatus === 'started' && (
        <div className="ai-loading">
          <p>⚙️ Analysis in progress…</p>
          <p className="info-text">Still computing indicators and querying the AI model</p>
        </div>
      )}
      {backgroundError && (
        <div className="ai-error">
          <p>⚠️ Background job failed: {backgroundError}</p>
        </div>
      )}

      {aiDisabled && (
        <div className="ai-disabled-info">
          <p>⚠️ AI is disabled.</p>
          <p className="info-text">
            Flip the toggle above to turn it on, or set
            {' '}<code>AI_ENABLED=true</code> in your environment.
            Configure with <code>AI_PROVIDER</code> (e.g. <code>ollama</code>,
            {' '}<code>openai</code>) and <code>AI_MODEL</code>.
          </p>
        </div>
      )}

      {error && (
        <div className="ai-error">
          <p>⚠️ {error}</p>
        </div>
      )}

      {!aiDisabled && !error && analysis && (
        <div className="ai-body">
          <div className="ai-trend-row">
            <div
              className="ai-trend-badge"
              style={{
                backgroundColor: trendColor(analysis.trend),
                color: '#fff',
              }}
              title={`confidence: ${confidenceLabel(analysis.confidence)}`}
            >
              {trendIcon(analysis.trend)} {analysis.trend.toUpperCase()}
            </div>
            <div className="ai-confidence">
              <div className="ai-conf-label">Confidence</div>
              <div className="progress-bar ai-conf-bar">
                <div
                  className="progress-fill"
                  style={{
                    width: `${Math.round(analysis.confidence * 100)}%`,
                    backgroundColor: trendColor(analysis.trend),
                  }}
                />
              </div>
              <div className="ai-conf-value">
                {confidenceLabel(analysis.confidence)}
              </div>
            </div>
            {analysis.is_uncertain && (
              <span className="ai-uncertain-tag" title="AI was unable to produce a confident analysis">
                uncertain
              </span>
            )}
          </div>

          <div className="ai-summary">
            <p>{analysis.summary}</p>
          </div>

          {analysis.trade_plan && (
            <div className="ai-section ai-trade-plan">
              <h3>
                📋 Trade Setup
                <span
                  className="tp-rec"
                  style={{ backgroundColor: recColor(analysis.trade_plan.recommendation) }}
                >
                  {analysis.trade_plan.recommendation.toUpperCase()}
                </span>
                <span className="tp-meta">
                  {analysis.trade_plan.conviction} conviction · {analysis.trade_plan.time_horizon}
                </span>
              </h3>

              {(analysis.trade_plan.recommendation === 'buy'
                || analysis.trade_plan.recommendation === 'sell') && (
                <div className="tp-levels">
                  {analysis.trade_plan.entry_zone_low != null && (
                    <div className="tp-level">
                      <span>Entry</span>
                      <b>
                        {money(analysis.trade_plan.entry_zone_low)}
                        {analysis.trade_plan.entry_zone_high != null
                          && analysis.trade_plan.entry_zone_high !== analysis.trade_plan.entry_zone_low
                          && ` – ${money(analysis.trade_plan.entry_zone_high)}`}
                      </b>
                    </div>
                  )}
                  {analysis.trade_plan.stop_loss != null && (
                    <div className="tp-level">
                      <span>Stop</span><b>{money(analysis.trade_plan.stop_loss)}</b>
                    </div>
                  )}
                  {analysis.trade_plan.targets.length > 0 && (
                    <div className="tp-level">
                      <span>Targets</span>
                      <b>{analysis.trade_plan.targets.map(money).join('  ·  ')}</b>
                    </div>
                  )}
                  {analysis.trade_plan.risk_reward != null && (
                    <div className="tp-level">
                      <span>R : R</span><b>{analysis.trade_plan.risk_reward.toFixed(2)}</b>
                    </div>
                  )}
                </div>
              )}

              <p className="tp-thesis">{analysis.trade_plan.thesis}</p>
              <p className="tp-invalidation">
                <b>Invalidation:</b> {analysis.trade_plan.invalidation}
              </p>
              <p className="tp-disclaimer">
                Research to inform your own decision — not personalized financial advice.
              </p>
            </div>
          )}

          {analysis.supporting_factors.length > 0 && (
            <div className="ai-section ai-bullish">
              <h3>🟢 Supporting Factors</h3>
              <ul>
                {analysis.supporting_factors.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            </div>
          )}

          {analysis.risk_factors.length > 0 && (
            <div className="ai-section ai-bearish">
              <h3>🔴 Risk Factors</h3>
              <ul>
                {analysis.risk_factors.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            </div>
          )}

          {analysis.timeframe_conflicts.length > 0 && (
            <div className="ai-section ai-conflict">
              <h3>⚖️ Timeframe Conflicts</h3>
              <ul>
                {analysis.timeframe_conflicts.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}

          {analysis.key_levels.length > 0 && (
            <div className="ai-section ai-levels">
              <h3>🎯 Key Levels</h3>
              <ul>
                {analysis.key_levels.map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="ai-footer">
            <span className="info-text">
              Model: <code>{analysis.model}</code>
            </span>
          </div>
        </div>
      )}

      {!aiDisabled && !error && !analysis && !loading && (
        <div className="ai-empty">
          <p>Click "Analyze" to run an AI-powered analysis of {symbol}.</p>
        </div>
      )}

      {!aiDisabled && !error && loading && !analysis && (
        <div className="ai-loading">
          <p>🤖 Analyzing {symbol}…</p>
          <p className="info-text">Gathering indicators and asking the AI model</p>
        </div>
      )}
    </div>
  );
}

export default AIAnalysisPanel;
