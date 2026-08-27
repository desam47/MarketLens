import React from 'react';
import { TrendData } from '../services/api';

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

export function TrendCard({ trend }: TrendCardProps) {
  const icon = directionIcons[trend.direction] || '?';
  const color = directionColors[trend.direction] || '#9ca3af';
  const strengthColor = strengthColors[trend.strength] || '#9ca3af';
  const confidencePct = (trend.confidence * 100).toFixed(0);

  const timeframeLabels: Record<string, string> = {
    '1m': '1 Min',
    '5m': '5 Min',
    '15m': '15 Min',
    '30m': '30 Min',
    '1h': '1 Hour',
    '4h': '4 Hour',
    '1d': 'Daily',
    '1wk': 'Weekly',
  };

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
    </div>
  );
}
