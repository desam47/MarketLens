import React from 'react';
import { RegimeData } from '../services/api';

interface RegimeCardProps {
  regime: RegimeData | null;
  error?: string | null;
}

const regimeColors: Record<string, string> = {
  trending_up: '#10b981',
  trending_down: '#ef4444',
  ranging: '#f59e0b',
  volatile: '#dc2626',
  quiet: '#6b7280',
  breakout_up: '#22c55e',
  breakout_down: '#f97316',
  unknown: '#9ca3af',
};

export function RegimeCard({ regime, error }: RegimeCardProps) {
  if (error) {
    return (
      <div className="card regime-card card-error">
        <h2>Market Regime</h2>
        <p className="empty-state">⚠ Failed to load: {error}</p>
      </div>
    );
  }
  if (!regime) {
    return (
      <div className="card regime-card">
        <h2>Market Regime</h2>
        <p className="empty-state">No regime data available</p>
      </div>
    );
  }

  const color = regimeColors[regime.regime] || '#9ca3af';
  const confidencePct = (regime.confidence * 100).toFixed(0);
  const strengthPct = (regime.strength * 100).toFixed(0);

  return (
    <div className="card regime-card">
      <h2>Market Regime</h2>
      <div className="regime-main">
        <span className="regime-badge" style={{ backgroundColor: color }}>
          {regime.regime.replace(/_/g, ' ').toUpperCase()}
        </span>
      </div>
      <div className="regime-metrics">
        <div className="metric">
          <span className="metric-label">Confidence</span>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${confidencePct}%`, backgroundColor: color }} />
          </div>
          <span className="metric-value">{confidencePct}%</span>
        </div>
        <div className="metric">
          <span className="metric-label">Strength</span>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${strengthPct}%`, backgroundColor: color }} />
          </div>
          <span className="metric-value">{strengthPct}%</span>
        </div>
      </div>
      {regime.supporting_factors && Object.keys(regime.supporting_factors).length > 0 && (
        <div className="regime-factors">
          <h4>Supporting Factors</h4>
          <ul>
            {Object.entries(regime.supporting_factors).slice(0, 5).map(([key, value]) => (
              <li key={key}>
                <span className="factor-key">{key.replace(/_/g, ' ')}:</span>
                <span className="factor-value">
                  {typeof value === 'number' ? value.toFixed(2) : String(value)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {regime.timestamp && (
        <div className="timestamp">
          Updated: {new Date(regime.timestamp).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
