import React, { useState, useEffect, useCallback, memo, lazy, Suspense } from 'react';
import api, {
  Bar,
  Divergence,
  ScanResult,
  SRLevel,
  Transition,
} from '../services/api';
import { parseET, formatETDate, formatETDateTime } from '../components/chartMath';
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
const ChatPanel = lazy(() =>
  import('../components/ChatPanel').then(m => ({ default: m.ChatPanel })),
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
  today_high: "Today's High",
  today_low: "Today's Low",
  prev_day_high: 'Prev Day High',
  prev_day_low: 'Prev Day Low',
  this_week_high: "This Week's High",
  this_week_low: "This Week's Low",
  prev_week_high: 'Prev Week High',
  prev_week_low: 'Prev Week Low',
  all_time_high: 'All Time High',
  all_time_low: 'All Time Low',
  pivot_high: 'Pivot High',
  pivot_low: 'Pivot Low',
  swing_high: 'Swing High',
  swing_low: 'Swing Low',
  consolidation_zone: 'Zone',
};

// Display order for the S/R panel — today, prev day, this week, prev week, all time.
// Lower number = higher in the list. Levels not in this map are appended at the end.
const srTypeOrder: Record<string, number> = {
  today_high: 0,
  prev_day_high: 1,
  this_week_high: 2,
  prev_week_high: 3,
  all_time_high: 4,
  today_low: 0,
  prev_day_low: 1,
  this_week_low: 2,
  prev_week_low: 3,
  all_time_low: 4,
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
const TransitionsPanel = memo(function TransitionsPanel({
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
  const recent = transitions.slice(0, 20); // already newest first, limit 20

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
        <div className="timestamp">Updated: {formatETDateTime(latestTimestamp)}</div>
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
                  {t.timestamp && <span>{formatETDate(t.timestamp)}</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});

// --- S/R Levels panel ---
const SRPanel = memo(function SRPanel({ levels, latestClose }: { levels: SRLevel[]; latestClose: number | null }) {
  // Only show levels on the *correct* side of current price: a "high"-type
  // level is resistance only if it's above current price (if it's below,
  // price has broken through it and it's no longer meaningful resistance).
  // Similarly a "low"-type level is support only if it's below current
  // price. This prevents showing a column of "Support" prices that are
  // actually above the current price (e.g. after a gap-down).
  // Consolidation zones are dropped from the per-side list — they are a
  // separate concept (a cluster of swings) and not a directional S/R level.
  const resistances = levels
    .filter(l =>
      !['consolidation_zone', 'pivot_high', 'pivot_low', 'swing_high', 'swing_low'].includes(l.type) && l.type.includes('high')
    )
    .sort((a, b) => (srTypeOrder[a.type] ?? 99) - (srTypeOrder[b.type] ?? 99));
  const supports = levels
    .filter(l =>
      !['consolidation_zone', 'pivot_high', 'pivot_low', 'swing_high', 'swing_low'].includes(l.type) && l.type.includes('low')
    )
    .sort((a, b) => (srTypeOrder[a.type] ?? 99) - (srTypeOrder[b.type] ?? 99));

  return (
    <div className="card analysis-card">
      <h2>Price Range</h2>
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
                    {items.slice(0, 8).map((l, i) => {
                      // Resistance distances are positive (above), support
                      // distances are negative (below) by sign convention.
                      const absDist = l.distance_from_price != null
                        ? Math.abs(l.distance_from_price)
                        : null;
                      const dist = absDist == null
                        ? null
                        : label === 'Support' ? -absDist : absDist;
                      return (
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
                          <td className="sr-dist">{pct(dist)}</td>
                        </tr>
                      );
                    })}
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
});

// --- Divergences panel ---
const DivergencesPanel = memo(function DivergencesPanel({ divergences }: { divergences: Divergence[] }) {
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
                  {d.timestamp && <span>{formatETDate(d.timestamp)}</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});

// --- Bars table ---
const BarsTable = memo(function BarsTable({ bars }: { bars: Bar[] }) {
  // Backend returns newest→oldest (desc=True), so index 0 is already latest
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
              {bars.map((b, i) => {
                // Each bar shows % change from the next older bar (bars[i+1])
                const prev = bars[i + 1];
                // Positive chg = price went UP from older bar to this bar
                const chg = prev && prev.close > 0
                  ? ((b.close - prev.close) / prev.close * 100)
                  : null;
                const c = barColor(b.close, b.open);
                return (
                  <tr key={i}>
                    <td>{b.timestamp ? (() => {
                        // Explicit timeZone — without it these format using
                        // the viewer's browser/OS zone instead of ET.
                        const d = parseET(b.timestamp);
                        const date = d.toLocaleDateString('en-US', { timeZone: 'America/New_York' });
                        const time = d.toLocaleTimeString('en-US', {
                          timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false,
                        });
                        return `${date} ${time}`;
                    })() : '—'}</td>
                    <td>${strPrice(b.open)}</td>
                    <td>${strPrice(b.high)}</td>
                    <td>${strPrice(b.low)}</td>
                    <td style={{ color: c }}>${strPrice(b.close)}</td>
                    <td>{(b.volume / 1000).toFixed(0)}k</td>
                    <td style={{ color: c }}>
                      {chg !== null ? `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%` : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
});

// --- Main page ---
export function SymbolPage({ symbol, onSymbolChange }: SymbolPageProps) {
  const [quote, setQuote] = useState<any>(null);

  const [transitions, setTransitions] = useState<Transition[]>([]);
  const [latestScore, setLatestScore] = useState(0);
  const [latestTimestamp, setLatestTimestamp] = useState<string | null>(null);
  const [transitionsLoading, setTransitionsLoading] = useState(true);

  const [srLevels, setSrLevels] = useState<SRLevel[]>([]);
  const [latestClose, setLatestClose] = useState<number | null>(null);
  const [srLoading, setSrLoading] = useState(true);

  const [divergences, setDivergences] = useState<Divergence[]>([]);
  const [divergencesLoading, setDivergencesLoading] = useState(true);

  const [bars, setBars] = useState<Bar[]>([]);
  const [barsLoading, setBarsLoading] = useState(true);

  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scanLoading, setScanLoading] = useState(true);

  const [timeframe, setTimeframe] = useState('1d');
  const [chartMode, setChartMode] = useState<'single' | 'multi'>('single');

  const fetchQuote = useCallback(async () => {
    try {
      const data = await api.getQuote(symbol);
      setQuote(data);
    } catch (err: any) {
      console.error('Failed to load quote:', err);
    }
  }, [symbol]);

  const fetchTransitions = useCallback(async () => {
    setTransitionsLoading(true);
    try {
      const data = await api.getTransitions(symbol, timeframe);
      setTransitions(data?.transitions || []);
      setLatestScore(data?.latest_score ?? 0);
      setLatestTimestamp(data?.latest_timestamp ?? null);
    } catch (err: any) {
      console.error('Failed to load transitions:', err);
    } finally {
      setTransitionsLoading(false);
    }
  }, [symbol, timeframe]);

  const fetchSR = useCallback(async () => {
    setSrLoading(true);
    try {
      const data = await api.getSupportResistance(symbol, timeframe);
      setSrLevels(data?.levels || []);
      setLatestClose(data?.latest_close ?? null);
    } catch (err: any) {
      console.error('Failed to load S/R levels:', err);
    } finally {
      setSrLoading(false);
    }
  }, [symbol, timeframe]);

  const fetchDivergences = useCallback(async () => {
    setDivergencesLoading(true);
    try {
      const data = await api.getDivergences(symbol, timeframe);
      setDivergences(data?.divergences || []);
    } catch (err: any) {
      console.error('Failed to load divergences:', err);
    } finally {
      setDivergencesLoading(false);
    }
  }, [symbol, timeframe]);

  const fetchBars = useCallback(async () => {
    setBarsLoading(true);
    try {
      // `bars` feeds both CandlestickChart (a canvas-based lightweight-
      // charts instance — comfortably handles many thousands of points)
      // and BarsTable (a plain, non-virtualized HTML <table> — must stay
      // bounded, see the slice passed to it below). Fetch the full
      // history for the chart; the table takes its own bounded slice
      // rather than limiting this fetch itself, which previously starved
      // the chart down to the table's row cap.
      const data = await api.getAnalysisBars(symbol, timeframe, 10000);
      setBars(data?.bars || []);
    } catch (err: any) {
      console.error('Failed to load bars:', err);
    } finally {
      setBarsLoading(false);
    }
  }, [symbol, timeframe]);

  const fetchScan = useCallback(async () => {
    setScanLoading(true);
    try {
      const data = await api.getScanResult(symbol);
      setScanResult(data);
    } catch (err: any) {
      console.error('Failed to load scan:', err);
    } finally {
      setScanLoading(false);
    }
  }, [symbol]);

  const handleRefresh = useCallback(() => {
    fetchQuote();
    fetchTransitions();
    fetchSR();
    fetchDivergences();
    fetchBars();
    fetchScan();
  }, [fetchQuote, fetchTransitions, fetchSR, fetchDivergences, fetchBars, fetchScan]);

  useEffect(() => {
    fetchQuote();
  }, [fetchQuote]);

  useEffect(() => {
    fetchTransitions();
  }, [fetchTransitions]);

  useEffect(() => {
    fetchSR();
  }, [fetchSR]);

  useEffect(() => {
    fetchDivergences();
  }, [fetchDivergences]);

  useEffect(() => {
    fetchBars();
  }, [fetchBars]);

  useEffect(() => {
    fetchScan();
  }, [fetchScan]);

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
          <SymbolInput symbol={symbol} onChange={onSymbolChange} onSubmit={handleRefresh} />
          <button className="btn" onClick={handleRefresh}>↻ Refresh</button>
        </div>
      </div>

      <div className="symbol-grid">
        <div className={transitionsLoading && transitions.length === 0 ? 'card-loading-skeleton' : ''}>
          <TransitionsPanel
            transitions={transitions}
            latestScore={latestScore}
            latestTimestamp={latestTimestamp}
            symbol={symbol}
            timeframe={timeframe}
          />
        </div>
        <div className={srLoading && srLevels.length === 0 ? 'card-loading-skeleton' : ''}>
          <SRPanel levels={srLevels} latestClose={latestClose} />
        </div>
        <div className={divergencesLoading && divergences.length === 0 ? 'card-loading-skeleton' : ''}>
          <DivergencesPanel divergences={divergences} />
        </div>
        <div className={scanLoading && !scanResult ? 'card-loading-skeleton' : ''}>
          <ScoreDetailPanel
            totalScore={scanResult?.total_score ?? 0}
            scores={scanResult?.scores ?? {}}
            signals={scanResult?.signals}
            symbol={symbol}
          />
        </div>
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
        <Suspense fallback={<div className="panel-skeleton">Loading chat…</div>}>
          <ChatPanel symbol={symbol} />
        </Suspense>
        <div className={scanLoading && !scanResult ? 'card-loading-skeleton' : ''}>
          <MTFScoreGrid
            trendSignals={(scanResult?.trend_signals ?? {}) as TrendSignalsMap}
            symbol={symbol}
          />
        </div>
        {chartMode === 'single' ? (
          <CandlestickChart bars={bars} symbol={symbol} transitions={transitions} initialActiveOverlays={['supertrend']} />
        ) : (
          <MultiTimeframeChartGrid symbol={symbol} timeframes={DEFAULT_GRID_TIMEFRAMES} />
        )}
        <div className={barsLoading && bars.length === 0 ? 'card-loading-skeleton' : ''}>
          {/* Table stays bounded to the most recent rows (plain HTML
              table, not virtualized) — the chart above gets the full
              `bars` fetched by fetchBars. */}
          <BarsTable bars={bars.slice(0, 500)} />
        </div>
      </div>
    </div>
  );
}
