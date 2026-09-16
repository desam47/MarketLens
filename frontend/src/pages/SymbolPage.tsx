import React, { useState, useEffect, useCallback, useRef, memo, lazy, Suspense } from 'react';
import api, {
  Bar,
  Divergence,
  ScanResult,
  SRLevel,
  PriceHistoryItem,
  TapeSnapshot,
  Transition,
} from '../services/api';
import { parseET, formatETDate, formatETDateTime } from '../components/chartMath';
import { CandlestickChart } from '../components/CandlestickChart';
import { MultiTimeframeChartGrid } from '../components/MultiTimeframeChartGrid';
import { MTFScoreGrid, TrendSignalsMap } from '../components/MTFScoreGrid';
import { ScoreDetailPanel } from '../components/ScoreDetailPanel';
import { TapePressureCard } from '../components/TapePressureCard';
import { SymbolInput } from '../components/SymbolInput';
import { DEFAULT_GRID_TIMEFRAMES, DEFAULT_TIMEFRAME, TIMEFRAMES, TIMEFRAME_LABELS } from '../utils/timeframeUtils';

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
// AITemplatesPanel and ChatPanel moved to the AI Hub page (2026-09-10).

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
  week_52_high: '52 Week High',
  week_52_low: '52 Week Low',
  pivot_pp: 'PP',
  pivot_r1: 'R1',
  pivot_r2: 'R2',
  pivot_r3: 'R3',
  pivot_s1: 'S1',
  pivot_s2: 'S2',
  pivot_s3: 'S3',
  swing_high: 'Swing High',
  swing_low: 'Swing Low',
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

/** Color a change value by its own sign, independent of candle direction.
A bar can be a down-candle (close < open) yet still post a positive change
vs the prior bar's close — coloring by candle color would mis-color that
change. Used for the Change / Change % columns (and matching the header's
"Last Close" delta coloring). */
function changeColor(v: number | null | undefined): string {
  if (v == null) return 'inherit';
  return v >= 0 ? '#10b981' : '#ef4444';
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

/** Format volume as k or M — shared by BarsTable and Price History, whose
52-week row sums a full year of volume, far larger than a single bar's. */
function formatVolume(v: number | null | undefined): string {
  if (v == null) return '—';
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  return `${(v / 1000).toFixed(0)}k`;
}

/** Format a signed "Change" value with the same precision `strPrice` uses for
prices. A hardcoded toFixed(2) rounds sub-cent moves on penny stocks to
"0.00" while the paired Change % (computed from the same raw number) still
shows a real figure — the two columns then contradict each other. */
function formatChange(v: number | null | undefined): string {
  if (v == null) return '—';
  const sign = v >= 0 ? '+' : '-';
  return sign + strPrice(Math.abs(v)).replace(/^0$/, '0.00');
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
      <p className="panel-caveat">
        Score is a close-price z-score vs a 21-bar SMA (±2σ), not the
        blended TrendEngine score.
      </p>
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

// --- Support & Resistance levels panel ---
// Renders /price-range data (SRLevel[]) as a classic pivot table: one row
// per level, ordered R3 -> R1 -> PP -> S1 -> S3 by the API. Resistance
// rows (R1-R3) are red, support rows (S1-S3) and the PP pivot are green,
// and the row nearest the last close is highlighted as the actionable one.
const SRPanel = memo(function SRPanel({
  levels,
  latestClose,
}: {
  levels: SRLevel[];
  latestClose: number | null;
}) {
  const NEAR_PCT = 3.0;
  const isResistance = (t: string) => t.startsWith('pivot_r');

  return (
    <div className="card analysis-card">
      <h2>Support &amp; Resistance</h2>
      {levels.length === 0 ? (
        <p className="empty-state">No levels detected</p>
      ) : (
        <table className="sr-table sr-pivot-table">
          <thead>
            <tr>
              <th>Level</th>
              <th>Price</th>
              <th>Str</th>
              <th>Touch</th>
              <th>Dist</th>
            </tr>
          </thead>
          <tbody>
            {levels.map((l, i) => {
              const dist = latestClose
                ? ((l.price - latestClose) / latestClose) * 100
                : null;
              const isNear = dist != null && Math.abs(dist) <= NEAR_PCT;
              const color = isResistance(l.type) ? '#ef4444' : '#10b981';
              return (
                <tr key={i} className={isNear ? 'sr-row-near' : undefined}>
                  <td className="sr-type" style={{ color, fontWeight: 600 }}>
                    {srTypeLabel[l.type] || l.type}
                  </td>
                  <td className="sr-price">${strPrice(l.price)}</td>
                  <td className="sr-strength">
                    <div className="mini-bar">
                      <div
                        className="mini-fill"
                        style={{ width: `${(l.strength * 100).toFixed(0)}%`, backgroundColor: color }}
                      />
                    </div>
                  </td>
                  <td className="sr-touch">{l.touch_count}</td>
                  <td className="sr-dist">{pct(dist)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
});

// --- Price History panel ---
// Renders the calendar-anchored reference levels (today / prev day / this week /
// prev week / 52-week high & low) that the S/R pivot table intentionally omits,
// paired into one row per period. High is blue, Low is purple.
const PriceHistoryPanel = memo(function PriceHistoryPanel({
  history,
  latestClose,
  change,
  changePercent,
}: {
  history: PriceHistoryItem[];
  latestClose: number | null;
  change?: number | null;
  changePercent?: number | null;
}) {
  // Pair the high/low of each period into a single row.
  const byPeriod: Record<string, { high?: PriceHistoryItem; low?: PriceHistoryItem }> = {};
  for (const h of history) {
    const period = h.type.replace(/_(high|low)$/, "");
    byPeriod[period] = byPeriod[period] || {};
    if (h.type.endsWith("_high")) byPeriod[period].high = h;
    else byPeriod[period].low = h;
  }
  // Preserve engine's display order (today -> prev day -> this week -> ...).
  const rows = history
    .filter((h) => h.type.endsWith("_high"))
    .map((h) => {
      const period = h.type.replace(/_high$/, "");
      const low = byPeriod[period].low;
      // open/close/volume/change are identical on the high and low entries
      // for a period (the backend duplicates them onto both sides).
      return {
        period,
        high: byPeriod[period].high,
        low,
        label: h.label.replace(" High", ""),
        open: h.open,
        close: h.close,
        volume: h.volume,
        change: h.change,
        changePercent: h.change_pct,
      };
    });

  return (
    <div className="card analysis-card">
      <h2>Price History</h2>
      {latestClose != null && (
        <div className="current-price">
          <span className="price-label">Last Close</span>
          <span className="price-value">${strPrice(latestClose)}</span>
          {change != null && (
            <span
              className="price-change"
              style={{ color: change >= 0 ? '#10b981' : '#ef4444' }}
            >
              {formatChange(change)} ({changePercent != null
                ? `${changePercent >= 0 ? '+' : ''}${changePercent.toFixed(2)}%`
                : '—'})
            </span>
          )}
        </div>
      )}
      {rows.length === 0 ? (
        <p className="empty-state">No history levels available</p>
      ) : (
        <table className="sr-table price-history-table">
          <thead>
            <tr>
              <th>Period</th>
              <th>Open</th>
              <th>High</th>
              <th>Low</th>
              <th>Close</th>
              <th>Change</th>
              <th>Change %</th>
              <th>Volume</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="ph-label">{r.label}</td>
                <td className="ph-open">
                  {r.open != null ? `$${strPrice(r.open)}` : "—"}
                </td>
                <td className="ph-high">
                  {r.high ? `$${strPrice(r.high.price)}` : "—"}
                </td>
                <td className="ph-low">
                  {r.low ? `$${strPrice(r.low.price)}` : "—"}
                </td>
                <td className="ph-close">
                  {r.close != null ? `$${strPrice(r.close)}` : "—"}
                </td>
                <td style={{ color: changeColor(r.change) }}>
                  {formatChange(r.change)}
                </td>
                <td style={{ color: changeColor(r.changePercent) }}>
                  {r.changePercent != null
                    ? `${r.changePercent >= 0 ? '+' : ''}${r.changePercent.toFixed(2)}%`
                    : "—"}
                </td>
                <td>{formatVolume(r.volume)}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
                <th>Change</th>
                <th>Change %</th>
                <th>Volume</th>
              </tr>
            </thead>
            <tbody>
              {bars.map((b, i) => {
                // Each bar shows change from the next older bar (bars[i+1]);
                // bars arrive newest→oldest. Absolute $ change + % change.
                const prev = bars[i + 1];
                const chgAbs =
                  prev && typeof prev.close === 'number' && typeof b.close === 'number'
                    ? b.close - prev.close
                    : null;
                const chg =
                  chgAbs != null && prev.close > 0
                    ? (chgAbs / prev.close) * 100
                    : null;
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
                    <td>${strPrice(b.close)}</td>
                    <td style={{ color: changeColor(chgAbs) }}>
                      {formatChange(chgAbs)}
                    </td>
                    <td style={{ color: changeColor(chg) }}>
                      {chg !== null ? `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%` : '—'}
                    </td>
                    <td>{formatVolume(b.volume)}</td>
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
  const [priceHistory, setPriceHistory] = useState<PriceHistoryItem[]>([]);
  const [latestClose, setLatestClose] = useState<number | null>(null);
  const [srLoading, setSrLoading] = useState(true);

  const [divergences, setDivergences] = useState<Divergence[]>([]);
  const [divergencesLoading, setDivergencesLoading] = useState(true);

  const [bars, setBars] = useState<Bar[]>([]);
  const [barsLoading, setBarsLoading] = useState(true);

  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scanLoading, setScanLoading] = useState(true);
  // Tracks the symbol a fetchScan call was issued for, so a slow response
  // for a symbol the user has since navigated away from can't overwrite
  // the currently-displayed symbol's Score & Confidence data — same
  // stale-response guard as tapeRequestSymbolRef below.
  const scanRequestSymbolRef = useRef<string>(symbol);

  const [tape, setTape] = useState<TapeSnapshot | null>(null);
  const [tapeDisabled, setTapeDisabled] = useState(false);
  const [tapeError, setTapeError] = useState<string | null>(null);
  // Tracks the symbol a fetchTape call was issued for, so a slow response
  // for a symbol the user has since navigated away from can't overwrite
  // the currently-displayed symbol's tape data.
  const tapeRequestSymbolRef = useRef<string>(symbol);

  const [timeframe, setTimeframe] = useState<string>(DEFAULT_TIMEFRAME);
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
      const data = await api.getPriceRange(symbol, timeframe);
      setSrLevels(data?.levels || []);
      setPriceHistory(data?.price_history || []);
      setLatestClose(data?.latest_close ?? null);
    } catch (err: any) {
      console.error('Failed to load price-range levels:', err);
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

  const fetchTape = useCallback(async () => {
    const requestedSymbol = symbol;
    tapeRequestSymbolRef.current = requestedSymbol;
    try {
      const data = await api.getTape(requestedSymbol);
      // The user may have switched symbols while this request was in
      // flight — a stale response for a symbol we're no longer showing
      // must not clobber the current one.
      if (tapeRequestSymbolRef.current !== requestedSymbol) return;
      setTape(data.snapshot);
      setTapeDisabled(false);
      setTapeError(null);
    } catch (err: any) {
      if (tapeRequestSymbolRef.current !== requestedSymbol) return;
      // 503 = tape streaming off; anything else is a real error.
      if (String(err?.message || '').includes('503')) {
        setTapeDisabled(true);
        setTapeError(null);
      } else {
        console.error('Failed to load tape:', err);
        setTapeError(err?.message || 'Failed to load tape data');
      }
      setTape(null);
    }
  }, [symbol]);

  const fetchScan = useCallback(async () => {
    const requestedSymbol = symbol;
    scanRequestSymbolRef.current = requestedSymbol;
    setScanLoading(true);
    try {
      const data = await api.getScanResult(requestedSymbol);
      if (scanRequestSymbolRef.current !== requestedSymbol) return;
      setScanResult(data);
    } catch (err: any) {
      if (scanRequestSymbolRef.current !== requestedSymbol) return;
      console.error('Failed to load scan:', err);
    } finally {
      if (scanRequestSymbolRef.current === requestedSymbol) setScanLoading(false);
    }
  }, [symbol]);

  const handleRefresh = useCallback(() => {
    fetchQuote();
    fetchTransitions();
    fetchSR();
    fetchDivergences();
    fetchBars();
    fetchScan();
    fetchTape();
  }, [fetchQuote, fetchTransitions, fetchSR, fetchDivergences, fetchBars, fetchScan, fetchTape]);

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

  useEffect(() => {
    fetchTape();
    const id = setInterval(fetchTape, 15000);
    return () => clearInterval(id);
  }, [fetchTape]);

  // Quote/bars/scan/etc. otherwise only ever fetch once (on mount or
  // symbol/timeframe change) — a page left open goes stale forever
  // until a manual "Refresh" click. Confirmed live: at 12:10 the 5m bar
  // chart was still showing 12:00 as the latest candle even though the
  // backend already had 12:05 available, because nothing had re-asked
  // for it. handleRefresh() re-fetches everything this page shows
  // (tape included — its own 15s interval above already covers it, so
  // this just re-fetches it slightly more often too, which is harmless).
  useEffect(() => {
    const id = setInterval(handleRefresh, 30000);
    return () => clearInterval(id);
  }, [handleRefresh]);

  // Browsers throttle setInterval heavily in backgrounded/inactive tabs,
  // so a tab left in the background can sit on stale data far longer
  // than 30s until its throttled timer eventually fires again. Refetch
  // immediately on tab-focus-regain to close that gap.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') handleRefresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [handleRefresh]);

  const currentPrice = quote?.price ?? quote?.currentPrice ?? null;
  const priceDisplay = currentPrice != null ? `$${strPrice(currentPrice)}` : '—';

  // Period-over-period change for the S/R panel: compare the latest bar's
  // close to the prior bar's close (bars arrive newest→oldest). The backend
  // Quote model has no change field, so we derive it here — the same delta
  // the BarsTable shows per row.
  const barsChange =
    bars.length >= 2 && typeof bars[0].close === 'number' && typeof bars[1].close === 'number' && bars[1].close !== 0
      ? bars[0].close - bars[1].close
      : null;
  const barsChangePct = barsChange != null ? (barsChange / bars[1].close) * 100 : null;

  return (
    <div className="symbol-page">
      <div className="dashboard-header">
        <div>
          <h1>{symbol} Analysis</h1>
          <p className="subtitle">
            {priceDisplay}
            {barsChange != null && (
              <span style={{ color: barsChange >= 0 ? '#10b981' : '#ef4444', marginLeft: 8 }}>
                {formatChange(barsChange)} ({barsChangePct != null
                  ? `${barsChangePct >= 0 ? '+' : ''}${barsChangePct.toFixed(2)}%`
                  : '—'})
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
        <PriceHistoryPanel
          history={priceHistory}
          latestClose={latestClose}
          change={barsChange}
          changePercent={barsChangePct}
        />
        <div className={srLoading && srLevels.length === 0 ? 'card-loading-skeleton' : ''}>
          <SRPanel
            levels={srLevels}
            latestClose={latestClose}
          />
        </div>
        <div className={transitionsLoading && transitions.length === 0 ? 'card-loading-skeleton' : ''}>
          <TransitionsPanel
            transitions={transitions}
            latestScore={latestScore}
            latestTimestamp={latestTimestamp}
            symbol={symbol}
            timeframe={timeframe}
          />
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
        <TapePressureCard tape={tape} disabled={tapeDisabled} error={tapeError} />
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
