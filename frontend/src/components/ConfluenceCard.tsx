import React, { memo } from 'react';
import { ConfluenceData } from '../services/api';
import { formatETDateTime } from './chartMath';

interface ConfluenceCardProps {
  confluence: ConfluenceData | null;
  error?: string | null;
  selectedPreset: string;
  onPresetChange: (preset: string) => void;
}

const directionColors: Record<string, string> = {
  strong_uptrend: '#10b981',
  uptrend: '#22c55e',
  weak_uptrend: '#84cc16',
  neutral: '#f59e0b',
  sideways: '#f59e0b',
  weak_downtrend: '#f97316',
  downtrend: '#ef4444',
  strong_downtrend: '#dc2626',
};

// Canonical sort order for timeframe strings (shortest → longest).
const TF_ORDER: Record<string, number> = {
  '1m': 1, '2m': 2, '3m': 3, '5m': 4,
  '15m': 5, '30m': 6, '1h': 7, '4h': 8,
  '1d': 9, '1wk': 10,
};

// Canonical order from the backend (shortest horizon first).
const PRESET_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'scalper', label: 'Scalper' },
  { value: 'day_trading', label: 'Day Trading' },
  { value: 'swing', label: 'Swing Trading' },
  { value: 'all', label: 'All Timeframes' },
];

export const ConfluenceCard = memo(function ConfluenceCard({ confluence, error, selectedPreset, onPresetChange }: ConfluenceCardProps) {
  if (error) {
    return (
      <div className="card confluence-card card-error">
        <div className="confluence-card-header">
          <h2>Multi-Timeframe Confluence</h2>
          <PresetSelector selectedPreset={selectedPreset} onPresetChange={onPresetChange} />
        </div>
        <p className="empty-state">⚠ Failed to load: {error}</p>
      </div>
    );
  }
  if (!confluence) {
    return (
      <div className="card confluence-card">
        <div className="confluence-card-header">
          <h2>Multi-Timeframe Confluence</h2>
          <PresetSelector selectedPreset={selectedPreset} onPresetChange={onPresetChange} />
        </div>
        <p className="empty-state">No confluence data available</p>
      </div>
    );
  }

  const color = directionColors[confluence.direction] || '#9ca3af';
  const alignmentPct = (confluence.alignment_score * 100).toFixed(0);
  const strengthPct = (confluence.strength * 100).toFixed(0);

  const timeframeSignals = confluence.timeframe_signals || {};
  const signalEntries = Object.entries(timeframeSignals).sort(
    ([a], [b]) => (TF_ORDER[a] ?? 99) - (TF_ORDER[b] ?? 99)
  );

  // Phase 7: alignment breakdown + horizon directions.
  const bullishPct = ((confluence.bullish_alignment ?? 0) * 100).toFixed(0);
  const bearishPct = ((confluence.bearish_alignment ?? 0) * 100).toFixed(0);
  const conflicts = confluence.conflicting ?? 0;

  // TrendDirection-style strings (e.g. "uptrend", "downtrend", "sideways")
  // share the same color map as ConfluenceDirection.
  const horizonChips: Array<{ label: string; value: string | undefined }> = [
    { label: 'Short', value: confluence.short_term_direction },
    { label: 'Intermediate', value: confluence.intermediate_direction },
    { label: 'Higher', value: confluence.higher_direction },
  ];

  return (
    <div className="card confluence-card">
      <div className="confluence-card-header">
        <h2>Multi-Timeframe Confluence</h2>
        <PresetSelector selectedPreset={selectedPreset} onPresetChange={onPresetChange} />
      </div>
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
      {/* Phase 7: alignment breakdown chips + conflict badge. */}
      <div className="confluence-phase7">
        <div className="alignment-chips">
          <span className="alignment-chip bullish" title="Bullish alignment">
            ↑ {bullishPct}%
          </span>
          <span className="alignment-chip bearish" title="Bearish alignment">
            ↓ {bearishPct}%
          </span>
          {conflicts > 0 && (
            <span className="conflict-badge" title="Timeframes disagreeing with majority">
              ⚠ {conflicts} conflict{conflicts === 1 ? '' : 's'}
            </span>
          )}
        </div>
        <div className="horizon-chips">
          {horizonChips.map((chip) => {
            const dir = chip.value || 'neutral';
            return (
              <span key={chip.label} className="horizon-chip" title={`${chip.label}-term direction`}>
                <span className="horizon-label">{chip.label}:</span>{' '}
                <span style={{ color: directionColors[dir] || '#9ca3af', fontWeight: 600 }}>
                  {dir.replace(/_/g, ' ')}
                </span>
              </span>
            );
          })}
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
                  {signal.direction === 'uptrend' ? '↑' : signal.direction === 'downtrend' ? '↓' : signal.direction === 'sideways' ? '↔' : '?'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      {confluence.timestamp && (
        <div className="timestamp">
          Updated: {formatETDateTime(confluence.timestamp)}
        </div>
      )}
    </div>
  );
});

// ------------------------------------------------------------------ sub-components

function PresetSelector({
  selectedPreset,
  onPresetChange,
}: {
  selectedPreset: string;
  onPresetChange: (preset: string) => void;
}) {
  return (
    <div className="preset-selector-wrap" title="Choose a trading style preset">
      <select
        className="preset-selector"
        value={selectedPreset}
        onChange={(e) => onPresetChange(e.target.value)}
        aria-label="Trading style preset"
      >
        {PRESET_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      <span className="preset-selector-caret" aria-hidden="true">▾</span>
    </div>
  );
}
