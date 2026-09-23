/**
 * FilterBuilder — Phase 10 composable filter builder.
 *
 * Renders an add/remove list of filter expressions and fires
 * POST /api/scanner/filter when the user applies.  The parent
 * receives results via onResults and can display them however
 * it wants.
 */
import React, { useCallback, useEffect, useState } from 'react';
import api, { FilterSpec } from '../services/api';
import { useDebounce } from '../hooks/useDebounce';

// ---------------------------------------------------------------------------
// Filter definitions — what each filter type needs as input
// ---------------------------------------------------------------------------

interface FilterDef {
  type: string;
  label: string;
  description: string;
  /** Controls the param inputs shown when this filter is selected. */
  paramDefs: ParamDef[];
}

interface ParamDef {
  key: string;
  label: string;
  type: 'number' | 'string' | 'select';
  default: number | string;
  min?: number;
  max?: number;
  step?: number;
  options?: { value: string; label: string }[];
}

/** Every filter known to the backend, with their param schemas. */
const FILTER_DEFS: FilterDef[] = [
  {
    type: 'market_session',
    label: 'Market Session',
    description: 'Scanner timestamp falls inside a selected US equity session',
    paramDefs: [{
      key: 'session', label: 'Session', type: 'select', default: 'regular',
      options: [
        { value: 'after_hours', label: 'After-hours (16:00–20:00 ET)' },
        { value: 'premarket', label: 'Premarket (04:00–09:30 ET)' },
        { value: 'regular', label: 'Regular (09:30–16:00 ET)' },
      ],
    }],
  },
  {
    type: 'trend_score_gt',
    label: 'Trend Score >',
    description: 'Total score is above threshold',
    paramDefs: [{ key: 'threshold', label: 'Threshold', type: 'number', default: 30, min: -100, max: 100, step: 5 }],
  },
  {
    type: 'trend_score_lt',
    label: 'Trend Score <',
    description: 'Total score is below threshold',
    paramDefs: [{ key: 'threshold', label: 'Threshold', type: 'number', default: -30, min: -100, max: 100, step: 5 }],
  },
  {
    type: 'timeframe_direction',
    label: 'Timeframe Direction',
    description: 'Trend direction on a specific timeframe',
    paramDefs: [
      {
        key: 'timeframe', label: 'Timeframe', type: 'select', default: '1d',
        // Only the timeframes the scanner actually populates. The trend engine
        // fills trend_signals for ONE_MINUTE/FIVE_MINUTE/FIFTEEN_MINUTE/
        // ONE_HOUR/FOUR_HOUR/ONE_DAY (backend/scanner/scanner.py) — a filter on
        // 30m or 1w resolved fine on the backend but could never match a
        // symbol, so those options were removed rather than left as silent
        // never-match choices.
        options: [
          { value: '1m', label: '1m' }, { value: '5m', label: '5m' },
          { value: '15m', label: '15m' }, { value: '1h', label: '1h' },
          { value: '4h', label: '4h' }, { value: '1d', label: '1d' },
        ],
      },
      {
        key: 'direction', label: 'Direction', type: 'select', default: 'uptrend',
        options: [{ value: 'uptrend', label: 'Uptrend' }, { value: 'downtrend', label: 'Downtrend' }],
      },
      { key: 'min_confidence', label: 'Min Confidence', type: 'number', default: 0.5, min: 0, max: 1, step: 0.1 },
    ],
  },
  {
    type: 'daily_bullish',
    label: 'Daily Bullish',
    description: 'Daily trend is bullish (uptrend)',
    paramDefs: [{ key: 'min_confidence', label: 'Min Confidence', type: 'number', default: 0.5, min: 0, max: 1, step: 0.1 }],
  },
  {
    type: 'daily_bearish',
    label: 'Daily Bearish',
    description: 'Daily trend is bearish (downtrend)',
    paramDefs: [{ key: 'min_confidence', label: 'Min Confidence', type: 'number', default: 0.5, min: 0, max: 1, step: 0.1 }],
  },
  {
    type: 'min_timeframe_bullish',
    label: '≥N Bullish Timeframes',
    description: 'At least N timeframes are bullish',
    paramDefs: [
      { key: 'min_count', label: 'Min Count', type: 'number', default: 3, min: 1, max: 7, step: 1 },
      { key: 'min_confidence', label: 'Min Confidence', type: 'number', default: 0.5, min: 0, max: 1, step: 0.1 },
    ],
  },
  {
    type: 'min_timeframe_bearish',
    label: '≥N Bearish Timeframes',
    description: 'At least N timeframes are bearish',
    paramDefs: [
      { key: 'min_count', label: 'Min Count', type: 'number', default: 3, min: 1, max: 7, step: 1 },
      { key: 'min_confidence', label: 'Min Confidence', type: 'number', default: 0.5, min: 0, max: 1, step: 0.1 },
    ],
  },
  {
    type: 'mtf_alignment',
    label: 'MTF Alignment',
    description: 'All timeframes aligned on the same direction',
    paramDefs: [
      { key: 'min_timeframes', label: 'Min Timeframes', type: 'number', default: 3, min: 1, max: 7, step: 1 },
      { key: 'min_confidence', label: 'Min Confidence', type: 'number', default: 0.5, min: 0, max: 1, step: 0.1 },
    ],
  },
  {
    type: 'rsi_oversold',
    label: 'RSI Oversold',
    description: 'RSI is below threshold (oversold)',
    paramDefs: [{ key: 'threshold', label: 'Threshold', type: 'number', default: 30, min: 0, max: 100, step: 5 }],
  },
  {
    type: 'rsi_overbought',
    label: 'RSI Overbought',
    description: 'RSI is above threshold (overbought)',
    paramDefs: [{ key: 'threshold', label: 'Threshold', type: 'number', default: 70, min: 0, max: 100, step: 5 }],
  },
  {
    type: 'macd_bullish',
    label: 'MACD Bullish',
    description: 'MACD histogram is positive',
    paramDefs: [],
  },
  {
    type: 'macd_bearish',
    label: 'MACD Bearish',
    description: 'MACD histogram is negative',
    paramDefs: [],
  },
  {
    type: 'high_volume',
    label: 'High Volume',
    description: 'Volume is above minimum threshold',
    paramDefs: [{ key: 'min_volume', label: 'Min Volume', type: 'number', default: 1000000, min: 0, step: 100000 }],
  },
  {
    type: 'signal_present',
    label: 'Signal Present',
    description: 'Result contains at least one of the given signals',
    paramDefs: [{ key: 'signals', label: 'Signals (comma-separated)', type: 'string', default: '' }],
  },
  {
    type: 'price_above',
    label: 'Price Above',
    description: 'Last price is above threshold',
    paramDefs: [{ key: 'price', label: 'Price', type: 'number', default: 0, min: 0, step: 0.01 }],
  },
  {
    type: 'price_below',
    label: 'Price Below',
    description: 'Last price is below threshold',
    paramDefs: [{ key: 'price', label: 'Price', type: 'number', default: 0, min: 0, step: 0.01 }],
  },
  {
    type: 'adx_strong',
    label: 'ADX Strong',
    description: 'ADX is above threshold (strong trend)',
    paramDefs: [{ key: 'threshold', label: 'Threshold', type: 'number', default: 25, min: 0, max: 100, step: 1 }],
  },
  {
    type: 'oversold_reversal',
    label: 'Oversold Reversal',
    description: 'RSI was oversold and is turning higher with price confirmation',
    paramDefs: [
      { key: 'threshold', label: 'Max Prior RSI', type: 'number', default: 35, min: 0, max: 100, step: 1 },
      { key: 'min_rsi_rise', label: 'Min RSI Rise', type: 'number', default: 2, min: 0, max: 50, step: 1 },
    ],
  },
  {
    type: 'breakout',
    label: 'Breakout',
    description: 'Price is above the prior high for the selected lookback',
    paramDefs: [
      {
        key: 'lookback', label: 'Lookback', type: 'select', default: '20',
        options: [{ value: '10', label: '10 bars' }, { value: '20', label: '20 bars' }, { value: '50', label: '50 bars' }],
      },
      { key: 'min_breakout_pct', label: 'Min Breakout %', type: 'number', default: 0, min: 0, max: 100, step: 0.1 },
    ],
  },
  {
    type: 'breakdown',
    label: 'Breakdown',
    description: 'Price is below the prior low for the selected lookback',
    paramDefs: [
      {
        key: 'lookback', label: 'Lookback', type: 'select', default: '20',
        options: [{ value: '10', label: '10 bars' }, { value: '20', label: '20 bars' }, { value: '50', label: '50 bars' }],
      },
      { key: 'min_breakdown_pct', label: 'Min Breakdown %', type: 'number', default: 0, min: 0, max: 100, step: 0.1 },
    ],
  },
  {
    type: 'volume_expansion',
    label: 'Volume Expansion',
    description: 'Current volume is above its trailing average',
    paramDefs: [{ key: 'min_ratio', label: 'Min Ratio', type: 'number', default: 1.5, min: 1, max: 20, step: 0.1 }],
  },
  {
    type: 'price_above_ma',
    label: 'Price Above SMA',
    description: 'Price is above a selected simple moving average',
    paramDefs: [
      {
        key: 'period', label: 'Period', type: 'select', default: '20',
        options: [{ value: '20', label: 'SMA 20' }, { value: '50', label: 'SMA 50' }, { value: '200', label: 'SMA 200' }],
      },
      { key: 'min_distance_pct', label: 'Min Distance %', type: 'number', default: 0, min: 0, max: 100, step: 0.1 },
    ],
  },
  {
    type: 'price_below_ma',
    label: 'Price Below SMA',
    description: 'Price is below a selected simple moving average',
    paramDefs: [
      {
        key: 'period', label: 'Period', type: 'select', default: '20',
        options: [{ value: '20', label: 'SMA 20' }, { value: '50', label: 'SMA 50' }, { value: '200', label: 'SMA 200' }],
      },
      { key: 'min_distance_pct', label: 'Min Distance %', type: 'number', default: 0, min: 0, max: 100, step: 0.1 },
    ],
  },
  {
    type: 'volatility_contraction',
    label: 'Volatility Contraction',
    description: 'Five-bar realized volatility is below its 20-bar baseline',
    paramDefs: [{ key: 'max_ratio', label: 'Max 5/20 Ratio', type: 'number', default: 0.75, min: 0.05, max: 1, step: 0.05 }],
  },
  {
    type: 'volatility_expansion',
    label: 'Volatility Expansion',
    description: 'Five-bar realized volatility is above its 20-bar baseline',
    paramDefs: [{ key: 'min_ratio', label: 'Min 5/20 Ratio', type: 'number', default: 1.25, min: 1, max: 10, step: 0.05 }],
  },
  {
    type: 'relative_strength_above',
    label: 'Relative Strength Above',
    description: 'Symbol outperforms a benchmark over the configured lookback',
    paramDefs: [
      {
        key: 'benchmark', label: 'Benchmark', type: 'select', default: 'SPY',
        options: [{ value: 'SPY', label: 'SPY' }, { value: 'QQQ', label: 'QQQ' }],
      },
      { key: 'min_pct', label: 'Min Outperformance %', type: 'number', default: 1, min: -100, max: 100, step: 0.5 },
    ],
  },
  {
    type: 'relative_strength_below',
    label: 'Relative Strength Below',
    description: 'Symbol underperforms a benchmark over the configured lookback',
    paramDefs: [
      {
        key: 'benchmark', label: 'Benchmark', type: 'select', default: 'SPY',
        options: [{ value: 'SPY', label: 'SPY' }, { value: 'QQQ', label: 'QQQ' }],
      },
      { key: 'max_pct', label: 'Max Relative Strength %', type: 'number', default: -1, min: -100, max: 100, step: 0.5 },
    ],
  },
  {
    type: 'price_above_vwap', label: 'Price Above VWAP',
    description: 'Price is above the 20-bar volume-weighted average price',
    paramDefs: [{ key: 'min_distance_pct', label: 'Min Distance %', type: 'number', default: 0, min: 0, max: 100, step: 0.1 }],
  },
  {
    type: 'price_below_vwap', label: 'Price Below VWAP',
    description: 'Price is below the 20-bar volume-weighted average price',
    paramDefs: [{ key: 'min_distance_pct', label: 'Min Distance %', type: 'number', default: 0, min: 0, max: 100, step: 0.1 }],
  },
  {
    type: 'ema_alignment', label: 'EMA Alignment',
    description: 'EMA 9, 20, and 50 are aligned in one direction',
    paramDefs: [{ key: 'direction', label: 'Direction', type: 'select', default: 'bullish', options: [{ value: 'bullish', label: 'Bullish' }, { value: 'bearish', label: 'Bearish' }] }],
  },
  {
    type: 'ema_crossover', label: 'EMA Crossover',
    description: 'EMA 9 is above or below EMA 20',
    paramDefs: [{ key: 'direction', label: 'Direction', type: 'select', default: 'bullish', options: [{ value: 'bullish', label: 'Bullish' }, { value: 'bearish', label: 'Bearish' }] }],
  },
  {
    type: 'tight_spread',
    label: 'Tight Spread',
    description: 'Live best-bid/offer spread is within the selected width',
    paramDefs: [{ key: 'max_spread_bps', label: 'Max Spread (bps)', type: 'number', default: 10, min: 0, max: 1000, step: 0.5 }],
  },
  {
    type: 'spread_widening',
    label: 'Spread Widening',
    description: 'Live best-bid/offer spread widened from its prior update',
    paramDefs: [{ key: 'min_change_bps', label: 'Min Widening (bps)', type: 'number', default: 3, min: 0, max: 1000, step: 0.5 }],
  },
  {
    type: 'tape_pressure',
    label: 'Tape Pressure',
    description: 'Recent Time & Sales flow is buy-side or sell-side',
    paramDefs: [{
      key: 'direction', label: 'Direction', type: 'select', default: 'buy',
      options: [{ value: 'buy', label: 'Buy Side' }, { value: 'sell', label: 'Sell Side' }],
    }],
  },
  {
    type: 'large_print_activity',
    label: 'Large-Print Activity',
    description: 'Recent tape contains block-sized prints',
    paramDefs: [{ key: 'min_blocks', label: 'Min Large Prints', type: 'number', default: 1, min: 1, max: 100, step: 1 }],
  },
  {
    type: 'trade_rate_spike',
    label: 'Trade-Rate Spike',
    description: 'Recent trade arrival rate exceeds its baseline',
    paramDefs: [{ key: 'min_acceleration', label: 'Min Acceleration', type: 'number', default: 1.5, min: 1, max: 20, step: 0.1 }],
  },
  {
    type: 'bid_ask_imbalance',
    label: 'Bid/Ask Imbalance',
    description: 'Displayed BBO size is tilted toward bids or asks',
    paramDefs: [
      {
        key: 'direction', label: 'Side', type: 'select', default: 'bid',
        options: [{ value: 'bid', label: 'Bid Heavy' }, { value: 'ask', label: 'Ask Heavy' }],
      },
      { key: 'min_imbalance', label: 'Min Imbalance', type: 'number', default: 0.2, min: 0.01, max: 1, step: 0.05 },
    ],
  },
  {
    type: 'live_volume_acceleration',
    label: 'Live Volume Acceleration',
    description: 'Recent tape volume rate exceeds its baseline',
    paramDefs: [{ key: 'min_acceleration', label: 'Min Acceleration', type: 'number', default: 1.5, min: 1, max: 20, step: 0.1 }],
  },
  {
    type: 'exclude_earnings_within_days',
    label: 'Exclude Upcoming Earnings',
    description: 'Exclude symbols with a provider-estimated earnings date inside this window',
    paramDefs: [{
      key: 'days', label: 'Exclude Within', type: 'select', default: '7',
      options: [{ value: '3', label: '3 days' }, { value: '7', label: '7 days' }, { value: '14', label: '14 days' }, { value: '30', label: '30 days' }],
    }],
  },
];

const TYPE_TO_DEF = Object.fromEntries(FILTER_DEFS.map(d => [d.type, d]));
const SORTED_FILTER_DEFS = [...FILTER_DEFS].sort((left, right) => left.label.localeCompare(right.label));

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface FilterBuilderProps {
  /** All watched symbols (to pass as optional filter hint to the backend). */
  symbols: string[];
  /** Called when the user applies the filter and the API returns results. */
  onResults: (results: ReturnType<typeof api.applyFilter> extends Promise<infer T> ? T : never) => void;
  /** Called when the user wants to clear the filter and go back to the default view. */
  onClear: () => void;
  /** Optional controlled starting state, used by saved scanner presets. */
  initialFilters?: FilterSpec[];
  initialMatch?: 'AND' | 'OR';
  /** Open the editor when another UI surface loads a generated preview. */
  initiallyOpen?: boolean;
  onFiltersChange?: (filters: FilterSpec[], match: 'AND' | 'OR') => void;
}

// ---------------------------------------------------------------------------
// FilterBuilder
// ---------------------------------------------------------------------------

export function FilterBuilder({
  symbols,
  onResults,
  onClear,
  initialFilters = [],
  initialMatch = 'AND',
  initiallyOpen = false,
  onFiltersChange,
}: FilterBuilderProps) {
  const [filters, setFilters] = useState<FilterSpec[]>(initialFilters);
  const [match, setMatch] = useState<'AND' | 'OR'>(initialMatch);
  const [isOpen, setIsOpen] = useState(initiallyOpen);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeFilterType, setActiveFilterType] = useState<string>('daily_bullish');

  useEffect(() => {
    onFiltersChange?.(filters, match);
  }, [filters, match, onFiltersChange]);

  // Auto-apply filters after a short debounce so rapid tweaks
  // (param changes, add/remove) don't require manual Apply clicks.
  // The manual Apply button is still available for explicit re-runs.
  const debouncedFilters = useDebounce(filters, 600);
  const debouncedMatch = useDebounce(match, 600);

  useEffect(() => {
    // Skip whenever there are no filters — this covers both the initial
    // mount AND the user clearing/removing the last filter. Regardless of
    // match (AND/OR), an empty filter list always resolves to TrueFilter
    // on the backend (matches every symbol), so firing here would
    // silently re-activate filter mode ~600ms after Clear/remove-last
    // with match='OR', undoing it (found live 2026-09-18).
    if (debouncedFilters.length === 0) return;
    const run = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const result = await api.applyFilter({ filters: debouncedFilters, match: debouncedMatch }, symbols);
        onResults(result);
        if (result.length === 0) setError('No symbols matched the filter.');
      } catch (e: any) {
        setError(e.message || 'Filter request failed');
      } finally {
        setIsLoading(false);
      }
    };
    run();
  }, [debouncedFilters, debouncedMatch, symbols, onResults]);

  // Build a default FilterSpec from the selected type
  const buildDefault = useCallback((type: string): FilterSpec => {
    const def = TYPE_TO_DEF[type];
    if (!def) return { type, params: {} };
    const params: Record<string, any> = {};
    for (const p of def.paramDefs) {
      params[p.key] = p.default;
    }
    return { type, params };
  }, []);

  // Add a new filter with defaults
  const addFilter = useCallback(() => {
    setFilters(prev => [...prev, buildDefault(activeFilterType)]);
  }, [activeFilterType, buildDefault]);

  // Remove a filter by index
  const removeFilter = useCallback((index: number) => {
    const next = filters.filter((_, i) => i !== index);
    setFilters(next);
    // An empty filter list intentionally skips auto-apply (it means
    // "browse all" in the backend), so clear the parent result set here
    // instead of leaving matches from the deleted filter on screen.
    if (next.length === 0) {
      setError(null);
      onClear();
    }
  }, [filters, onClear]);

  // Update a param on a filter
  const updateParam = useCallback((index: number, paramKey: string, value: any) => {
    setFilters(prev => prev.map((f, i) =>
      i === index ? { ...f, params: { ...f.params, [paramKey]: value } } : f
    ));
  }, []);

  // Apply — call the backend
  const apply = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.applyFilter({ filters, match }, symbols);
      onResults(result);
      if (result.length === 0) setError('No symbols matched the filter.');
    } catch (e: any) {
      setError(e.message || 'Filter request failed');
    } finally {
      setIsLoading(false);
    }
  }, [filters, match, symbols, onResults]);

  const clear = useCallback(() => {
    setFilters([]);
    setError(null);
    onClear();
  }, [onClear]);

  const hasFilters = filters.length > 0;

  const activeDef = TYPE_TO_DEF[activeFilterType];

  return (
    <div className={`filter-builder ${isOpen ? 'open' : ''}`}>
      {/* Header / toggle */}
      <div className="filter-builder-header" onClick={() => setIsOpen(v => !v)}>
        <span className="filter-builder-title">
          Filter Builder
          {hasFilters && <span className="filter-count">{filters.length}</span>}
        </span>
        <span className="filter-builder-chevron">{isOpen ? '▲' : '▼'}</span>
      </div>

      {/* Body */}
      {isOpen && (
        <div className="filter-builder-body">
          {/* Active filters list */}
          {hasFilters && (
            <div className="filter-list">
              <div className="filter-list-header">
                <span className="filter-list-label">Active Filters ({filters.length})</span>
                <span className="filter-match-toggle">
                  Match:
                  <select
                    value={match}
                    onChange={e => setMatch(e.target.value as 'AND' | 'OR')}
                    onClick={e => e.stopPropagation()}
                    className="filter-match-select"
                  >
                    <option value="AND">ALL (AND)</option>
                    <option value="OR">ANY (OR)</option>
                  </select>
                </span>
              </div>
              {filters.map((f, idx) => {
                const def = TYPE_TO_DEF[f.type];
                return (
                  <div key={idx} className="filter-item">
                    <div className="filter-item-header">
                      <span className="filter-item-type">{def?.label ?? f.type}</span>
                      <button
                        className="filter-item-remove"
                        onClick={e => { e.stopPropagation(); removeFilter(idx); }}
                        title="Remove filter"
                      >
                        ✕
                      </button>
                    </div>
                    {def?.paramDefs && def.paramDefs.length > 0 && (
                      <div className="filter-item-params">
                        {def.paramDefs.map(p => (
                          <div key={p.key} className="filter-param">
                            <label className="filter-param-label">{p.label}</label>
                            {p.type === 'select' ? (
                              <select
                                value={f.params[p.key] ?? p.default}
                                onChange={e => updateParam(idx, p.key, e.target.value)}
                                className="filter-param-select"
                              >
                                {p.options?.map(o => (
                                  <option key={o.value} value={o.value}>{o.label}</option>
                                ))}
                              </select>
                            ) : p.type === 'string' ? (
                              <input
                                type="text"
                                value={f.params[p.key] ?? ''}
                                onChange={e => updateParam(idx, p.key, e.target.value)}
                                className="filter-param-input"
                                placeholder={p.label}
                              />
                            ) : (
                              <input
                                type="number"
                                value={f.params[p.key] ?? p.default}
                                min={p.min}
                                max={p.max}
                                step={p.step}
                                onChange={e => updateParam(idx, p.key, parseFloat(e.target.value))}
                                className="filter-param-input"
                              />
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Add filter row */}
          <div className="filter-add-row">
            <select
              className="filter-type-select"
              value={activeFilterType}
              onChange={e => setActiveFilterType(e.target.value)}
              onClick={e => e.stopPropagation()}
              title={activeDef?.description}
            >
              {SORTED_FILTER_DEFS.map(def => (
                <option key={def.type} value={def.type}>{def.label}</option>
              ))}
            </select>
            <button
              className="btn btn-secondary btn-sm"
              onClick={e => { e.stopPropagation(); addFilter(); }}
            >
              + Add Filter
            </button>
          </div>

          {/* Action buttons */}
          <div className="filter-actions">
            <button
              className="btn btn-primary"
              onClick={apply}
              disabled={isLoading || !hasFilters}
            >
              {isLoading ? 'Applying…' : `Apply Filter${hasFilters ? ` (${filters.length})` : ''}`}
            </button>
            {hasFilters && (
              <button className="btn btn-ghost" onClick={clear}>
                Clear
              </button>
            )}
          </div>

          {error && <div className="filter-error">{error}</div>}
        </div>
      )}
    </div>
  );
}
