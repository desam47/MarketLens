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
import React, {
  forwardRef,
  useEffect,
  useState,
  useCallback,
  useImperativeHandle,
  useRef,
} from 'react';
import api, { AIAnalysisResult, AIConfig, AIJobStatusResponse } from '../services/api';
import { DEFAULT_TIMEFRAME } from '../utils/timeframeUtils';
import { highlightMessage } from '../utils/textHighlight';
import { MarketDataFreshnessBadge } from './MarketDataFreshnessBadge';
import { formatETDateTime } from './chartMath';

interface AIAnalysisPanelProps {
  symbol: string;
  timeframe?: string;
}

export interface AIAnalysisPanelHandle {
  runBackground: (templateId?: number) => Promise<void>;
  cancelBackground: () => Promise<void>;
  refreshAnalysis: () => void;
}

export const AI_BACKGROUND_POLL_MS = 2000;
export const AI_BACKGROUND_TIMEOUT_MS = 10 * 60 * 1000;

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

function labelValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.replace(/_/g, ' ') : null;
}

function recordNumber(record: Record<string, unknown> | undefined, key: string): number | null {
  const value = record?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function recordText(record: Record<string, unknown> | undefined, key: string): string | null {
  const value = record?.[key];
  return typeof value === 'string' && value.trim() ? value : null;
}

export const AIAnalysisPanel = forwardRef(function AIAnalysisPanel(
  { symbol, timeframe = DEFAULT_TIMEFRAME }: AIAnalysisPanelProps,
  ref: React.ForwardedRef<AIAnalysisPanelHandle>,
) {
    const [analysis, setAnalysis] = useState<AIAnalysisResult | null>(null);
    const [config, setConfig] = useState<AIConfig | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [hasRun, setHasRun] = useState(false);
    const [toggling, setToggling] = useState(false);
    const [backgroundJobId, setBackgroundJobId] = useState<string | null>(null);
    const [backgroundStatus, setBackgroundStatus] = useState<string | null>(null);
    const [backgroundError, setBackgroundError] = useState<string | null>(null);
    const [trackingStatus, setTrackingStatus] = useState<'idle' | 'saving' | 'tracked' | 'duplicate' | 'error'>('idle');
    const [trackingError, setTrackingError] = useState<string | null>(null);
    const pollRef = useRef<number | null>(null);
    const backgroundTimeoutRef = useRef<number | null>(null);
    const backgroundRunIdRef = useRef(0);
    const backgroundAbortRef = useRef<AbortController | null>(null);
    const backgroundTemplateIdRef = useRef<number | undefined>(undefined);
    const analysisRequestIdRef = useRef(0);
    const analysisAbortRef = useRef<AbortController | null>(null);

    // Fetch AI config on mount so the user knows whether the feature is available.
    useEffect(() => {
        let cancelled = false;
        api.getAIConfig()
            .then(cfg => { if (!cancelled) setConfig(cfg); })
            .catch(() => { /* config endpoint may be down; non-fatal */ });
        return () => { cancelled = true; };
    }, []);

    const clearBackgroundTimers = useCallback(() => {
        if (pollRef.current !== null) {
            clearInterval(pollRef.current);
            pollRef.current = null;
        }
        if (backgroundTimeoutRef.current !== null) {
            clearTimeout(backgroundTimeoutRef.current);
            backgroundTimeoutRef.current = null;
        }
    }, []);

    const invalidateBackground = useCallback(() => {
        backgroundRunIdRef.current += 1;
        backgroundAbortRef.current?.abort();
        backgroundAbortRef.current = null;
        clearBackgroundTimers();
    }, [clearBackgroundTimers]);

    const runAnalysis = useCallback(async (forceRefresh = false) => {
        if (!symbol) return;
        const requestId = ++analysisRequestIdRef.current;
        analysisAbortRef.current?.abort();
        const controller = new AbortController();
        analysisAbortRef.current = controller;
        setLoading(true);
        setError(null);
        setAnalysis(null);
        setHasRun(false);
        // A synchronous run supersedes any template-backed background job.
        invalidateBackground();
        setBackgroundJobId(null);
        setBackgroundStatus(null);
        setBackgroundError(null);
        setTrackingStatus('idle');
        setTrackingError(null);
        try {
            const result = await api.analyzeSymbol(symbol, timeframe, {
                force_refresh: forceRefresh,
                signal: controller.signal,
            });
            if (requestId !== analysisRequestIdRef.current) return;
            setAnalysis(result);
            setHasRun(true);
        } catch (e: any) {
            if (requestId !== analysisRequestIdRef.current || e?.name === 'AbortError') return;
            setError(e.message || 'Analysis failed');
            setHasRun(true);
        } finally {
            if (requestId === analysisRequestIdRef.current) {
                setLoading(false);
                analysisAbortRef.current = null;
            }
        }
    }, [invalidateBackground, symbol, timeframe]);

    // ── Background job runner ─────────────────────────────────────────────

    const runBackground = useCallback(async (templateId?: number) => {
        if (!symbol) return;
        invalidateBackground();
        const runId = backgroundRunIdRef.current;
        const controller = new AbortController();
        backgroundAbortRef.current = controller;
        backgroundTemplateIdRef.current = templateId;
        setBackgroundJobId(null);
        setBackgroundError(null);
        setBackgroundStatus('queued');
        try {
            const { job_id } = await api.enqueueAIJob({
                symbol,
                timeframe,
                template_id: templateId,
            }, controller.signal);
            if (runId !== backgroundRunIdRef.current) return;
            setBackgroundJobId(job_id);

            const finish = () => {
                clearBackgroundTimers();
                // Invalidate any poll request that was already in flight so
                // a late response cannot resurrect a terminal state.
                backgroundRunIdRef.current += 1;
                backgroundAbortRef.current?.abort();
                backgroundAbortRef.current = null;
            };

            const poll = async () => {
                if (runId !== backgroundRunIdRef.current) return;
                try {
                    const status: AIJobStatusResponse = await api.getAIJob(job_id, controller.signal);
                    if (runId !== backgroundRunIdRef.current) return;
                    setBackgroundStatus(status.status);
                    if (status.status === 'finished') {
                        setBackgroundError(null);
                        if (status.result) {
                            setAnalysis(status.result as AIAnalysisResult);
                            setHasRun(true);
                            setBackgroundStatus('done');
                        } else {
                            setBackgroundError('Background analysis finished without a result.');
                            setBackgroundStatus('failed');
                        }
                        finish();
                    } else if (status.status === 'failed') {
                        setBackgroundError('Background analysis failed. Retry or run it synchronously.');
                        setBackgroundStatus('failed');
                        finish();
                    } else if (status.status === 'cancelled') {
                        setBackgroundError('Background analysis was cancelled.');
                        setBackgroundStatus('cancelled');
                        finish();
                    }
                } catch (e: any) {
                    if (runId !== backgroundRunIdRef.current || e?.name === 'AbortError') return;
                    // A transient polling failure is non-terminal; the timeout
                    // below still gives the user a deterministic recovery path.
                    setBackgroundError('Unable to check the background job; retrying…');
                }
            };

            void poll();
            pollRef.current = window.setInterval(() => { void poll(); }, AI_BACKGROUND_POLL_MS);
            backgroundTimeoutRef.current = window.setTimeout(() => {
                if (runId !== backgroundRunIdRef.current) return;
                backgroundAbortRef.current?.abort();
                finish();
                setBackgroundStatus('timed_out');
                setBackgroundError('Background analysis timed out. Retry or run it synchronously.');
            }, AI_BACKGROUND_TIMEOUT_MS);
        } catch (e: any) {
            if (runId !== backgroundRunIdRef.current || e?.name === 'AbortError') return;
            setBackgroundError('Unable to queue background analysis. Retry or run it synchronously.');
            setBackgroundStatus('failed');
            backgroundAbortRef.current = null;
        }
    }, [clearBackgroundTimers, invalidateBackground, symbol, timeframe]);

    const cancelBackground = useCallback(async () => {
        const jobId = backgroundJobId;
        invalidateBackground();
        setBackgroundJobId(null);
        setBackgroundStatus('cancelled');
        setBackgroundError('Background analysis was cancelled.');
        if (!jobId) return;
        try {
            await api.cancelAIJob(jobId);
        } catch (_) {
            // The local cancellation still stops polling even if the worker
            // has already completed or the queue is temporarily unavailable.
        }
    }, [backgroundJobId, invalidateBackground]);

    useImperativeHandle(ref, () => ({
        runBackground,
        cancelBackground,
        refreshAnalysis: () => { void runAnalysis(true); },
    }), [cancelBackground, runAnalysis, runBackground]);

  const trackSetup = useCallback(async () => {
    const plan = analysis?.trade_plan;
    if (!plan || plan.recommendation === 'hold' || plan.recommendation === 'avoid') return;
    if (analysis.trade_plan_validation?.status !== 'verified') return;
    if (!window.confirm(`Track this validated ${plan.recommendation.toUpperCase()} setup for ${analysis.symbol || symbol}?`)) {
      return;
    }
    setTrackingStatus('saving');
    setTrackingError(null);
    try {
      const result = await api.trackTradePlan(analysis.symbol || symbol, analysis.timeframe || timeframe, plan);
      setTrackingStatus(result.duplicate ? 'duplicate' : 'tracked');
    } catch (e: any) {
      setTrackingStatus('error');
      setTrackingError(e.message || 'Unable to track this setup.');
    }
  }, [analysis, symbol, timeframe]);

  // Analysis is intentionally user-triggered. Changing symbol/timeframe
  // clears the previous result and cancels work, but never invokes a provider
  // or records a setup just because the trader is browsing.
  useEffect(() => {
    analysisRequestIdRef.current += 1;
    analysisAbortRef.current?.abort();
    analysisAbortRef.current = null;
    invalidateBackground();
    setAnalysis(null);
    setLoading(false);
    setHasRun(false);
    setError(null);
    setTrackingStatus('idle');
    setTrackingError(null);
  }, [invalidateBackground, symbol, timeframe]);

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
      analysisRequestIdRef.current += 1;
      analysisAbortRef.current?.abort();
      analysisAbortRef.current = null;
      backgroundRunIdRef.current += 1;
      backgroundAbortRef.current?.abort();
      backgroundAbortRef.current = null;
      clearBackgroundTimers();
    };
  }, [clearBackgroundTimers]);

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
  const scoreEntries = analysis ? Object.entries(analysis.timeframe_scores || {}) : [];
  const regimeLabel = analysis ? labelValue(analysis.market_regime?.regime) : null;
  const trackRecord = analysis?.track_record;
  const correlation = analysis?.correlation_context;
  const trackSample = recordNumber(trackRecord, 'sample_size');
  const trackWinRate = recordNumber(trackRecord, 'win_rate');
  const hasTrackRecord = !!trackRecord && Object.keys(trackRecord).length > 0;
  const peers = correlation && Array.isArray(correlation.peers)
    ? correlation.peers.filter((peer): peer is Record<string, unknown> => (
      !!peer && typeof peer === 'object' && !Array.isArray(peer)
    ))
    : [];
  const peerCount = recordNumber(correlation, 'peer_count');
  const alignedPeers = recordNumber(correlation, 'aligned');
  const opposedPeers = recordNumber(correlation, 'opposed');
  const sameSectorPeers = recordNumber(correlation, 'same_sector_count');
  const primarySector = recordText(correlation, 'primary_sector');
  const hasCorrelation = !!correlation && (peers.length > 0 || peerCount !== null);

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
          <span className="ai-background-actions">
            <span className="btn btn-small btn-loading" aria-live="polite">
              ⏳ {backgroundStatus === 'queued' ? 'Queued…' : 'Running…'}
            </span>
            <button
              className="btn btn-small"
              onClick={() => { void cancelBackground(); }}
              title="Stop waiting for this background analysis"
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            className={`btn btn-small ${loading ? 'btn-loading' : ''}`}
            onClick={() => { void runAnalysis(hasRun); }}
            disabled={loading || aiDisabled}
            title={aiDisabled ? 'AI is disabled' : 'Refresh market data and rerun AI analysis'}
          >
            {loading ? '⟳ Analyzing…' : hasRun ? '↻ Refresh & rerun' : '✨ Analyze'}
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
                <p>⚠️ {backgroundError}</p>
                {(backgroundStatus === 'failed' || backgroundStatus === 'timed_out' || backgroundStatus === 'cancelled') && (
                  <button
                    className="btn btn-small data-state-retry"
                    onClick={() => { void runBackground(backgroundTemplateIdRef.current); }}
                  >
                    Retry background analysis
                  </button>
                )}
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
          <button className="btn btn-small data-state-retry" onClick={() => runAnalysis(true)}>Retry</button>
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

          <section className="ai-evidence" aria-label="Analysis market-data evidence">
            <div className="ai-evidence-heading">
              <strong>Market-data evidence</strong>
              {analysis.cache_status === 'cached' && <span className="ai-cache-tag">Cached analysis</span>}
            </div>
            <div className="ai-evidence-primary">
              <span><b>{analysis.symbol || symbol}</b> · {analysis.timeframe || timeframe}</span>
              {analysis.price != null && <span>Price {money(analysis.price)}</span>}
              <MarketDataFreshnessBadge
                dataStatus={analysis.data_status}
                timestamp={analysis.source_timestamp}
                ageSeconds={analysis.data_age_seconds}
                provider={analysis.market_data_provider}
                marketSession={analysis.market_session === 'unknown' ? null : analysis.market_session}
                staleAfterSeconds={60}
                showAge
              />
            </div>
            <div className="ai-evidence-meta">
              {analysis.source_timestamp
                ? <>As of {formatETDateTime(analysis.source_timestamp)} ET · {analysis.market_session?.replace('_', ' ') || 'session unavailable'}</>
                : <>Source timestamp unavailable · {analysis.market_session?.replace('_', ' ') || 'session unavailable'}</>}
            </div>
          </section>

          <div className="ai-summary">
            <p>{highlightMessage(analysis.summary, [symbol])}</p>
          </div>

          {(regimeLabel || scoreEntries.length > 0 || hasTrackRecord || hasCorrelation) && (
            <section className="ai-section ai-quant-context" aria-label="Quantitative context">
              <h3>📊 Quantitative Context</h3>
              {regimeLabel && <p className="ai-context-regime">Market regime: <b>{regimeLabel}</b></p>}
              {scoreEntries.length > 0 && (
                <div className="ai-timeframe-scores">
                  {scoreEntries.map(([scoreTimeframe, score]) => (
                    <span className="ai-timeframe-score" key={scoreTimeframe}>
                      <b>{scoreTimeframe}</b> · {score.direction || 'unknown'}
                      {score.strength ? ` · ${score.strength}` : ''}
                      {typeof score.confidence === 'number' ? ` · ${Math.round(score.confidence * 100)}%` : ''}
                    </span>
                  ))}
                  </div>
              )}
              {hasTrackRecord && (
                <div className="ai-context-subsection" aria-label="AI trade-plan track record">
                  <h4>Track record</h4>
                  <p>
                    {trackSample !== null ? `${trackSample} resolved call${trackSample === 1 ? '' : 's'}` : 'Historical calls'}
                    {trackWinRate !== null ? ` · ${Math.round(trackWinRate * 100)}% win rate` : ''}
                  </p>
                  <p className="info-text">
                    {recordNumber(trackRecord, 'all_time_win_count') ?? 0} wins ·{' '}
                    {recordNumber(trackRecord, 'all_time_loss_count') ?? 0} losses ·{' '}
                    {recordNumber(trackRecord, 'all_time_open_count') ?? 0} open ·{' '}
                    {recordNumber(trackRecord, 'all_time_expired_count') ?? 0} expired
                  </p>
                </div>
              )}
              {hasCorrelation && (
                <div className="ai-context-subsection" aria-label="Peer context">
                  <h4>Peer context</h4>
                  <p>
                    {peerCount !== null ? `${peerCount} peer${peerCount === 1 ? '' : 's'}` : `${peers.length} peers`}
                    {alignedPeers !== null ? ` · ${alignedPeers} aligned` : ''}
                    {opposedPeers !== null ? ` · ${opposedPeers} opposed` : ''}
                    {sameSectorPeers !== null ? ` · ${sameSectorPeers} same sector` : ''}
                    {primarySector ? ` · ${primarySector}` : ''}
                  </p>
                  {peers.length > 0 && (
                    <ul className="ai-peer-list">
                      {peers.slice(0, 8).map((peer, index) => {
                        const peerSymbol = recordText(peer, 'symbol');
                        if (!peerSymbol) return null;
                        const direction = labelValue(peer.direction) || 'unknown';
                        const strength = labelValue(peer.strength);
                        return (
                          <li key={`${peerSymbol}-${index}`}>
                            <b>{peerSymbol}</b> · {direction}{strength ? ` · ${strength}` : ''}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              )}
            </section>
          )}

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

              <p className="tp-thesis">{highlightMessage(analysis.trade_plan.thesis, [symbol])}</p>
              <p className="tp-invalidation">
                <b>Invalidation:</b> {highlightMessage(analysis.trade_plan.invalidation, [symbol])}
              </p>
              {analysis.trade_plan_validation?.status === 'verified' && (
                <p className="tp-validation">
                  ✓ Validated against quote {analysis.trade_plan_validation.quote_price != null
                    ? money(analysis.trade_plan_validation.quote_price) : '—'}
                  {analysis.trade_plan_validation.supports?.length
                    ? ` · support ${analysis.trade_plan_validation.supports.map(money).join(', ')}` : ''}
                  {analysis.trade_plan_validation.resistances?.length
                    ? ` · resistance ${analysis.trade_plan_validation.resistances.map(money).join(', ')}` : ''}
                </p>
              )}
              {analysis.trade_plan_validation?.status === 'verified'
                && (analysis.trade_plan.recommendation === 'buy' || analysis.trade_plan.recommendation === 'sell')
                && config?.trade_plan_tracking_enabled && (
                <div className="ai-tracking-action">
                  <button
                    className="btn btn-small"
                    onClick={() => { void trackSetup(); }}
                    disabled={trackingStatus === 'saving' || trackingStatus === 'tracked' || trackingStatus === 'duplicate'}
                  >
                    {trackingStatus === 'saving' ? 'Tracking…'
                      : trackingStatus === 'tracked' ? '✓ Setup tracked'
                        : trackingStatus === 'duplicate' ? '✓ Already tracked'
                          : 'Track this setup'}
                  </button>
                  {trackingError && <span className="ai-tracking-error" role="alert">{trackingError}</span>}
                </div>
              )}
              <p className="tp-disclaimer">
                Research to inform your own decision — not personalized financial advice.
              </p>
            </div>
          )}

          {analysis.trade_plan_validation?.status === 'unavailable' && (
            <div className="ai-plan-unavailable" role="status">
              <b>No validated trade setup.</b> {analysis.trade_plan_validation.reason}
            </div>
          )}

          {analysis.supporting_factors.length > 0 && (
            <div className="ai-section ai-bullish">
              <h3>🟢 Supporting Factors</h3>
              <ul>
                {analysis.supporting_factors.map((f, i) => (
                  <li key={i}>{highlightMessage(f, [symbol])}</li>
                ))}
              </ul>
            </div>
          )}

          {analysis.risk_factors.length > 0 && (
            <div className="ai-section ai-bearish">
              <h3>🔴 Risk Factors</h3>
              <ul>
                {analysis.risk_factors.map((f, i) => (
                  <li key={i}>{highlightMessage(f, [symbol])}</li>
                ))}
              </ul>
            </div>
          )}

          {analysis.timeframe_conflicts.length > 0 && (
            <div className="ai-section ai-conflict">
              <h3>⚖️ Timeframe Conflicts</h3>
              <ul>
                {analysis.timeframe_conflicts.map((c, i) => (
                  <li key={i}>{highlightMessage(c, [symbol])}</li>
                ))}
              </ul>
            </div>
          )}

          {analysis.key_levels.length > 0 && (
            <div className="ai-section ai-levels">
              <h3>🎯 Key Levels</h3>
              <ul>
                {analysis.key_levels.map((l, i) => (
                  <li key={i}>{highlightMessage(l, [symbol])}</li>
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
});

export default AIAnalysisPanel;
