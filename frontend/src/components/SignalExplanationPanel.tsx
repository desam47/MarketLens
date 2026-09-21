import React from 'react';
import { SignalExplanation } from '../services/api';

interface SignalExplanationPanelProps {
  symbol: string;
  explanation?: SignalExplanation;
}

const directionColors: Record<string, string> = {
  bullish: '#10b981',
  bearish: '#ef4444',
  neutral: '#f59e0b',
};

function titleCase(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function formatAge(seconds: number | null): string {
  if (seconds == null) return 'Unavailable';
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${(seconds / 3600).toFixed(1)}h ago`;
}

function formatReturn(value: number | null): string {
  if (value == null) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export function SignalExplanationPanel({ symbol, explanation }: SignalExplanationPanelProps) {
  if (
    !explanation
    || !Number.isFinite(explanation.confidence)
    || !explanation.timeframe_agreement
    || !explanation.data_freshness
  ) {
    return (
      <div className="card analysis-card signal-explanation-card">
        <div className="card-header-row"><h2>Signal Explanation</h2><span className="symbol-tag">{symbol}</span></div>
        <p className="empty-state">Explanation data is not available yet.</p>
      </div>
    );
  }

  const agreement = explanation.timeframe_agreement;
  const freshness = explanation.data_freshness;
  const history = explanation.historical_performance;
  const changes = explanation.changes;
  const color = directionColors[explanation.direction] || directionColors.neutral;
  const drivers = explanation.drivers.slice(0, 8);

  return (
    <div className="card analysis-card signal-explanation-card">
      <div className="card-header-row">
        <div>
          <h2>Signal Explanation</h2>
          <p className="panel-caveat">Why the current scanner signal looks this way.</p>
        </div>
        <span className="symbol-tag">{symbol}</span>
      </div>

      <div className="signal-explanation-summary">
        <span className="signal-explanation-direction" style={{ color }}>
          {explanation.direction === 'bullish' ? '↑' : explanation.direction === 'bearish' ? '↓' : '→'} {titleCase(explanation.direction)}
        </span>
        <span className="signal-explanation-confidence">{explanation.confidence.toFixed(0)}% confidence</span>
      </div>

      <div className="signal-explanation-section">
        <div className="score-breakdown-title">What is driving it</div>
        {drivers.length === 0 ? <p className="empty-state">No qualifying indicator drivers are available.</p> : (
          <div className="signal-driver-list">
            {drivers.map(driver => (
              <div className="signal-driver" key={driver.key}>
                <span className="signal-driver-icon" style={{ color: directionColors[driver.direction] || directionColors.neutral }}>
                  {driver.direction === 'bullish' ? '↑' : driver.direction === 'bearish' ? '↓' : '•'}
                </span>
                <div>
                  <strong>{driver.label}</strong>
                  <p>{driver.description}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="signal-explanation-section">
        <div className="score-breakdown-title">Timeframe agreement</div>
        <div className="signal-agreement-summary">
          <strong>{agreement.alignment_pct.toFixed(0)}%</strong>
          <span>{agreement.dominant} across {agreement.total || 0} timeframe{agreement.total === 1 ? '' : 's'}</span>
        </div>
        <div className="signal-timeframe-chips">
          {agreement.timeframes.map(timeframe => (
            <span key={timeframe.timeframe} className="signal-timeframe-chip" data-direction={timeframe.direction}>
              {timeframe.timeframe} {timeframe.direction === 'bullish' ? '↑' : timeframe.direction === 'bearish' ? '↓' : '→'}
            </span>
          ))}
        </div>
      </div>

      <div className="signal-explanation-meta">
        <span><strong>Data:</strong> {titleCase(freshness.status)} · {formatAge(freshness.age_seconds)}</span>
        {freshness.provider && <span><strong>Provider:</strong> {freshness.provider}</span>}
      </div>

      <div className="signal-explanation-section">
        <div className="score-breakdown-title">What changed</div>
        {!changes ? (
          <p className="signal-explanation-muted">This is the first scan in the current session.</p>
        ) : !changes.changed ? (
          <p className="signal-explanation-muted">No material change since the previous scan.</p>
        ) : (
          <div className="signal-change-grid">
            <span>Score {changes.score_delta >= 0 ? '+' : ''}{changes.score_delta.toFixed(1)}</span>
            {changes.signals_added.length > 0 && <span className="signal-change-added">+ {changes.signals_added.map(titleCase).join(', ')}</span>}
            {changes.signals_removed.length > 0 && <span className="signal-change-removed">− {changes.signals_removed.map(titleCase).join(', ')}</span>}
          </div>
        )}
      </div>

      <div className="signal-history-strip">
        <div>
          <span className="score-breakdown-title">Historical daily signals</span>
          <p>{history ? `${history.with_outcomes} completed outcomes from ${history.total} recorded signals.` : 'No completed historical outcomes yet.'}</p>
        </div>
        {history && (
          <div className="signal-history-metrics">
            <span><strong>{history.win_rate == null ? '—' : `${(history.win_rate * 100).toFixed(0)}%`}</strong><small> win rate</small></span>
            <span><strong>{formatReturn(history.avg_return_5b)}</strong><small> avg 5-bar</small></span>
            <span><strong>{formatReturn(history.avg_return_10b)}</strong><small> avg 10-bar</small></span>
          </div>
        )}
      </div>
    </div>
  );
}

export default SignalExplanationPanel;
