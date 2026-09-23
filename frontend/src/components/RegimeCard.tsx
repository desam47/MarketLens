import React, { memo } from 'react';
import { RegimeData, SectorData } from '../services/api';
import { formatETDateTime } from './chartMath';

interface RegimeCardProps {
  regime: RegimeData | null;
  sectorData?: SectorData | null;
  error?: string | null;
  onRetry?: () => void;
}

// Phase 8: spec-compliant regime color map.
const regimeColors: Record<string, string> = {
  risk_on: '#10b981',    // green — bullish
  risk_off: '#ef4444',   // red — bearish
  neutral: '#f59e0b',    // amber — range-bound
  transition: '#a855f7',  // purple — volatile / changing
  unknown: '#9ca3af',    // gray — insufficient data
};

export const RegimeCard = memo(function RegimeCard({ regime, sectorData, error, onRetry }: RegimeCardProps) {
  if (error) {
    return (
      <div className="card regime-card card-error">
        <h2>Symbol Market Regime</h2>
        <p className="empty-state">⚠ Failed to load: {error}</p>
        {onRetry && <button className="btn btn-small data-state-retry" onClick={onRetry}>Retry</button>}
      </div>
    );
  }
  if (!regime) {
    return (
      <div className="card regime-card">
        <h2>Symbol Market Regime</h2>
        <p className="empty-state">No regime data available</p>
      </div>
    );
  }

  const color = regimeColors[regime.regime] || '#9ca3af';
  const confidencePct = (regime.confidence * 100).toFixed(0);
  const strengthPct = (regime.strength * 100).toFixed(0);

  return (
    <div className="card regime-card">
      <h2>Symbol Market Regime</h2>
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

      {/* Phase 8: sector + relative-strength footer */}
      {sectorData && (
        <div className="regime-footer">
          <div className="footer-row">
            <span className="footer-label">Sector:</span>
            <span className="footer-value">
              {sectorData.sector}
              {sectorData.sector_etf && ` (${sectorData.sector_etf})`}
            </span>
          </div>
          {sectorData.alignment_score > 0 && (
            <div className="footer-row">
              <span className="footer-label">Alignment:</span>
              <span className="footer-value" title="Stock vs sector vs market">
                {sectorData.alignment_score.toFixed(2)}
              </span>
            </div>
          )}
        </div>
      )}
      {regime.timestamp && (
        <div className="timestamp">
          {regime.freshness === 'closed' ? 'CLOSED' : regime.freshness.toUpperCase()}
          {regime.freshness !== 'closed' && regime.data_age_seconds != null && ` · Signal age: ${Math.round(regime.data_age_seconds)}s`}
          {' · '}Last completed bar: {formatETDateTime(regime.timestamp)}
        </div>
      )}
    </div>
  );
});
