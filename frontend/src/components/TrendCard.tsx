import React, { memo } from 'react';
import { TrendData } from '../services/api';
import { formatETDateTime } from './chartMath';

interface TrendCardProps {
  trend: TrendData;
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

const invalidReasonLabels: Record<string, string> = {
  signal_unavailable: 'Signal unavailable',
  source_timestamp_missing: 'Source timestamp unavailable',
  source_metadata_unavailable: 'Source evidence unavailable',
  bar_forming: 'Waiting for bar close',
  insufficient_warmup: 'More history needed',
  warmup_unknown: 'Warm-up status unavailable',
  source_stale: 'Source is stale',
};

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
  const confidence = `${Math.round(trend.confidence * 100)}%`;

  if (trend.direction === 'unknown') {
    return `${timeframe} has insufficient data for a directional signal.`;
  }
  if (isShortHorizon) {
    return momentum
      ? `${timeframe} is ${direction} with ${momentum} short-term momentum and ${confidence} confidence.`
      : `${timeframe} is ${direction}; short-term momentum is awaiting enough closed-bar evidence.`;
  }
  return `${timeframe} is ${direction} with ${strength} strength and ${confidence} confidence.`;
}

export const TrendCard = memo(function TrendCard({ trend }: TrendCardProps) {
  const icon = directionIcons[trend.direction] || '?';
  const color = directionColors[trend.direction] || '#9ca3af';
  const strengthColor = strengthColors[trend.strength] || '#9ca3af';
  const isShortHorizon = ['1m', '2m', '3m'].includes(trend.timeframe);
  const momentum = trend.short_horizon_momentum;
  const momentumColor = momentum ? momentumColors[momentum] || '#9ca3af' : '#9ca3af';
  const confidencePct = (trend.confidence * 100).toFixed(0);
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

  return (
    <div className="card trend-card" style={{ borderLeftColor: color }}>
      <div className="trend-header">
        <span className="timeframe-badge">{timeframeLabels[trend.timeframe] || trend.timeframe}</span>
        <span className="direction-icon" style={{ color }}>{icon}</span>
      </div>
      <div className="trend-direction" style={{ color }}>
        {trend.direction.replace(/_/g, ' ').toUpperCase()}
      </div>
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
          <span className="detail-label">Confidence</span>
          <span className="detail-value">{confidencePct}%</span>
        </div>
      </div>
      <div className="confidence-bar">
        <div
          className="confidence-fill"
          style={{ width: `${confidencePct}%`, backgroundColor: color }}
        />
      </div>
      <p className="signal-explanation">{signalExplanation(trend)}</p>
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
        {trend.provider && <span>{trend.provider}</span>}
        {sourceAsOf && <span>As of {formatETDateTime(sourceAsOf)}</span>}
      </div>
    </div>
  );
});
