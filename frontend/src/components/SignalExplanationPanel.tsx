import React from 'react';
import { LiveQuoteUpdateData, SignalExplanation, TapeSnapshot } from '../services/api';

interface SignalExplanationPanelProps {
  symbol: string;
  explanation?: SignalExplanation;
  liveQuote?: LiveQuoteUpdateData | null;
  tape?: TapeSnapshot | null;
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

export function SignalExplanationPanel({ symbol, explanation, liveQuote, tape }: SignalExplanationPanelProps) {
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
  const bullish = explanation.direction === 'bullish';
  const bearish = explanation.direction === 'bearish';
  const evidence: string[] = [];
  let confirms = 0;
  let contradicts = 0;
  if (liveQuote && liveQuote.bid_size != null && liveQuote.ask_size != null && liveQuote.bid_size + liveQuote.ask_size > 0) {
    const imbalance = (liveQuote.bid_size - liveQuote.ask_size) / (liveQuote.bid_size + liveQuote.ask_size);
    const aligned = bullish ? imbalance >= 0.15 : bearish ? imbalance <= -0.15 : false;
    const opposed = bullish ? imbalance <= -0.15 : bearish ? imbalance >= 0.15 : false;
    if (aligned) confirms += 1;
    if (opposed) contradicts += 1;
    evidence.push(`BBO ${imbalance >= 0 ? 'bid' : 'ask'} imbalance ${Math.abs(imbalance * 100).toFixed(0)}%`);
  }
  if (tape) {
    const buyFlow = tape.pressure.includes('buy') || tape.pressure_trend.includes('buy');
    const sellFlow = tape.pressure.includes('sell') || tape.pressure_trend.includes('sell');
    if ((bullish && buyFlow) || (bearish && sellFlow)) confirms += 1;
    if ((bullish && sellFlow) || (bearish && buyFlow)) contradicts += 1;
    evidence.push(`Tape ${titleCase(tape.pressure_trend === 'balanced' ? tape.pressure : tape.pressure_trend)}`);
    if ((tape.tape_accel ?? 0) >= 1.5 || (tape.volume_accel ?? 0) >= 1.5) evidence.push('Live activity accelerating');
  }
  const liveVerdict = confirms > contradicts ? 'Confirmed' : contradicts > confirms ? 'Contradicted' : evidence.length ? 'Mixed' : 'No live data';

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
        <div className="score-breakdown-title">Live confirmation</div>
        <div className={`signal-live-verdict signal-live-${liveVerdict.toLowerCase().replace(/ /g, '-')}`}><strong>{liveVerdict}</strong><span>{evidence.length ? evidence.join(' · ') : 'Waiting for live BBO or Time & Sales data.'}</span></div>
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
        <span>
          <strong>Data:</strong> {freshness.status === 'closed' ? 'Mkt Closed' : titleCase(freshness.status)}
          {freshness.status !== 'closed' && ` · ${formatAge(freshness.age_seconds)}`}
        </span>
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
