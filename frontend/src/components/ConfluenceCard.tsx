import React from 'react';
import { ConfluenceData } from '../services/api';

interface ConfluenceCardProps {
  confluence: ConfluenceData | null;
  error?: string | null;
}

const directionColors: Record<string, string> = {
  strong_uptrend: '#10b981',
  uptrend: '#22c55e',
  weak_uptrend: '#84cc16',
  neutral: '#f59e0b',
  weak_downtrend: '#f97316',
  downtrend: '#ef4444',
  strong_downtrend: '#dc2626',
};

export function ConfluenceCard({ confluence, error }: ConfluenceCardProps) {
  if (error) {
    return (
      <div className="card confluence-card card-error">
        <h2>Multi-Timeframe Confluence</h2>
        <p className="empty-state">⚠ Failed to load: {error}</p>
      </div>
    );
  }
  if (!confluence) {
    return (
      <div className="card confluence-card">
        <h2>Multi-Timeframe Confluence</h2>
        <p className="empty-state">No confluence data available</p>
      </div>
    );
  }

  const color = directionColors[confluence.direction] || '#9ca3af';
  const alignmentPct = (confluence.alignment_score * 100).toFixed(0);
  const strengthPct = (confluence.strength * 100).toFixed(0);

  const timeframeSignals = confluence.timeframe_signals || {};
  const signalEntries = Object.entries(timeframeSignals);

  return (
    <div className="card confluence-card">
      <h2>Multi-Timeframe Confluence</h2>
      <div className="confluence-main">
        <div className="confluence-direction" style={{ color }}>
          <span className="direction-arrow">{confluence.direction.includes('up') ? '↑' : confluence.direction.includes('down') ? '↓' : '→'}</span>
          <span className="direction-text">{confluence.direction.replace(/_/g, ' ').toUpperCase()}</span>
        </div>
      </div>
      <div className="confluence-metrics">
        <div className="metric">
          <span className="metric-label">Alignment</span>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${alignmentPct}%`, backgroundColor: color }} />
          </div>
          <span className="metric-value">{alignmentPct}%</span>
        </div>
        <div className="metric">
          <span className="metric-label">Strength</span>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${strengthPct}%`, backgroundColor: color }} />
          </div>
          <span className="metric-value">{strengthPct}%</span>
        </div>
      </div>
      {signalEntries.length > 0 && (
        <div className="timeframe-signals">
          <h4>Timeframe Signals</h4>
          <div className="signal-grid">
            {signalEntries.map(([tf, signal]: [string, any]) => (
              <div key={tf} className="signal-item">
                <span className="signal-tf">{tf}</span>
                <span
                  className="signal-dir"
                  style={{ color: directionColors[signal.direction] || '#9ca3af' }}
                >
                  {signal.direction === 'uptrend' ? '↑' : signal.direction === 'downtrend' ? '↓' : '→'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      {confluence.timestamp && (
        <div className="timestamp">
          Updated: {new Date(confluence.timestamp).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
