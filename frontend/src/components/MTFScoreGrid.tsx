import React from 'react';

export interface TrendSignalEntry {
  direction: string;
  strength: string;
  confidence: number;
}

export interface TrendSignalsMap {
  [timeframe: string]: TrendSignalEntry;
}

interface MTFScoreGridProps {
  trendSignals: TrendSignalsMap;
  symbol: string;
}

// Map scanner timeframe keys to display labels and sort order.
const TIMEFRAME_ORDER: [string, string][] = [
  ['ONE_MINUTE', '1m'],
  ['FIVE_MINUTE', '5m'],
  ['FIFTEEN_MINUTE', '15m'],
  ['THIRTY_MINUTE', '30m'],
  ['ONE_HOUR', '1h'],
  ['FOUR_HOUR', '4h'],
  ['ONE_DAY', '1d'],
];

const DIR_COLORS: Record<string, string> = {
  uptrend: '#10b981',
  downtrend: '#ef4444',
  sideways: '#f59e0b',
  unknown: '#9ca3af',
};

const DIR_ICONS: Record<string, string> = {
  uptrend: '↑',
  downtrend: '↓',
  sideways: '↔',
  unknown: '?',
};

const STR_COLORS: Record<string, string> = {
  very_strong: '#22c55e',
  strong: '#10b981',
  moderate: '#f59e0b',
  weak: '#ef4444',
  unknown: '#9ca3af',
};

export function MTFScoreGrid({ trendSignals, symbol }: MTFScoreGridProps) {
  // Build ordered list of timeframes present in the data.
  const entries = TIMEFRAME_ORDER
    .filter(([key]) => key in trendSignals)
    .map(([key, label]) => ({ key, label, signal: trendSignals[key] }));

  if (entries.length === 0) {
    return (
      <div className="card analysis-card mtfs-grid-card">
        <h2>Multi-Timeframe Trend</h2>
        <p className="empty-state">No MTF trend data for {symbol}</p>
      </div>
    );
  }

  return (
    <div className="card analysis-card mtfs-grid-card">
      <div className="card-header-row">
        <h2>Multi-Timeframe Trend</h2>
        <span className="symbol-tag">{symbol}</span>
      </div>
      <div className="mtfs-grid">
        {entries.map(({ key, label, signal }) => {
          const color = DIR_COLORS[signal.direction] ?? '#9ca3af';
          const icon = DIR_ICONS[signal.direction] ?? '?';
          const strColor = STR_COLORS[signal.strength] ?? '#9ca3af';
          const confPct = Math.round((signal.confidence ?? 0) * 100);
          return (
            <div key={key} className="mtfs-cell" style={{ borderTop: `3px solid ${color}` }}>
              <div className="mtfs-tf-label">{label}</div>
              <div className="mtfs-direction" style={{ color }}>
                {icon} {signal.direction.replace(/_/g, ' ')}
              </div>
              <div className="mtfs-strength" style={{ color: strColor }}>
                {signal.strength.replace(/_/g, ' ').toUpperCase()}
              </div>
              <div className="mtfs-conf-bar">
                <div
                  className="mtfs-conf-fill"
                  style={{ width: `${confPct}%`, backgroundColor: color }}
                />
              </div>
              <div className="mtfs-conf-label">{confPct}% conf</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default MTFScoreGrid;
