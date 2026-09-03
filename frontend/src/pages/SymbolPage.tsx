import React, { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import api, {
  Bar,
  Divergence,
  ScanResult,
  SRLevel,
  Transition,
} from '../services/api';
import { CandlestickChart } from '../components/CandlestickChart';
import { MultiTimeframeChartGrid } from '../components/MultiTimeframeChartGrid';
import { MTFScoreGrid, TrendSignalsMap } from '../components/MTFScoreGrid';
import { ScoreDetailPanel } from '../components/ScoreDetailPanel';
import { SymbolInput } from '../components/SymbolInput';
import { DEFAULT_GRID_TIMEFRAMES, TIMEFRAMES, TIMEFRAME_LABELS } from '../utils/timeframeUtils';

// Heavy panels are loaded on demand so the initial route bundle stays small.
// Each panel makes its own API calls and isn't needed for the first paint.
const AIAnalysisPanel = lazy(() =>
  import('../components/AIAnalysisPanel').then(m => ({ default: m.AIAnalysisPanel })),
);
const NewsPanel = lazy(() =>
  import('../components/NewsPanel').then(m => ({ default: m.NewsPanel })),
);
const FundamentalsPanel = lazy(() =>
  import('../components/FundamentalsPanel').then(m => ({ default: m.FundamentalsPanel })),
);
const OptionsPanel = lazy(() =>
  import('../components/OptionsPanel').then(m => ({ default: m.OptionsPanel })),
);
const CustomIndicatorsPanel = lazy(() =>
  import('../components/CustomIndicatorsPanel').then(m => ({ default: m.CustomIndicatorsPanel })),
);
const DrawingToolsPanel = lazy(() =>
  import('../components/DrawingToolsPanel').then(m => ({ default: m.DrawingToolsPanel })),
);
const AITemplatesPanel = lazy(() =>
  import('../components/AITemplatesPanel').then(m => ({ default: m.AITemplatesPanel })),
);

interface SymbolPageProps {
  symbol: string;
  onSymbolChange: (symbol: string) => void;
}

// Color maps
const transitionColors: Record<string, string> = {
  bullish_reversal: '#10b981',
  bullish_acceleration: '#22c55e',
  bullish_weakening: '#84cc16',
  bearish_reversal: '#ef4444',
  bearish_acceleration: '#dc2626',
  bearish_weakening: '#f97316',
};

const directionBadge: Record<string, string> = {
  bullish: '🐂',
  bearish: '🐻',
  neutral: '➡',
};

const srTypeLabel: Record<string, string> = {
  swing_high: 'Swing High',
  swing_low: 'Swing Low',
  pivot_high: 'Pivot High',
  pivot_low: 'Pivot Low',
  prev_day_high: 'Prev Day High',
  prev_day_low: 'Prev Day Low',
  prev_week_high: 'Prev Week High',
  prev_week_low: 'Prev Week Low',
  consolidation_zone: 'Zone',
};

function formatDelta(delta: number): string {
  return (delta > 0 ? '+' : '') + delta.toFixed(1);
}

function formatScore(score: number): string {
  const sign = score > 0 ? '+' : '';
  return sign + score.toFixed(1);
}

function pct(v: number | null | undefined): string {
  if (v == null) return '—';
  return v.toFixed(2) + '%';
}

function barColor(close: number, open: number): string {
  return close >= open ? '#10b981' : '#ef4444';
}

function str(v: number | null | undefined): string {
  if (v == null) return '—';
  return v.toFixed(2);
}

/** Format a price with up to 4 decimals, trimming trailing zeros (e.g. 0.29, 761.0542). */
function strPrice(v: number | null | undefined): string {
  if (v == null) return '—';
  return v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

// --- Transitions panel ---
function TransitionsPanel({
  transitions,
  latestScore,
  latestTimestamp,
  symbol,
  timeframe,
}: {
  transitions: Transition[];
  latestScore: number;
  latestTimestamp: string | null;
  symbol: string;
  timeframe: string;
}) {
  const scoreColor = latestScore > 0 ? '#10b981' : latestScore < 0 ? '#ef4444' : '#9ca3af';
  const recent = transitions.slice(-20).reverse(); // newest first, limit 20

  return (
    <div className="card analysis-card">
      <div className="card-header-row">
        <h2>Trend Transitions</h2>
        <span className="timeframe-tag">{timeframe}</span>
      </div>
      <div className="score-bar">
        <span className="score-label">Score</span>
        <div className="progress-bar" style={{ flex: 1 }}>
          <div
            className="progress-fill"
            style={{
              width: `${Math.min(Math.abs(latestScore), 100)}%`,
              backgroundColor: scoreColor,
            }}
          />
        </div>
        <span className="score-value" style={{ color: scoreColor }}>
          {formatScore(latestScore)}
        </span>
      </div>
      {latestTimestamp && (
        <div className="timestamp">Updated: {new Date(latestTimestamp).toLocaleString()}</div>
      )}
      {recent.length === 0 ? (
        <p className="empty-state">No transitions detected</p>
      ) : (
        <div className="transitions-list">
          {recent.map((t, i) => {
            const color = transitionColors[t.type] || '#9ca3af';
            return (
              <div key={i} className="transition-item" style={{ borderLeftColor: color }}>
                <div className="transition-top">
                  <span className="transition-type" style={{ color }}>
                    {t.type.replace(/_/g, ' ')}
                  </span>
                  <span className="transition-score">
                    {formatScore(t.previous_score)} → {formatScore(t.current_score)}
                  </span>
                </div>
                <div className="transition-meta">
                  <span>{directionBadge[t.direction] || '—'} {t.direction}</span>
                  <span>Δ {formatDelta(t.delta)}</span>
                  <span>{t.magnitude.toFixed(1)} mag</span>
                  {t.timestamp && <span>{new Date(t.timestamp).toLocaleDateString()}</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- S/R Levels panel ---
function SRPanel({ levels, latestClose }: { levels: SRLevel[]; latestClose: number | null }) {
  const resistances = levels.filter(l => l.type.includes('high') || l.type === 'swing_high' || l.type === 'pivot_high' || l.type === 'consolidation_zone');
  const supports = levels.filter(l => l.type.includes('low') || l.type === 'swing_low' || l.type === 'pivot_low');

  return (
    <div className="card analysis-card">
      <h2>Support &amp; Resistance</h2>
      {latestClose != null && (
        <div className="current-price">
          <span className="price-label">Last</span>
          <span className="price-value">${strPrice(latestClose)}</span>
        </div>
      )}
      {levels.length === 0 ? (
        <p className="empty-state">No levels detected</p>
      ) : (
        <div className="sr-grid">
          {(['Resistance', 'Support'] as const).map(label => {
            const items = label === 'Resistance' ? resistances : supports;
            const color = label === 'Resistance' ? '#ef4444' : '#10b981';
            return (
              <div key={label} className="sr-column">
                <h3 style={{ color }}>{label}</h3>
                <table className="sr-table">
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th>Price</th>
                      <th>Str</th>
                      <th>Dist</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.slice(0, 6).map((l, i) => (
                      <tr key={i}>
                        <td className="sr-type">{srTypeLabel[l.type] || l.type}</td>
                        <td className="sr-price">${strPrice(l.price)}</td>
                        <td className="sr-strength">
                          <div className="mini-bar">
                            <div
                              className="mini-fill"
                              style={{ width: `${(l.strength * 100).toFixed(0)}%`, backgroundColor: color }}
                            />
                          </div>
                        </td>
                        <td className="sr-dist">{pct(l.distance_from_price)}</td>
                      </tr>
                    ))}
                    {items.length === 0 && (
                      <tr><td colSpan={4} className="empty-cell">—</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- Divergences panel ---
function DivergencesPanel({ divergences }: { divergences: Divergence[] }) {
  return (
    <div className="card analysis-card">
      <h2>Divergences</h2>
      {divergences.length === 0 ? (
        <p className="empty-state">No divergences detected</p>
      ) : (
        <div className="divergences-list">
          {divergences.slice(0, 15).map((d, i) => {
            const color = d.direction === 'bullish' ? '#10b981' : '#ef4444';
            return (
              <div key={i} className="divergence-item" style={{ borderLeftColor: color }}>
                <div className="divergence-top">
                  <span className="divergence-type" style={{ color }}>
                    {d.type.replace(/_/g, ' ')}
                  </span>
                  <span className="divergence-strength">
                    {(d.strength * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="divergence-meta">
                  <span>{directionBadge[d.direction] || '—'} {d.direction}</span>
                  <span>price: {str(d.pivot_b_price)}</span>
                  <span>ind: {str(d.pivot_b_indicator)}</span>
                  {d.timestamp && <span>{new Date(d.timestamp).toLocaleDateString()}</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- Bars table ---
function BarsTable({ bars }: { bars: Bar[] }) {
  const shown = bars.slice(-30).reverse(); // newest first
  return (
    <div className="card analysis-card">
      <h2>Recent Bars</h2>
      {bars.length === 0 ? (
        <p className="empty-state">No bars available</p>
      ) : (
        <div className="bars-table-wrap">
          <table className="bars-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Open</th>
                <th>High</th>
                <th>Low</th>
                <th>Close</th>
                <th>Vol</th>
                <th>%</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((b, i) => {
                const chg = i < shown.length - 1 && shown[i + 1].close > 0
                  ? ((b.close - shown[i + 1].close) / shown[i + 1].close * 100)
                  : 0;
                const c = barColor(b.close, b.open);
                return (
                  <tr key={i}>
                    <td>{b.timestamp ? new Date(b.timestamp).toLocaleDateString() : '—'}</td>
                    <td>${strPrice(b.open)}</td>
                    <td>${strPrice(b.high)}</td>
                    <td>${strPrice(b.low)}</td>
                    <td style={{ color: c }}>${strPrice(b.close)}</td>
                    <td>{(b.volume / 1000).toFixed(0)}k</td>
                    <td style={{ color: c }}>{chg > 0 ? '+' : ''}{chg.toFixed(2)}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// --- Main page ---
export function SymbolPage({ symbol, onSymbolChange }: SymbolPageProps) {
  const [quote, setQuote] = useState<any>(null);
  const [transitions, setTransitions] = useState<Transition[]>([]);
  const [latestScore, setLatestScore] = useState(0);
  const [latestTimestamp, setLatestTimestamp] = useState<string | null>(null);
  const [srLevels, setSrLevels] = useState<SRLevel[]>([]);
  const [latestClose, setLatestClose] = useState<number | null>(null);
  const [divergences, setDivergences] = useState<Divergence[]>([]);
  const [bars, setBars] = useState<Bar[]>([]);
  const [timeframe, setTimeframe] = useState('1d');
  const [chartMode, setChartMode] = useState<'single' | 'multi'>('single');
  const [loading, setLoading] = useState(true);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);

  const safe = useCallback(async <T,>(fn: () => Promise<T>): Promise<{ data: T | null; error: string | null }> => {
    try {
      return { data: await fn(), error: null };
    } catch (e: any) {
      return { data: null, error: e?.message || 'Request failed' };
    }
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    const [q, tr, sr, dv, br, sc] = await Promise.all([
      safe(() => api.getQuote(symbol)),
      safe(() => api.getTransitions(symbol, timeframe)),
      safe(() => api.getSupportResistance(symbol, timeframe)),
      safe(() => api.getDivergences(symbol, timeframe)),
      safe(() => api.getAnalysisBars(symbol, timeframe)),
      safe(() => api.getScanResult(symbol)),
    ]);
    setQuote(q.data);
    setTransitions(tr.data?.transitions || []);
    setLatestScore(tr.data?.latest_score ?? 0);
    setLatestTimestamp(tr.data?.latest_timestamp ?? null);
    setSrLevels(sr.data?.levels || []);
    setLatestClose(sr.data?.latest_close ?? null);
    setDivergences(dv.data?.divergences || []);
    setBars(br.data?.bars || []);
    setScanResult(sc.data ?? null);
    setLoading(false);
  }, [symbol, timeframe, safe]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const currentPrice = quote?.price ?? quote?.currentPrice ?? null;
  const priceDisplay = currentPrice != null ? `$${strPrice(currentPrice)}` : '—';

  return (
    <div className="symbol-page">
      <div className="dashboard-header">
        <div>
          <h1>{symbol} Analysis</h1>
          <p className="subtitle">
            {priceDisplay}
            {quote?.change != null && (
              <span style={{ color: quote.change >= 0 ? '#10b981' : '#ef4444', marginLeft: 8 }}>
                {quote.change >= 0 ? '+' : ''}{quote.change?.toFixed(2)} ({quote.changePercent?.toFixed(2)}%)
              </span>
            )}
          </p>
        </div>
        <div className="header-actions">
          <select
            className="timeframe-select"
            value={timeframe}
            onChange={e => setTimeframe(e.target.value)}
          >
            {TIMEFRAMES.map(tf => (
              <option key={tf} value={tf}>{TIMEFRAME_LABELS[tf] || tf}</option>
            ))}
          </select>
          <div className="chart-mode-toggle" role="group" aria-label="Chart layout">
            <button
              type="button"
              className={`chart-mode-btn${chartMode === 'single' ? ' active' : ''}`}
              onClick={() => setChartMode('single')}
              title="Single timeframe chart"
            >
              Single
            </button>
            <button
              type="button"
              className={`chart-mode-btn${chartMode === 'multi' ? ' active' : ''}`}
              onClick={() => setChartMode('multi')}
              title="Multi-timeframe grid (4 charts side by side)"
            >
              Multi-TF
            </button>
          </div>
          <SymbolInput symbol={symbol} onChange={onSymbolChange} onSubmit={() => fetchData()} />
          <button className="btn" onClick={fetchData}>↻ Refresh</button>
        </div>
      </div>

      {loading ? (
        <div className="loading-state">Loading analysis...</div>
      ) : (
        <div className="symbol-grid">
          <TransitionsPanel
            transitions={transitions}
            latestScore={latestScore}
            latestTimestamp={latestTimestamp}
            symbol={symbol}
            timeframe={timeframe}
          />
          <SRPanel levels={srLevels} latestClose={latestClose} />
          <DivergencesPanel divergences={divergences} />
          <ScoreDetailPanel
            totalScore={scanResult?.total_score ?? 0}
            scores={scanResult?.scores ?? {}}
            signals={scanResult?.signals}
            symbol={symbol}
          />
          <Suspense fallback={<div className="panel-skeleton">Loading AI analysis…</div>}>
            <AIAnalysisPanel symbol={symbol} timeframe={timeframe} />
          </Suspense>
          <Suspense fallback={<div className="panel-skeleton">Loading news…</div>}>
            <NewsPanel symbol={symbol} />
          </Suspense>
          <Suspense fallback={<div className="panel-skeleton">Loading fundamentals…</div>}>
            <FundamentalsPanel symbol={symbol} />
          </Suspense>
          <Suspense fallback={<div className="panel-skeleton">Loading options…</div>}>
            <OptionsPanel symbol={symbol} />
          </Suspense>
          <Suspense fallback={<div className="panel-skeleton">Loading indicators…</div>}>
            <CustomIndicatorsPanel symbol={symbol} timeframe={timeframe} />
          </Suspense>
          <Suspense fallback={<div className="panel-skeleton">Loading drawings…</div>}>
            <DrawingToolsPanel symbol={symbol} timeframe={timeframe} />
          </Suspense>
          <Suspense fallback={<div className="panel-skeleton">Loading AI templates…</div>}>
            <AITemplatesPanel symbol={symbol} timeframe={timeframe} />
          </Suspense>
          <MTFScoreGrid
            trendSignals={(scanResult?.trend_signals ?? {}) as TrendSignalsMap}
            symbol={symbol}
          />
          {chartMode === 'single' ? (
            <CandlestickChart bars={bars} symbol={symbol} transitions={transitions} />
          ) : (
            <MultiTimeframeChartGrid symbol={symbol} timeframes={DEFAULT_GRID_TIMEFRAMES} />
          )}
          <BarsTable bars={bars} />
        </div>
      )}
    </div>
  );
}
