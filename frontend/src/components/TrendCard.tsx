import React, { memo } from 'react';
import { TrendData, TrendChangeHistory, TrendKeyLevels } from '../services/api';
import { formatETDateTime } from './chartMath';

interface TrendCardProps {
  trend: TrendData;
  confluenceRole?: 'input' | 'out_of_scope';
  /** When provided, the card opens the symbol chart at this timeframe (TC-09). */
  onOpenChart?: () => void;
}

const directionIcons: Record<string, string> = {
  uptrend: '↑',
  downtrend: '↓',
  sideways: '→',
  unknown: '?',
};

const directionColors: Record<string, string> = {
  uptrend: '#10b981',
  downtrend: '#ef4444',
  sideways: '#f59e0b',
  unknown: '#9ca3af',
};

const strengthColors: Record<string, string> = {
  weak: '#ef4444',
  moderate: '#f59e0b',
  strong: '#10b981',
  very_strong: '#22c55e',
};

const momentumColors: Record<string, string> = {
  choppy: '#f59e0b',
  developing: '#60a5fa',
  persistent: '#10b981',
};

const timeframeLabels: Record<string, string> = {
  '1m': '1 Min',
  '2m': '2 Min',
  '3m': '3 Min',
  '5m': '5 Min',
  '15m': '15 Min',
  '30m': '30 Min',
  '1h': '1 Hour',
  '4h': '4 Hour',
  '1d': 'Daily',
  '1wk': 'Weekly',
};

const evidenceLabels: Record<string, string> = {
  live: 'Live',
  recent: 'Recent',
  stale: 'Stale',
  closed_session: 'Market closed',
  warming: 'Warming',
  unavailable: 'Unavailable',
};

const classificationLabels: Record<string, string> = {
  strong_bullish: 'Strong bullish',
  bullish: 'Bullish',
  weak_bullish: 'Weak bullish',
  neutral: 'Neutral',
  weak_bearish: 'Weak bearish',
  bearish: 'Bearish',
  strong_bearish: 'Strong bearish',
  no_signal: 'No signal',
};

// The card never recomputes the bucket; the server derives `classification`
// from `score`. This tooltip only documents the fixed boundaries so a trader
// can see how close a score sits to the next classification.
const scoreThresholdTitle =
  'Composite score, -100 to +100. Buckets: +70 Strong bullish, +30 Bullish, +10 Weak bullish, ' +
  '-9 to +9 Neutral, -10 Weak bearish, -30 Bearish, -70 Strong bearish.';

const invalidReasonLabels: Record<string, string> = {
  signal_unavailable: 'Signal unavailable',
  source_timestamp_missing: 'Source timestamp unavailable',
  source_metadata_unavailable: 'Source evidence unavailable',
  bar_forming: 'Waiting for bar close',
  insufficient_warmup: 'More history needed',
  warmup_unknown: 'Warm-up status unavailable',
  source_stale: 'Source is stale',
};

function formatProvider(provider: string | null | undefined): string | null {
  if (!provider || provider === 'unknown') return null;
  if (provider === 'mixed') return 'Mixed sources';
  if (provider === 'aggregated_from_1m') return 'Aggregated from 1m';
  if (provider === 'aggregated_from_1h') return 'Aggregated from 1h';
  if (provider === 'aggregated_from_1d') return 'Aggregated from daily';
  if (provider === 'live_from_1m') return 'Live aggregation from 1m';
  return provider.replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase());
}

function formatChangeHistory(change: TrendChangeHistory | null | undefined): string | null {
  if (!change || !Number.isFinite(change.bars_in_state)) return null;
  const bars = Math.max(1, Math.round(change.bars_in_state));
  const unit = bars === 1 ? 'bar' : 'bars';
  if (change.witnessed_change) {
    if (bars === 1) {
      const prev = change.previous_direction?.replace(/_/g, ' ');
      return prev ? `New (was ${prev})` : 'New this bar';
    }
    return `Held ${bars} ${unit}`;
  }
  // The transition into this state predates the tracked history, so the
  // duration is a lower bound, not an observed run length.
  return `Held ${bars}+ ${unit}`;
}

function formatSignedContribution(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return rounded > 0 ? `+${rounded}` : `${rounded}`;
}

function formatKeyLevels(levels: TrendKeyLevels | undefined): string[] {
  if (!levels) return [];
  const parts: string[] = [];
  if (levels.supertrend) {
    const arrow = levels.supertrend.direction === 'up' ? '↑' : '↓';
    parts.push(`SuperTrend ${arrow} ${levels.supertrend.flip_price}`);
  }
  if (levels.bollinger) {
    parts.push(`Bollinger ${levels.bollinger.lower}–${levels.bollinger.upper}`);
  }
  return parts;
}

function formatSignedScore(score: number | null | undefined): string | null {
  if (score == null || !Number.isFinite(score)) return null;
  const rounded = Math.round(score);
  if (rounded > 0) return `+${rounded}`;
  if (rounded < 0) return `-${Math.abs(rounded)}`;
  return '0';
}

function formatAge(ageSeconds: number | null | undefined): string | null {
  if (ageSeconds == null || !Number.isFinite(ageSeconds)) return null;
  const seconds = Math.max(0, Math.round(ageSeconds));
  if (seconds < 60) return `${seconds}s old`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m old`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h old`;
  return `${Math.round(seconds / 86400)}d old`;
}

function signalExplanation(trend: TrendData): string {
  const timeframe = timeframeLabels[trend.timeframe] || trend.timeframe;
  const direction = trend.direction.replace(/_/g, ' ');
  const isShortHorizon = ['1m', '2m', '3m'].includes(trend.timeframe);
  const strength = trend.strength.replace(/_/g, ' ');
  const momentum = trend.short_horizon_momentum?.replace(/_/g, ' ');
  const agreement = `${Math.round(trend.confidence * 100)}%`;

  if (trend.direction === 'unknown') {
    return `${timeframe} has insufficient data for a directional signal.`;
  }
  if (isShortHorizon) {
    return momentum
      ? `${timeframe} is ${direction} with ${momentum} short-term momentum and ${agreement} indicator agreement.`
      : `${timeframe} is ${direction}; short-term momentum is awaiting enough closed-bar evidence.`;
  }
  return `${timeframe} is ${direction} with ${strength} strength and ${agreement} indicator agreement.`;
}

export const TrendCard = memo(function TrendCard({ trend, confluenceRole, onOpenChart }: TrendCardProps) {
  const icon = directionIcons[trend.direction] || '?';
  const color = directionColors[trend.direction] || '#9ca3af';
  const strengthColor = strengthColors[trend.strength] || '#9ca3af';
  const isShortHorizon = ['1m', '2m', '3m'].includes(trend.timeframe);
  const momentum = trend.short_horizon_momentum;
  const momentumColor = momentum ? momentumColors[momentum] || '#9ca3af' : '#9ca3af';
  const confidencePct = (trend.confidence * 100).toFixed(0);
  const scoreText = formatSignedScore(trend.score);
  const classificationLabel = trend.classification
    ? classificationLabels[trend.classification] || trend.classification.replace(/_/g, ' ')
    : null;
  const scoreLine = scoreText
    ? `Score ${scoreText}${classificationLabel ? ` · ${classificationLabel}` : ''}`
    : classificationLabel;
  const changeHistoryLabel = formatChangeHistory(trend.change_history);
  const attribution = trend.attribution ?? [];
  const keyLevelParts = formatKeyLevels(trend.key_levels);
  const scoring = trend.scoring;
  const scoringProfileText = scoring
    ? `${scoring.label} · ${scoring.components.length} inputs`
    : 'Scoring profile unavailable';
  const agreementTitle = scoring
    ? 'Weighted indicator agreement within this profile. It is not a probability and is not calibrated for comparison with other timeframe profiles.'
    : 'Agreement semantics unavailable for this signal.';
  const evidence = trend.evidence;
  const freshnessState = evidence?.freshness_state;
  const freshnessLabel = freshnessState ? evidenceLabels[freshnessState] || freshnessState.replace(/_/g, ' ') : null;
  const evidenceAge = formatAge(evidence?.age_seconds ?? trend.data_age_seconds);
  const warmupLabel = freshnessState === 'warming' && evidence
    ? `${evidence.warmup_bars ?? 0} / ${evidence.required_warmup_bars} bars`
    : null;
  const invalidReason = evidence?.invalid_reason
    ? invalidReasonLabels[evidence.invalid_reason] || evidence.invalid_reason.replace(/^data_status_/, '').replace(/_/g, ' ')
    : null;
  const sourceAsOf = evidence?.source_as_of ?? trend.timestamp;
  const providerLabel = formatProvider(evidence?.provider ?? trend.provider);
  const sessionLabel = trend.session === 'after_hours'
    ? 'After-hours'
    : trend.session === 'premarket'
      ? 'Premarket'
      : trend.session === 'regular'
        ? 'Regular'
        : trend.session === 'mixed'
          ? 'Mixed sessions'
          : 'Session unknown';
  // "Closed" alone would be read as market-closed (the app-wide freshness badge
  // meaning); this is bar completion, an unrelated concept, so it needs its own wording.
  const statusLabel = trend.bar_closed === false
    ? 'Forming'
    : trend.data_status && trend.data_status !== 'ok'
      ? trend.data_status.replace(/_/g, ' ')
      : 'Bar closed';

  const timeframeLabel = timeframeLabels[trend.timeframe] || trend.timeframe;
  const interactive = Boolean(onOpenChart);
  const handleOpenChart = onOpenChart;
  const handleKeyDown = interactive
    ? (event: React.KeyboardEvent<HTMLDivElement>) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          handleOpenChart?.();
        }
      }
    : undefined;

  return (
    <div
      className={`card trend-card${confluenceRole ? ` trend-card-${confluenceRole}` : ''}${interactive ? ' trend-card-clickable' : ''}`}
      style={{ borderLeftColor: color }}
      onClick={handleOpenChart}
      onKeyDown={handleKeyDown}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={interactive ? `Open ${trend.symbol} ${timeframeLabel} chart` : undefined}
    >
      <div className="trend-header">
        <span className="timeframe-badge">{timeframeLabels[trend.timeframe] || trend.timeframe}</span>
        {confluenceRole === 'input' && <span className="trend-contributor-badge">Confluence input</span>}
        {confluenceRole === 'out_of_scope' && <span className="trend-out-of-scope-badge">Not in preset</span>}
        <span className="direction-icon" style={{ color }}>{icon}</span>
      </div>
      <div className="trend-direction" style={{ color }}>
        {trend.direction.replace(/_/g, ' ').toUpperCase()}
      </div>
      {scoreLine && (
        <div className="trend-score" title={scoreThresholdTitle} aria-label={scoreLine}>
          {scoreLine}
        </div>
      )}
      <div className="trend-details">
        <div className="trend-detail">
          <span className="detail-label">{isShortHorizon ? 'Momentum' : 'Strength'}</span>
          <span className="detail-value" style={{ color: isShortHorizon ? momentumColor : strengthColor }}>
            {isShortHorizon
              ? (momentum ? momentum.replace(/_/g, ' ').toUpperCase() : 'AWAITING BARS')
              : trend.strength.replace(/_/g, ' ').toUpperCase()}
          </span>
        </div>
        <div className="trend-detail">
          <span className="detail-label" title={agreementTitle}>Agreement</span>
          <span className="detail-value" title={agreementTitle}>{confidencePct}%</span>
        </div>
      </div>
      <div className="trend-scoring-profile" title={agreementTitle} aria-label={`Scoring profile: ${scoringProfileText}`}>
        Profile: {scoringProfileText}
      </div>
      <div className="confidence-bar">
        <div
          className="confidence-fill"
          style={{ width: `${confidencePct}%`, backgroundColor: color }}
        />
      </div>
      <p className="signal-explanation">{signalExplanation(trend)}</p>
      {changeHistoryLabel && (
        <div className="trend-change-history" title="Direction persistence measured in closed bars for this timeframe.">
          {changeHistoryLabel}
        </div>
      )}
      {keyLevelParts.length > 0 && (
        <div className="trend-key-levels" title="Available indicator price levels for this timeframe.">
          {keyLevelParts.join(' · ')}
        </div>
      )}
      {attribution.length > 0 && (
        <details
          className="trend-attribution"
          onClick={event => event.stopPropagation()}
          onKeyDown={event => event.stopPropagation()}
        >
          <summary>Why this score</summary>
          <ul>
            {attribution.map(entry => (
              <li key={entry.component}>
                <span>{entry.component}</span>
                <span className="trend-attribution-value">{formatSignedContribution(entry.contribution)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {freshnessLabel && (
        <div
          className={`trend-evidence trend-evidence-${freshnessState}`}
          aria-label={`Evidence: ${freshnessLabel}${evidenceAge ? `, ${evidenceAge}` : ''}`}
        >
          <span className="trend-evidence-label">Evidence: {freshnessLabel}</span>
          {evidenceAge && <span>{evidenceAge}</span>}
          {warmupLabel && <span>{warmupLabel}</span>}
          {invalidReason && <span>{invalidReason}</span>}
        </div>
      )}
      <div className="trend-provenance">
        <span>{statusLabel}</span>
        <span>{sessionLabel}</span>
        {providerLabel && <span>{providerLabel}</span>}
        {sourceAsOf && <span>As of {formatETDateTime(sourceAsOf)}</span>}
      </div>
    </div>
  );
});
