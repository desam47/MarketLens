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
];

const TYPE_TO_DEF = Object.fromEntries(FILTER_DEFS.map(d => [d.type, d]));

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
  onFiltersChange,
}: FilterBuilderProps) {
  const [filters, setFilters] = useState<FilterSpec[]>(initialFilters);
  const [match, setMatch] = useState<'AND' | 'OR'>(initialMatch);
  const [isOpen, setIsOpen] = useState(false);
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
    setFilters(prev => prev.filter((_, i) => i !== index));
  }, []);

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
              {FILTER_DEFS.map(def => (
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
