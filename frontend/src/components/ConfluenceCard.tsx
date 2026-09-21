import React, { memo, useMemo } from 'react';
import { ConfluenceData } from '../services/api';
import { formatETDateTime } from './chartMath';

interface ConfluenceCardProps {
  confluence: ConfluenceData | null;
  error?: string | null;
  selectedPreset: string;
  onPresetChange: (preset: string) => void;
  onRetry?: () => void;
}

const directionColors: Record<string, string> = {
  strong_uptrend: '#10b981',
  uptrend: '#22c55e',
  weak_uptrend: '#84cc16',
  strong_bullish: '#10b981',
  bullish: '#22c55e',
  weak_bullish: '#84cc16',
  neutral: '#f59e0b',
  sideways: '#f59e0b',
  weak_bearish: '#f97316',
  bearish: '#ef4444',
  strong_bearish: '#dc2626',
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

function directionIcon(direction: string): string {
  if (direction.includes('up') || direction.includes('bullish')) return '↑';
  if (direction.includes('down') || direction.includes('bearish')) return '↓';
  if (direction === 'sideways') return '↔';
  return '→';
}

function directionLabel(direction: string): string {
  if (direction.includes('up') || direction.includes('bullish')) return 'Bullish';
  if (direction.includes('down') || direction.includes('bearish')) return 'Bearish';
  return 'Neutral';
}

function structureLabel(state: string): string {
  if (state === 'continuation') return 'Continuation';
  if (state === 'pullback') return 'Pullback';
  if (state === 'transition') return 'Transition';
  if (state === 'reversal_confirmed') return 'Reversal';
  return state.replace(/_/g, ' ').toUpperCase();
}

function timingLabel(shortState: string, shortDir: string, higherDir: string): string {
  const directionSide = (direction: string): 'up' | 'down' | null => {
    if (direction.includes('up') || direction.includes('bullish')) return 'up';
    if (direction.includes('down') || direction.includes('bearish')) return 'down';
    return null;
  };
  const shortSide = directionSide(shortDir);
  const higherSide = directionSide(higherDir);
  if (shortState === 'pullback') return 'Countertrend';
  if (shortState === 'transition') return 'Breakout attempt';
  if (shortState === 'reversal_confirmed') return 'Breakout attempt';
  if (shortState === 'continuation' && shortSide && higherSide) {
    return shortSide === higherSide ? 'Aligned' : 'Countertrend';
  }
  return 'Neutral';
}

function getNarrative(confluence: ConfluenceData): string {
  const shortState = confluence.short_term_state || 'neutral';
  const interState = confluence.intermediate_state || 'neutral';
  const higherState = confluence.higher_state || 'neutral';
  const interDir = confluence.intermediate_direction || 'neutral';
  const higherDir = confluence.higher_direction || 'neutral';
  
  const isTrend = (d: string) => d === 'uptrend' || d === 'downtrend';
  const trendLabel = (d: string) => d === 'uptrend' ? 'Bullish' : d === 'downtrend' ? 'Bearish' : 'Neutral';
  
  // Build narrative based on trend states
  if (higherState === 'reversal_confirmed') {
    return `${trendLabel(higherDir)} reversal confirmed`;
  }
  if (higherState === 'transition') {
    return `${trendLabel(higherDir)} transition — ${interState === 'pullback' ? 'pullback' : 'early stage'}`;
  }
  // Pullback from established higher trend
  if (interState === 'pullback' && higherState === 'continuation' && isTrend(higherDir)) {
    return `${trendLabel(higherDir)} trend, short-term pullback`;
  }
  if (shortState === 'pullback' && interState === 'continuation' && isTrend(interDir)) {
    return `${trendLabel(interDir)} trend, lower TF counter-trend`;
  }
  if (shortState === 'continuation' && interState === 'continuation' && higherState === 'continuation' && isTrend(higherDir)) {
    return `Strong ${trendLabel(higherDir).toLowerCase()} alignment`;
  }
  // Neutral/conflicting cases
  if (!isTrend(higherDir)) {
    return `No clear trend (${trendLabel(higherDir)})`;
  }
  return `${trendLabel(higherDir)} bias`;
}

function getExplanation(confluence: ConfluenceData): string {
  const coverage = Math.round((confluence.valid_coverage ?? 0) * 100);
  const conflicts = confluence.conflicting ?? 0;
  const alignment = Math.round(confluence.alignment_score * 100);

  if (coverage === 0) {
    return 'Waiting for enough valid timeframe data to confirm the broader signal.';
  }
  if (conflicts > 0) {
    return `${alignment}% alignment across valid timeframes, with ${conflicts} timeframe${conflicts === 1 ? '' : 's'} disagreeing. Treat this as a mixed signal.`;
  }
  return `${alignment}% alignment across ${coverage}% of the configured timeframes supports this ${directionLabel(confluence.direction).toLowerCase()} bias.`;
}

export const ConfluenceCard = memo(function ConfluenceCard({ confluence, error, selectedPreset, onPresetChange, onRetry }: ConfluenceCardProps) {
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
        {onRetry && <button className="btn btn-small data-state-retry" onClick={onRetry}>Retry</button>}
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

  const narrative = getNarrative(confluence);

  return (
    <div className="card confluence-card">
      <div className="confluence-card-header">
        <h2>Multi-Timeframe Confluence</h2>
        <PresetSelector selectedPreset={selectedPreset} onPresetChange={onPresetChange} />
      </div>
      
      {/* Compact Trend Map */}
      <div className="trend-map-compact">
        <div className="tm-col tm-lower">
          <div className="tm-col-label">Lower</div>
          <div className="tm-col-value" style={{ color: directionColors[confluence.short_term_direction || 'neutral'] || '#9ca3af' }}>
            Timing: {timingLabel(confluence.short_term_state || 'neutral', confluence.short_term_direction || 'neutral', confluence.higher_direction || 'neutral')}
          </div>
        </div>
        <div className="tm-col tm-intermediate">
          <div className="tm-col-label">Intermediate</div>
          <div className="tm-col-value" style={{ color: stateColors[confluence.intermediate_state || 'neutral'] || '#9ca3af' }}>
            Structure: {structureLabel(confluence.intermediate_state || 'neutral')}
          </div>
        </div>
        <div className="tm-col tm-higher">
          <div className="tm-col-label">Higher</div>
          <div className="tm-col-value" style={{ color: directionColors[confluence.higher_direction || 'neutral'] || '#9ca3af' }}>
            Bias: {directionLabel(confluence.higher_direction || 'neutral')}
          </div>
          <div className="tm-col-sub">
            Score: {alignmentPct}% • Freshness: {formatETDateTime(confluence.timestamp)}
          </div>
        </div>
      </div>

      {/* Overall */}
      <div className="confluence-overall">
        <span className="overall-narrative">{narrative}</span>
        <div className="overall-metrics">
          <span className="overall-metric">Confidence: {strengthPct}%</span>
          <span className="overall-metric">Valid TF: {((confluence.valid_coverage ?? 0) * 100).toFixed(0)}%</span>
        </div>
      </div>
      <p className="signal-explanation confluence-explanation">Why this signal: {getExplanation(confluence)}</p>

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
        <div className="metric">
          <span className="metric-label">Quality Score</span>
          <span className="metric-value">
            {typeof confluence.quality_weighted_score === 'number'
              ? `${confluence.quality_weighted_score > 0 ? '+' : ''}${confluence.quality_weighted_score.toFixed(0)}`
              : '0'}
          </span>
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
                  {directionIcon(signal.direction)}
                </span>
                {typeof signal.score === 'number' && (
                  <span className="signal-score">{signal.score > 0 ? '+' : ''}{signal.score.toFixed(0)}</span>
                )}
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
