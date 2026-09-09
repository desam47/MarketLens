import React, { memo } from 'react';
import { MarketContextData } from '../services/api';
import { formatETDateTime } from './chartMath';

// Phase 8: color-coded regime names (same map as RegimeCard).
const regimeColors: Record<string, string> = {
  risk_on: '#10b981',
  risk_off: '#ef4444',
  neutral: '#f59e0b',
  transition: '#a855f7',
  unknown: '#9ca3af',
};

const indexLabels: Record<string, string> = {
  SPY: 'S&P 500 (SPY)',
  QQQ: 'Nasdaq (QQQ)',
  IWM: 'Russell 2K (IWM)',
  '^VIX': 'Volatility (VIX)',
};

interface MarketContextCardProps {
  context: MarketContextData | null;
  error?: string | null;
}

export const MarketContextCard = memo(function MarketContextCard({ context, error }: MarketContextCardProps) {
  if (error) {
    return (
      <div className="card market-context-card card-error">
        <h2>Market Context</h2>
        <p className="empty-state">⚠ Failed to load: {error}</p>
      </div>
    );
  }

  if (!context) {
    return (
      <div className="card market-context-card">
        <h2>Market Context</h2>
        <p className="empty-state">No market context data available</p>
      </div>
    );
  }

  const color = regimeColors[context.regime] || '#9ca3af';

  const momentumArrow = context.momentum > 0.05 ? '↑'
    : context.momentum < -0.05 ? '↓'
    : '→';
  const momentumColor = context.momentum > 0.05 ? '#10b981'
    : context.momentum < -0.05 ? '#ef4444'
    : '#9ca3af';

  const volLabel: Record<string, string> = {
    low: 'Low',
    normal: 'Normal',
    high: 'High',
    unknown: '—',
  };

  return (
    <div className="card market-context-card">
      <h2>Market Context</h2>
      <div className="mc-main">
        <span className="mc-badge" style={{ backgroundColor: color }}>
          {context.regime.replace(/_/g, ' ').toUpperCase()}
        </span>
        <div className="mc-metrics">
          <div className="mc-metric" title="Market momentum (composite of sub-indices)">
            <span className="mc-metric-label">Momentum</span>
            <span className="mc-metric-value" style={{ color: momentumColor }}>
              {momentumArrow} {Math.abs(context.momentum).toFixed(2)}
            </span>
          </div>
          <div className="mc-metric" title="Trend strength (ADX composite)">
            <span className="mc-metric-label">Trend</span>
            <span className="mc-metric-value">
              {(context.trend_strength * 100).toFixed(0)}%
            </span>
          </div>
          <div className="mc-metric" title="Volatility state">
            <span className="mc-metric-label">Volatility</span>
            <span className="mc-metric-value">
              {volLabel[context.volatility_state] ?? context.volatility_state}
            </span>
          </div>
        </div>
      </div>

      {Object.keys(context.sub_regimes).length > 0 && (
        <div className="mc-sub-table">
          <h4>Sub-Indices</h4>
          <div className="mc-sub-rows">
            {Object.entries(context.sub_regimes).map(([sym, reg]) => (
              <div key={sym} className="mc-sub-row">
                <span className="mc-sub-name" title={indexLabels[sym] ?? sym}>
                  {sym}
                </span>
                <span
                  className="mc-sub-badge"
                  style={{ backgroundColor: regimeColors[reg] || '#9ca3af' }}
                >
                  {reg.replace(/_/g, ' ').toUpperCase()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {context.timestamp && (
        <div className="timestamp">
          Updated: {formatETDateTime(context.timestamp)}
        </div>
      )}
    </div>
  );
});
