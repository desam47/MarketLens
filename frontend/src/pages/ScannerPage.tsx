import React, { useCallback, useEffect, useMemo, useState } from 'react';
import api, { FilterSpec, ScanResult, Watchlist } from '../services/api';
import { FilterBuilder } from '../components/FilterBuilder';
import { NamedRankingsPanel } from '../components/NamedRankingsPanel';
import { MarketDataFreshnessBadge } from '../components/MarketDataFreshnessBadge';
import { ErrorBanner } from '../components/ErrorBanner';

interface ScannerPageProps {
  onSelectSymbol: (symbol: string) => void;
}

interface SavedPreset {
  name: string;
  filters: FilterSpec[];
  match: 'AND' | 'OR';
}

const QUICK_PRESETS: SavedPreset[] = [
  {
    name: 'Breakout + Volume',
    filters: [
      { type: 'breakout', params: { lookback: '20', min_breakout_pct: 0 } },
      { type: 'volume_expansion', params: { min_ratio: 1.5 } },
    ],
    match: 'AND',
  },
  {
    name: 'Oversold Reversals',
    filters: [
      { type: 'oversold_reversal', params: { threshold: 35, min_rsi_rise: 2 } },
      { type: 'volume_expansion', params: { min_ratio: 1.2 } },
    ],
    match: 'AND',
  },
  {
    name: 'MTF Alignment',
    filters: [{ type: 'mtf_alignment', params: { min_timeframes: 3, min_confidence: 0.5 } }],
    match: 'AND',
  },
  {
    name: 'Relative Strength Leaders',
    filters: [
      { type: 'relative_strength_above', params: { benchmark: 'SPY', min_pct: 1 } },
      { type: 'price_above_ma', params: { period: '50', min_distance_pct: 0 } },
    ],
    match: 'AND',
  },
  {
    name: 'Volatility Squeeze',
    filters: [
      { type: 'volatility_contraction', params: { max_ratio: 0.75 } },
      { type: 'price_above_ma', params: { period: '20', min_distance_pct: 0 } },
    ],
    match: 'AND',
  },
];

const PRESETS_KEY = 'marketlens.scanner.presets';

function signalLabel(signal: string): string {
  return signal.replace(/_/g, ' ').toLowerCase().replace(/(^| )\S/g, c => c.toUpperCase());
}

function matchReason(result: ScanResult): string {
  const details: string[] = [];
  const indicators = result.indicator_values || {};
  const explanation = result.explanation;
  if (typeof explanation?.confidence === 'number') details.push(`${explanation.confidence.toFixed(0)}% confidence`);
  if (typeof explanation?.timeframe_agreement?.alignment_pct === 'number' && explanation.timeframe_agreement.total > 0) {
    details.push(`TF ${explanation.timeframe_agreement.alignment_pct.toFixed(0)}% aligned`);
  }
  const primaryRs = Object.entries(indicators).find(([key, value]) => key.startsWith('rs_pct_') && typeof value === 'number');
  if (primaryRs) details.push(`RS ${Number(primaryRs[1]) >= 0 ? '+' : ''}${Number(primaryRs[1]).toFixed(1)}%`);
  if (typeof indicators.volume_ratio === 'number') details.push(`Vol ${indicators.volume_ratio.toFixed(1)}×`);
  if (typeof indicators.volatility_ratio === 'number') details.push(`Volatility ${indicators.volatility_ratio.toFixed(2)}×`);
  const signalText = result.signals.length > 0 ? result.signals.slice(0, 3).map(signalLabel).join(' · ') : '';
  if (signalText || details.length > 0) return [signalText, ...details].filter(Boolean).join(' · ');
  if (result.total_score > 0) return `Positive composite score (+${result.total_score.toFixed(1)})`;
  if (result.total_score < 0) return `Negative composite score (${result.total_score.toFixed(1)})`;
  return 'Matched the selected filters';
}

export function ScannerPage({ onSelectSymbol }: ScannerPageProps) {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [selectedWatchlist, setSelectedWatchlist] = useState<number | null>(null);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [results, setResults] = useState<ScanResult[]>([]);
  const [presets, setPresets] = useState<SavedPreset[]>(() => {
    try { return JSON.parse(localStorage.getItem(PRESETS_KEY) || '[]'); } catch { return []; }
  });
  const [currentFilters, setCurrentFilters] = useState<FilterSpec[]>([]);
  const [currentMatch, setCurrentMatch] = useState<'AND' | 'OR'>('AND');
  const [presetName, setPresetName] = useState('');
  const [presetVersion, setPresetVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [symbolsLoading, setSymbolsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRunScan, setHasRunScan] = useState(false);

  const fetchWatchlists = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getWatchlists();
      setWatchlists(data);
      if (data.length > 0) setSelectedWatchlist(data[0].id);
      else setSelectedWatchlist(null);
    } catch (err: any) {
      setWatchlists([]);
      setSelectedWatchlist(null);
      setError(err?.message || 'Failed to load watchlists');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchWatchlists();
  }, [fetchWatchlists]);

  const fetchSymbols = useCallback(async () => {
    if (selectedWatchlist == null) {
      setSymbols([]);
      setSymbolsLoading(false);
      setResults([]);
      setHasRunScan(false);
      return;
    }
    setSymbolsLoading(true);
    setError(null);
    setResults([]);
    setHasRunScan(false);
    try {
      const rows = await api.getWatchlistSymbols(selectedWatchlist);
      setSymbols(rows.filter(row => row.is_enabled).map(row => row.symbol));
    } catch (err: any) {
      setSymbols([]);
      setError(err?.message || 'Failed to load watchlist symbols');
    } finally {
      setSymbolsLoading(false);
    }
  }, [selectedWatchlist]);

  useEffect(() => {
    void fetchSymbols();
  }, [fetchSymbols]);

  const handleResults = useCallback((next: ScanResult[]) => {
    setResults(next);
    setHasRunScan(true);
    setError(null);
  }, []);

  const handleFiltersChange = useCallback((filters: FilterSpec[], match: 'AND' | 'OR') => {
    setCurrentFilters(filters);
    setCurrentMatch(match);
  }, []);

  const savePreset = () => {
    const name = presetName.trim();
    if (!name || currentFilters.length === 0) return;
    const next = [...presets.filter(p => p.name.toLowerCase() !== name.toLowerCase()), {
      name, filters: currentFilters, match: currentMatch,
    }];
    setPresets(next);
    localStorage.setItem(PRESETS_KEY, JSON.stringify(next));
    setPresetName('');
  };

  const loadPreset = (preset: SavedPreset) => {
    setCurrentFilters(preset.filters);
    setCurrentMatch(preset.match);
    setPresetVersion(v => v + 1);
  };

  const deletePreset = (name: string) => {
    const next = presets.filter(p => p.name !== name);
    setPresets(next);
    localStorage.setItem(PRESETS_KEY, JSON.stringify(next));
  };

  const sortedResults = useMemo(
    () => [...results].sort((a, b) => b.total_score - a.total_score),
    [results],
  );

  const retryLoad = () => {
    if (selectedWatchlist == null) void fetchWatchlists();
    else void fetchSymbols();
  };

  return (
    <div className="scanner-page">
      <div className="page-title-row">
        <div>
          <h1>Scanner</h1>
          <p className="subtitle">Build repeatable scans from your existing watchlist data.</p>
        </div>
        <select className="scanner-watchlist-select" value={selectedWatchlist ?? ''} onChange={e => setSelectedWatchlist(Number(e.target.value))}>
          {watchlists.length === 0 && <option value="">No watchlists</option>}
          {watchlists.map(watchlist => <option key={watchlist.id} value={watchlist.id}>{watchlist.name}</option>)}
        </select>
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} onRetry={retryLoad} />}
      {loading || symbolsLoading ? (
        <div className="card scanner-empty" role="status">Loading scan universe…</div>
      ) : watchlists.length === 0 ? (
        <div className="card scanner-empty"><strong>No watchlists yet.</strong><br />Create a watchlist and add symbols before running a scan.</div>
      ) : symbols.length === 0 ? (
        <div className="card scanner-empty"><strong>No enabled symbols.</strong><br />Add or enable symbols in this watchlist to scan them.</div>
      ) : null}

      <div className="scanner-layout">
        <div>
          <div className="card scanner-builder-card">
            <FilterBuilder
              key={presetVersion}
              symbols={symbols}
              onResults={handleResults}
              onClear={() => { setResults([]); setHasRunScan(false); }}
              initialFilters={currentFilters}
              initialMatch={currentMatch}
              onFiltersChange={handleFiltersChange}
            />
            <div className="scanner-presets">
              <div className="scanner-presets-header"><h2>Saved scans</h2><span>{presets.length}</span></div>
              <div className="scanner-quick-presets">
                <span className="label">Quick scans</span>
                <div className="scanner-quick-preset-buttons">
                  {QUICK_PRESETS.map(preset => (
                    <button className="btn btn-ghost btn-sm" key={preset.name} onClick={() => loadPreset(preset)}>
                      {preset.name}
                    </button>
                  ))}
                </div>
              </div>
              <div className="scanner-save-row">
                <input value={presetName} onChange={e => setPresetName(e.target.value)} placeholder="Name this scan" />
                <button className="btn btn-secondary btn-sm" onClick={savePreset} disabled={!presetName.trim() || currentFilters.length === 0}>Save</button>
              </div>
              {presets.map(preset => (
                <div className="scanner-preset-row" key={preset.name}>
                  <button className="btn btn-ghost btn-sm" onClick={() => loadPreset(preset)}>{preset.name}</button>
                  <span>{preset.filters.length} filter{preset.filters.length === 1 ? '' : 's'} · {preset.match}</span>
                  <button className="scanner-preset-delete" onClick={() => deletePreset(preset.name)} aria-label={`Delete ${preset.name}`}>×</button>
                </div>
              ))}
            </div>
          </div>

          <div className="card scanner-results-card">
            <div className="scanner-results-header"><h2>Matches</h2><span>{results.length} symbols</span></div>
            {sortedResults.length === 0 ? (
              <p className="empty-state">
                {hasRunScan || currentFilters.length > 0
                  ? 'No symbols matched this scan. Adjust a filter or choose another saved scan.'
                  : 'Choose a quick scan or add filters to search your watchlist.'}
              </p>
            ) : (
              <div className="scanner-results-list">
                {sortedResults.map(result => (
                  <button className="scanner-result-row" key={result.symbol} onClick={() => onSelectSymbol(result.symbol)}>
                    <strong>{result.symbol}</strong>
                    <span className={result.total_score >= 0 ? 'scanner-score-positive' : 'scanner-score-negative'}>{result.total_score >= 0 ? '+' : ''}{result.total_score.toFixed(1)}</span>
                    <span className="scanner-result-reason">{matchReason(result)}</span>
                    <MarketDataFreshnessBadge
                      dataStatus={result.quote?.data_status}
                      freshness={result.explanation?.data_freshness?.status}
                      timestamp={result.quote?.timestamp ?? result.timestamp}
                    />
                    <span className="scanner-result-price">{result.quote?.price != null ? `$${result.quote.price.toFixed(2)}` : '—'}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <NamedRankingsPanel symbols={symbols} onSelectSymbol={onSelectSymbol} />
      </div>
    </div>
  );
}

export default ScannerPage;
