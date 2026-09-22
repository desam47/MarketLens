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

function signalExplanation(trend: TrendData): string {
  const timeframe = timeframeLabels[trend.timeframe] || trend.timeframe;
  const direction = trend.direction.replace(/_/g, ' ');
  const strength = trend.strength.replace(/_/g, ' ');
  const confidence = `${Math.round(trend.confidence * 100)}%`;

  if (trend.direction === 'unknown') {
    return `${timeframe} has insufficient data for a directional signal.`;
  }
  return `${timeframe} is ${direction} with ${strength} strength and ${confidence} confidence.`;
}

export const TrendCard = memo(function TrendCard({ trend }: TrendCardProps) {
  const icon = directionIcons[trend.direction] || '?';
  const color = directionColors[trend.direction] || '#9ca3af';
  const strengthColor = strengthColors[trend.strength] || '#9ca3af';
  const confidencePct = (trend.confidence * 100).toFixed(0);
  const sessionLabel = trend.session === 'after_hours'
    ? 'After-hours'
    : trend.session === 'premarket'
      ? 'Premarket'
      : trend.session === 'regular'
        ? 'Regular'
        : trend.session === 'mixed'
          ? 'Mixed sessions'
          : 'Session unknown';
  const statusLabel = trend.bar_closed === false
    ? 'Forming'
    : trend.data_status && trend.data_status !== 'ok'
      ? trend.data_status.replace(/_/g, ' ')
      : 'Closed';

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
          <span className="detail-label">Strength</span>
          <span className="detail-value" style={{ color: strengthColor }}>
            {trend.strength.replace(/_/g, ' ').toUpperCase()}
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
      <div className="trend-provenance">
        <span>{statusLabel}</span>
        <span>{sessionLabel}</span>
        {trend.provider && <span>{trend.provider}</span>}
        {trend.timestamp && <span>{formatETDateTime(trend.timestamp)}</span>}
      </div>
    </div>
  );
});
