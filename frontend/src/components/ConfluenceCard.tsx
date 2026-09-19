import React, { memo, useMemo } from 'react';
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

const stateColors: Record<string, string> = {
  continuation: '#10b981',
  pullback: '#f59e0b',
  transition: '#ef4444',
  reversal_confirmed: '#dc2626',
  neutral: '#9ca3af',
};

const stateIcons: Record<string, string> = {
  continuation: '→',
  pullback: '↩',
  transition: '⇄',
  reversal_confirmed: '↻',
  neutral: '•',
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

function formatStateLabel(state: string): string {
  return state.replace(/_/g, ' ').toUpperCase();
}

function getNarrative(confluence: ConfluenceData): string {
  const shortState = confluence.short_term_state || 'neutral';
  const interState = confluence.intermediate_state || 'neutral';
  const higherState = confluence.higher_state || 'neutral';
  const shortDir = confluence.short_term_direction || 'neutral';
  const higherDir = confluence.higher_direction || 'neutral';
  
  // Build narrative based on trend states
  if (higherState === 'reversal_confirmed') {
    return `${higherDir === 'uptrend' ? 'Bullish' : 'Bearish'} reversal confirmed`;
  }
  if (higherState === 'transition') {
    return `${higherDir === 'uptrend' ? 'Bullish' : 'Bearish'} transition — ${interState === 'pullback' ? 'pullback' : 'early stage'}`;
  }
  if (interState === 'pullback' && higherState === 'continuation') {
    return `${higherDir === 'uptrend' ? 'Bullish' : 'Bearish'} trend, short-term pullback`;
  }
  if (shortState === 'pullback' && interState === 'continuation') {
    return `${interState === 'continuation' ? 'Trend continuing' : 'Mixed'}, lower TF counter-trend`;
  }
  if (shortState === 'continuation' && interState === 'continuation' && higherState === 'continuation') {
    return `Strong ${shortDir === 'uptrend' ? 'bullish' : shortDir === 'downtrend' ? 'bearish' : 'neutral'} alignment`;
  }
  return `${higherDir === 'uptrend' ? 'Bullish' : higherDir === 'downtrend' ? 'Bearish' : 'Neutral'} bias`;
}

export const ConfluenceCard = memo(function ConfluenceCard({ confluence, error, selectedPreset, onPresetChange }: ConfluenceCardProps) {
  // Hooks must run unconditionally on every render of this component
  // instance, so this has to sit above the early returns below (a
  // conditional useMemo would change hook count between a null and a
  // populated confluence prop, which React disallows outright).
  const timeframeSignals = confluence?.timeframe_signals;
  const signalEntries = useMemo(
    () => Object.entries(timeframeSignals || {}).sort(
      ([a], [b]) => (TF_ORDER[a] ?? 99) - (TF_ORDER[b] ?? 99)
    ),
    [timeframeSignals]
  );

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

  // Phase 7: alignment breakdown + horizon directions.
  const bullishPct = ((confluence.bullish_alignment ?? 0) * 100).toFixed(0);
  const bearishPct = ((confluence.bearish_alignment ?? 0) * 100).toFixed(0);
  const conflicts = confluence.conflicting ?? 0;

  // Horizon chips with direction + state
  const horizonChips: Array<{ label: string; direction: string | undefined; state: string | undefined }> = [
    { label: 'Higher', direction: confluence.higher_direction, state: confluence.higher_state },
    { label: 'Intermediate', direction: confluence.intermediate_direction, state: confluence.intermediate_state },
    { label: 'Lower', direction: confluence.short_term_direction, state: confluence.short_term_state },
  ];

  const narrative = getNarrative(confluence);

  return (
    <div className="card confluence-card">
      <div className="confluence-card-header">
        <h2>Multi-Timeframe Confluence</h2>
        <PresetSelector selectedPreset={selectedPreset} onPresetChange={onPresetChange} />
      </div>
      
      {/* Trend Map - Horizon Narrative */}
      <div className="trend-map">
        <div className="trend-narrative">
          <span className="narrative-label">Trend Map:</span>
          <span className="narrative-text">{narrative}</span>
        </div>
        <div className="horizon-row">
          {horizonChips.map((chip) => {
            const dir = chip.direction || 'neutral';
            const state = chip.state || 'neutral';
            return (
              <div key={chip.label} className="horizon-column">
                <span className="horizon-label">{chip.label}</span>
                <div className="horizon-content">
                  <span className="horizon-direction" style={{ color: directionColors[dir] || '#9ca3af' }}>
                    {dir.replace(/_/g, ' ').toUpperCase()}
                  </span>
                  <span className="horizon-state" style={{ color: stateColors[state] || '#9ca3af' }}>
                    {stateIcons[state]} {formatStateLabel(state)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
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
        <div className="metric">
          <span className="metric-label">Valid Coverage</span>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${((confluence.valid_coverage ?? 0) * 100).toFixed(0)}%`, backgroundColor: '#06b6d4' }} />
          </div>
          <span className="metric-value">{((confluence.valid_coverage ?? 0) * 100).toFixed(0)}%</span>
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
