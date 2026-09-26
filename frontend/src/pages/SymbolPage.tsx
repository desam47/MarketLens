import React, { useState, useEffect, useCallback, useRef, memo, lazy, Suspense, useMemo } from 'react';
import api, {
  Bar,
  Divergence,
  ScanResult,
  SRLevel,
  PriceHistoryItem,
  TapeSnapshot,
  Transition,
  TrendData,
  MarketQuote,
  CalendarEvent,
  BarUpdateData,
  RealtimeEvent,
  RealtimeConnectionStatus,
  LiveQuoteUpdateData,
  RegimeData,
  StrategyData,
  SectorData,
} from '../services/api';
import { formatETDate, formatETDateTime, parseET } from '../components/chartMath';
import { CandlestickChart } from '../components/CandlestickChart';
import { MultiTimeframeChartGrid } from '../components/MultiTimeframeChartGrid';
// import { TrendByTimeframeGrid, TrendSignalsMap } from '../components/TrendByTimeframeGrid';
import { ConfluenceCard } from '../components/ConfluenceCard';
import { TrendCard } from '../components/TrendCard';
import { RegimeCard } from '../components/RegimeCard';
import { StrategyCard } from '../components/StrategyCard';
import { ScoreDetailPanel } from '../components/ScoreDetailPanel';
import { SignalExplanationPanel } from '../components/SignalExplanationPanel';
import { TapePressureCard } from '../components/TapePressureCard';
import { ErrorBanner } from '../components/ErrorBanner';
import { SymbolInput, type SymbolInputHandle } from '../components/SymbolInput';
import { MarketDataUpdateStatus } from '../components/MarketDataUpdateStatus';
import { EarningsBadge } from '../components/EarningsBadge';
import { DEFAULT_GRID_TIMEFRAMES, DEFAULT_TIMEFRAME, TIMEFRAMES, TIMEFRAME_LABELS } from '../utils/timeframeUtils';
import { readSessionPreference, sessionMatchesPreference, SESSION_PREFERENCE_KEY, type SessionPreference, classifySessionFromTimestamp } from '../utils/marketSession';
import { useMarketSession } from '../hooks/useMarketSession';
import { saveChartState, type ChartState } from '../utils/chartState';

// Heavy panels are loaded on demand so the initial route bundle stays small.
// Each panel makes its own API calls and isn't needed for the first paint.
const AIAnalysisPanel = lazy(() =>
  import('../components/AIAnalysisPanel').then(m => ({ default: m.AIAnalysisPanel })),
);
const CatalystTimelinePanel = lazy(() =>
  import('../components/CatalystTimelinePanel').then(m => ({ default: m.CatalystTimelinePanel })),
);
const OptionsPanel = lazy(() =>
  import('../components/OptionsPanel').then(m => ({ default: m.OptionsPanel })),
);
const CustomIndicatorsPanel = lazy(() =>
  import('../components/CustomIndicatorsPanel').then(m => ({ default: m.CustomIndicatorsPanel })),
);
// DrawingToolsPanel remains available as a component but is not mounted on
// the Symbol page. Options are surfaced here as the provider-backed snapshot.
// AITemplatesPanel and ChatPanel moved to the AI Hub page (2026-09-10).

interface SymbolPageProps {
  symbol: string;
  onSymbolChange: (symbol: string) => void;
  initialTimeframe?: string;
  initialSession?: SessionFilter;
  /** Open the Trade Planning page with this symbol/timeframe prefilled. */
  onPlanTrade?: (symbol: string, timeframe: string) => void;
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

type SessionFilter = SessionPreference;
const SESSION_LABELS: Record<SessionFilter, string> = {
  all: 'All sessions',
  premarket: 'Premarket (04:00–09:30 ET)',
  regular: 'Regular (09:30–16:00 ET)',
  after_hours: 'After-hours (16:00–20:00 ET)',
};

function barSession(bar: Bar): 'premarket' | 'regular' | 'after_hours' {
  return bar.session === 'premarket' || bar.session === 'after_hours' ? bar.session : 'regular';
}

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
  latestCloseTimestamp,
  fetchedAt,
  change,
  changePercent,
}: {
  history: PriceHistoryItem[];
  latestClose: number | null;
  latestCloseTimestamp: string | null;
  fetchedAt: string | null;
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
          <span className="price-label">Last Price</span>
          <span className="price-value">${latestClose.toFixed(4)}</span>
          {change != null && (
            <span
              className="price-change"
              style={{ color: change >= 0 ? '#10b981' : '#ef4444' }}
            >
              {change >= 0 ? '+' : ''}{change.toFixed(4)}
            </span>
          )}
          {changePercent != null && (
            <span className="price-change-pct" style={{ color: changePercent >= 0 ? '#10b981' : '#ef4444' }}>
              ({changePercent >= 0 ? '+' : ''}{changePercent.toFixed(2)}%)
            </span>
          )}
          {latestCloseTimestamp != null && (
            <span className="price-timestamp">
              {formatETDateTime(fetchedAt ?? latestCloseTimestamp)}
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
          {divergences.slice(0, 20).map((d, i) => {
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
const BarsTable = memo(function BarsTable({
  bars,
  sessionFilter,
}: {
  bars: Bar[];
  sessionFilter: SessionFilter;
}) {
  const BARS_PER_PAGE = 50;
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(bars.length / BARS_PER_PAGE));
  const pageStart = page * BARS_PER_PAGE;
  const pageBars = bars.slice(pageStart, pageStart + BARS_PER_PAGE);
  const latestBarTimestamp = bars[0]?.timestamp;

  useEffect(() => {
    setPage(0);
  }, [latestBarTimestamp]);

  // Backend returns newest→oldest (desc=True), so index 0 is already latest
  return (
    <div className="card analysis-card">
      <h2>Recent Bars <span className="health-meta">· {SESSION_LABELS[sessionFilter]}</span></h2>
      {bars.length === 0 ? (
        <p className="empty-state">No bars available</p>
      ) : (
        <div className="bars-table-wrap">
          <table className="bars-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Session</th>
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
              {pageBars.map((b, i) => {
                // Each bar shows change from the next older bar (bars[i+1]);
                // bars arrive newest→oldest. Absolute $ change + % change.
                const prev = bars[pageStart + i + 1];
                const chgAbs =
                  prev && typeof prev.close === 'number' && typeof b.close === 'number'
                    ? b.close - prev.close
                    : null;
                const chg =
                  chgAbs != null && prev.close > 0
                    ? (chgAbs / prev.close) * 100
                    : null;
                return (
                  <tr key={b.timestamp || pageStart + i}>
                    <td>{b.timestamp ? formatETDateTime(b.timestamp) : '—'}</td>
                    <td><span className={`session-badge session-${barSession(b)}`}>{barSession(b).replace('_', ' ')}</span></td>
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
      {bars.length > BARS_PER_PAGE && (
        <div className="bars-pagination">
          <span>Rows {pageStart + 1}–{Math.min(pageStart + BARS_PER_PAGE, bars.length)} of {bars.length}</span>
          <div>
            <button className="btn btn-small" onClick={() => setPage(current => Math.max(0, current - 1))} disabled={page === 0}>Previous</button>
            <button className="btn btn-small" onClick={() => setPage(current => Math.min(pageCount - 1, current + 1))} disabled={page >= pageCount - 1}>Next</button>
          </div>
        </div>
      )}
    </div>
  );
});

/** Merge one streamed trade into the active 1-minute candle.
 *
 * API bars arrive newest first. Webull trade volume is the size of that
 * individual print, so it can safely be accumulated into the local bar;
 * snapshot volume is cumulative and is deliberately not used here.
 */
function mergeLiveTradeIntoMinuteBars(bars: Bar[], live: LiveQuoteUpdateData): Bar[] {
  if (live.price == null || live.event_type !== 'trade') return bars;
  const tickTime = parseET(live.timestamp).getTime();
  if (!Number.isFinite(tickTime)) return bars;

  const bucketMs = Math.floor(tickTime / 60_000) * 60_000;
  const timestamp = new Date(bucketMs).toISOString();
  const size = typeof live.volume === 'number' && Number.isFinite(live.volume)
    ? Math.max(0, live.volume)
    : 0;
  const index = bars.findIndex(bar => Math.floor(parseET(bar.timestamp).getTime() / 60_000) * 60_000 === bucketMs);

  if (index >= 0) {
    const existing = bars[index];
    const updated: Bar = {
      ...existing,
      high: Math.max(existing.high, live.price),
      low: Math.min(existing.low, live.price),
      close: live.price,
      volume: existing.volume + size,
      data_status: 'LIVE',
      source: 'webull_stream',
    };
    const next = [...bars];
    next[index] = updated;
    return next;
  }

  const newBar: Bar = {
    timestamp,
    open: live.price,
    high: live.price,
    low: live.price,
    close: live.price,
    volume: size,
    data_status: 'LIVE',
    source: 'webull_stream',
    session: sessionFromTimestamp(live.timestamp),
  };
  return [...bars, newBar].sort((a, b) => parseET(b.timestamp).getTime() - parseET(a.timestamp).getTime());
}

/** Tag a synthetic bar with its session. Bar.session has no 'closed' value
 * (a real trade bar shouldn't have one) so, mirroring the backend's own
 * classify_bar_session() defensive convention, 'closed'/missing fall back
 * to 'regular' rather than inventing a fourth stored value. */
function sessionFromTimestamp(timestamp: string | null): 'premarket' | 'regular' | 'after_hours' {
  const session = classifySessionFromTimestamp(timestamp);
  return session === 'premarket' || session === 'after_hours' ? session : 'regular';
}

/** Merge the shared backend candle update into the active 1-minute history. */
function mergeLiveBarIntoMinuteBars(bars: Bar[], update: BarUpdateData): Bar[] {
  if (update.timestamp == null || update.close == null) return bars;
  const updateTime = parseET(update.timestamp).getTime();
  if (!Number.isFinite(updateTime)) return bars;
  const bucketMs = Math.floor(updateTime / 60_000) * 60_000;
  const index = bars.findIndex(
    bar => Math.floor(parseET(bar.timestamp).getTime() / 60_000) * 60_000 === bucketMs,
  );
  const existing = index >= 0 ? bars[index] : null;
  const nextBar: Bar = {
    timestamp: update.timestamp,
    open: update.open ?? existing?.open ?? update.close,
    high: update.high ?? existing?.high ?? update.close,
    low: update.low ?? existing?.low ?? update.close,
    close: update.close,
    volume: update.volume ?? existing?.volume ?? 0,
    data_status: update.data_status ?? 'LIVE',
    source: update.source ?? 'webull_stream',
    session: update.session ?? existing?.session ?? sessionFromTimestamp(update.timestamp),
  };
  if (index >= 0) {
    const next = [...bars];
    next[index] = { ...existing, ...nextBar };
    return next;
  }
  return [...bars, nextBar].sort(
    (a, b) => parseET(b.timestamp).getTime() - parseET(a.timestamp).getTime(),
  );
}

const TREND_TIMEFRAME_GROUPS: Array<{ label: string; timeframes: string[] }> = [
  { label: 'Timing · 1m–5m', timeframes: ['1m', '2m', '3m', '5m'] },
  { label: 'Structure · 15m–1h', timeframes: ['15m', '30m', '1h'] },
  // Weekly is excluded: the trend engine's warm-up exceeds the weekly bar
  // history available, so its score is always null and it contributed a
  // permanently blank third input to this group's bias reading.
  { label: 'Bias · 4h–Daily', timeframes: ['4h', '1d'] },
];

function TrendCardSkeleton() {
  return (
    <div className="card trend-card">
      <div className="trend-header">
        <div style={{ width: '45%', height: '0.9rem', background: 'var(--skeleton-bg, #e2e8f0)', borderRadius: 4 }} />
        <div style={{ width: '1.1rem', height: '1.1rem', background: 'var(--skeleton-bg, #e2e8f0)', borderRadius: 999 }} />
      </div>
      <div style={{ width: '65%', height: '1rem', background: 'var(--skeleton-bg, #e2e8f0)', borderRadius: 4, marginTop: 8 }} />
    </div>
  );
}

// --- Main page ---
export function SymbolPage({ symbol, onSymbolChange, initialTimeframe, initialSession, onPlanTrade }: SymbolPageProps) {
  const [quote, setQuote] = useState<MarketQuote | null>(null);
  const [liveQuote, setLiveQuote] = useState<LiveQuoteUpdateData | null>(null);
  const [quoteConnectionStatus, setQuoteConnectionStatus] = useState<RealtimeConnectionStatus>('closed');
  const marketSession = useMarketSession();
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[]>([]);
  const [loadErrors, setLoadErrors] = useState<Record<string, string>>({});

  const [transitions, setTransitions] = useState<Transition[]>([]);
  const [latestScore, setLatestScore] = useState(0);
  const [latestTimestamp, setLatestTimestamp] = useState<string | null>(null);
  const [transitionsLoading, setTransitionsLoading] = useState(true);

  const [srLevels, setSrLevels] = useState<SRLevel[]>([]);
  const [priceHistory, setPriceHistory] = useState<PriceHistoryItem[]>([]);
  const [latestClose, setLatestClose] = useState<number | null>(null);
  const [latestCloseTimestamp, setLatestCloseTimestamp] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [prevClose, setPrevClose] = useState<number | null>(null);
  const [srLoading, setSrLoading] = useState(true);

  const [divergences, setDivergences] = useState<Divergence[]>([]);
  const [divergencesLoading, setDivergencesLoading] = useState(true);

  const [bars, setBars] = useState<Bar[]>([]);
  const [sessionFilter, setSessionFilter] = useState<SessionFilter>(() => readSessionPreference());
  const [drawings, setDrawings] = useState<Array<{ drawing_type: string; label: string | null; is_visible: boolean }>>([]);
  const [barsLoading, setBarsLoading] = useState(true);
  const [loadingOlderBars, setLoadingOlderBars] = useState(false);
  const [hasOlderBars, setHasOlderBars] = useState(false);
  const lastLiveTradeKeyRef = useRef<string | null>(null);
  // A new streamed 1-minute bucket means the previous candle has closed.
  // Refresh levels then (rather than on every tick) so pivots remain stable
  // while the displayed distance follows the live quote immediately.
  const lastSrBarTimestampRef = useRef<string | null>(null);
  const srRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Stable ref so the subscriber effect doesn't need fetchSR in its dep array.
  // Without this, every timeframe change recreates fetchSR → tears down the WS.
  const fetchSRRef = useRef<() => Promise<void>>(() => Promise.resolve());

  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scanLoading, setScanLoading] = useState(true);
  // Tracks the symbol a fetchScan call was issued for, so a slow response
  // for a symbol the user has since navigated away from can't overwrite
  // the currently-displayed symbol's Score & Confidence data — same
  // stale-response guard as tapeRequestSymbolRef below.
  const scanRequestSymbolRef = useRef<string>(symbol);

  // MTF Confluence (replaces TrendByTimeframeGrid - uses same snapshot as Dashboard)
  const [mtfPreset, setMtfPreset] = useState<string>('day_trading');
  const [mtfConfluence, setMtfConfluence] = useState<any>(null);
  const [mtfLoading, setMtfLoading] = useState(true);
  const [mtfError, setMtfError] = useState<string | null>(null);
  const [mtfBarVersion, setMtfBarVersion] = useState(0);
  const mtfRequestSymbolRef = useRef<string>(symbol);
  const mtfPresetRef = useRef<string>('day_trading');
  mtfPresetRef.current = mtfPreset;

  const [trends, setTrends] = useState<TrendData[]>([]);
  const [trendsLoading, setTrendsLoading] = useState(true);
  const [trendsError, setTrendsError] = useState<string | null>(null);

  const [tape, setTape] = useState<TapeSnapshot | null>(null);
  const [tapeDisabled, setTapeDisabled] = useState(false);
  const [tapeError, setTapeError] = useState<string | null>(null);

  const [regime, setRegime] = useState<RegimeData | null>(null);
  const [regimeLoading, setRegimeLoading] = useState(true);
  const [regimeError, setRegimeError] = useState<string | null>(null);
  const [sectorData, setSectorData] = useState<SectorData | null>(null);

  const [strategy, setStrategy] = useState<StrategyData | null>(null);
  const [strategyLoading, setStrategyLoading] = useState(true);
  const [strategyError, setStrategyError] = useState<string | null>(null);
  // Tracks the symbol a fetchTape call was issued for, so a slow response
  // for a symbol the user has since navigated away from can't overwrite
  // the currently-displayed symbol's tape data.
  const tapeRequestSymbolRef = useRef<string>(symbol);

  const [timeframe, setTimeframe] = useState<string>(initialTimeframe || DEFAULT_TIMEFRAME);
  useEffect(() => {
    if (initialTimeframe) setTimeframe(initialTimeframe);
  }, [initialTimeframe]);
  useEffect(() => {
    if (initialSession) setSessionFilter(initialSession);
  }, [initialSession]);
  useEffect(() => {
    let cancelled = false;
    // Older test doubles / backend deployments may not expose drawings yet;
    // chart state remains valid without that optional enrichment.
    if (typeof api.getDrawingTools !== 'function') return () => { cancelled = true; };
    api.getDrawingTools({ symbol, timeframe })
      .then(rows => { if (!cancelled) setDrawings(rows.map(row => ({ drawing_type: row.drawing_type, label: row.label, is_visible: row.is_visible }))); })
      .catch(() => { if (!cancelled) setDrawings([]); });
    return () => { cancelled = true; };
  }, [symbol, timeframe]);
  const handleChartStateChange = useCallback((state: ChartState) => saveChartState(state), []);
  useEffect(() => {
    let cancelled = false;
    api.getSymbolCalendar(symbol).then(result => { if (!cancelled) setCalendarEvents(result.events); }).catch(() => { if (!cancelled) setCalendarEvents([]); });
    return () => { cancelled = true; };
  }, [symbol]);
  const [chartMode, setChartMode] = useState<'single' | 'multi'>('single');
  const currentSymbolRef = useRef(symbol);
  const analysisContextRef = useRef({ symbol, timeframe });
  currentSymbolRef.current = symbol;
  analysisContextRef.current = { symbol, timeframe };
  // Ticker search lives in both the page header and the chart card
  // header, so it needs two refs to reach the input from the page-level
  // Refresh button.
  const tickerSearchRef = useRef<SymbolInputHandle | null>(null);
  const chartTickerSearchRef = useRef<SymbolInputHandle | null>(null);

  const setLoadError = useCallback((source: string, message: string) => {
    setLoadErrors(previous => ({ ...previous, [source]: message }));
  }, []);

  const clearLoadError = useCallback((source: string) => {
    setLoadErrors(previous => {
      if (!(source in previous)) return previous;
      const { [source]: _removed, ...remaining } = previous;
      return remaining;
    });
  }, []);

  const fetchQuote = useCallback(async () => {
    const requestedSymbol = symbol;
    try {
      const data = await api.getQuote(requestedSymbol);
      if (currentSymbolRef.current !== requestedSymbol) return;
      setQuote(data);
      // Seed the panel with the REST snapshot until the live stream delivers
      // its first event; the next Webull event replaces this with LIVE data.
      setLiveQuote(previous => previous?.event_type !== 'rest_fallback' && previous?.provider === 'webull' ? previous : ({
        price: data.price,
        volume: data.volume,
        bid: data.bid,
        ask: data.ask,
        bid_size: null,
        ask_size: null,
        timestamp: data.timestamp,
        received_at: Date.now() / 1000,
        provider: data.provider || 'rest',
        event_type: 'rest_fallback',
      }));
      clearLoadError('quote');
    } catch (err: any) {
      if (currentSymbolRef.current !== requestedSymbol) return;
      console.error('Failed to load quote:', err);
      setLoadError('quote', 'Latest quote is temporarily unavailable.');
    }
  }, [symbol, clearLoadError, setLoadError]);

  const fetchTransitions = useCallback(async () => {
    const requestedSymbol = symbol;
    const requestedTimeframe = timeframe;
    setTransitionsLoading(true);
    try {
      const data = await api.getTransitions(requestedSymbol, requestedTimeframe);
      const current = analysisContextRef.current;
      if (current.symbol !== requestedSymbol || current.timeframe !== requestedTimeframe) return;
      setTransitions(data?.transitions || []);
      setLatestScore(data?.latest_score ?? 0);
      setLatestTimestamp(data?.latest_timestamp ?? null);
      clearLoadError('transitions');
    } catch (err: any) {
      const current = analysisContextRef.current;
      if (current.symbol !== requestedSymbol || current.timeframe !== requestedTimeframe) return;
      console.error('Failed to load transitions:', err);
      setLoadError('transitions', 'Trend transitions are temporarily unavailable.');
    } finally {
      const current = analysisContextRef.current;
      if (current.symbol === requestedSymbol && current.timeframe === requestedTimeframe) {
        setTransitionsLoading(false);
      }
    }
  }, [symbol, timeframe, clearLoadError, setLoadError]);

  const fetchSR = useCallback(async () => {
    const requestedSymbol = symbol;
    const requestedTimeframe = timeframe;
    setSrLoading(true);
    try {
      const data = await api.getPriceRange(requestedSymbol, requestedTimeframe);
      const current = analysisContextRef.current;
      if (current.symbol !== requestedSymbol || current.timeframe !== requestedTimeframe) return;
      setSrLevels(data?.levels || []);
      setPriceHistory(data?.price_history || []);
      setLatestClose(data?.latest_close ?? null);
      setLatestCloseTimestamp(data?.latest_close_timestamp ?? null);
      setFetchedAt(data?.fetched_at ?? null);
      clearLoadError('price history');
      // Compute prev_close from today's bar close and change (same as Dashboard)
      // prev_close = today_close - today_change (i.e., yesterday's close)
      if (data?.price_history && Array.isArray(data.price_history)) {
        const todayEntry = data.price_history.find((entry: any) =>
          entry.type === 'today_high' || entry.type === 'today_low'
        );
        if (todayEntry && todayEntry.close != null && todayEntry.change != null) {
          setPrevClose(todayEntry.close - todayEntry.change);
        } else {
          setPrevClose(null);
        }
      } else {
        setPrevClose(null);
      }
    } catch (err: any) {
      const current = analysisContextRef.current;
      if (current.symbol !== requestedSymbol || current.timeframe !== requestedTimeframe) return;
      console.error('Failed to load price-range levels:', err);
      setLoadError('price history', 'Price history is temporarily unavailable.');
    } finally {
      const current = analysisContextRef.current;
      if (current.symbol === requestedSymbol && current.timeframe === requestedTimeframe) {
        setSrLoading(false);
      }
    }
  }, [symbol, timeframe, clearLoadError, setLoadError]);
  fetchSRRef.current = fetchSR;

  const fetchDivergences = useCallback(async () => {
    const requestedSymbol = symbol;
    const requestedTimeframe = timeframe;
    setDivergencesLoading(true);
    try {
      const data = await api.getDivergences(requestedSymbol, requestedTimeframe);
      const current = analysisContextRef.current;
      if (current.symbol !== requestedSymbol || current.timeframe !== requestedTimeframe) return;
      setDivergences(data?.divergences || []);
      clearLoadError('divergences');
    } catch (err: any) {
      const current = analysisContextRef.current;
      if (current.symbol !== requestedSymbol || current.timeframe !== requestedTimeframe) return;
      console.error('Failed to load divergences:', err);
      setLoadError('divergences', 'Divergences are temporarily unavailable.');
    } finally {
      const current = analysisContextRef.current;
      if (current.symbol === requestedSymbol && current.timeframe === requestedTimeframe) {
        setDivergencesLoading(false);
      }
    }
  }, [symbol, timeframe, clearLoadError, setLoadError]);

const fetchBars = useCallback(async () => {
    const requestedSymbol = symbol;
    const requestedTimeframe = timeframe;
    setBarsLoading(true);
    try {
      // `bars` feeds both CandlestickChart (a canvas-based lightweight-
      // charts instance — comfortably handles many thousands of points)
      // and BarsTable (a plain, non-virtualized HTML <table> — must stay
      // bounded, see the slice passed to it below). Fetch the full
      // history for the chart; the table takes its own bounded slice
      // rather than limiting this fetch itself, which previously starved
      // the chart down to the table's row cap.
      // Per-timeframe cap: 1m has 10000+ rows and the JSON response alone
      // is ~1.5MB, which took 2-40s on the wire. 1m is capped at 2000
      // (~1.4 trading days) — plenty for a chart and far cheaper to ship.
      const LIMITS: Record<string, number> = { '1m': 2000, '5m': 3000, '15m': 4000, '30m': 4000, '1h': 4000, '4h': 3000, '1d': 2000 };
      const data = await api.getAnalysisBars(requestedSymbol, requestedTimeframe, LIMITS[requestedTimeframe] ?? 5000);
      const current = analysisContextRef.current;
      if (current.symbol !== requestedSymbol || current.timeframe !== requestedTimeframe) return;
      const fetchedBars = data?.bars || [];
      setBars(fetchedBars);
      setHasOlderBars(fetchedBars.length >= (LIMITS[requestedTimeframe] ?? 5000));
      clearLoadError('bars');
    } catch (err: any) {
      const current = analysisContextRef.current;
      if (current.symbol !== requestedSymbol || current.timeframe !== requestedTimeframe) return;
      console.error('Failed to load bars:', err);
      setLoadError('bars', 'Chart bars are temporarily unavailable.');
    } finally {
      const current = analysisContextRef.current;
      if (current.symbol === requestedSymbol && current.timeframe === requestedTimeframe) {
        setBarsLoading(false);
      }
    }
  }, [symbol, timeframe, clearLoadError, setLoadError]);

  const loadOlderBars = useCallback(async () => {
    const requestedSymbol = symbol;
    const requestedTimeframe = timeframe;
    const oldest = bars.reduce<string | null>((current, bar) => {
      if (!bar.timestamp) return current;
      if (!current) return bar.timestamp;
      return parseET(bar.timestamp).getTime() < parseET(current).getTime() ? bar.timestamp : current;
    }, null);
    if (!oldest) return;
    setLoadingOlderBars(true);
    try {
      const LIMITS: Record<string, number> = { '1m': 2000, '5m': 3000, '15m': 4000, '30m': 4000, '1h': 4000, '4h': 3000, '1d': 2000 };
      const before = new Date(parseET(oldest).getTime() - 1).toISOString();
      const data = await api.getAnalysisBars(requestedSymbol, requestedTimeframe, LIMITS[requestedTimeframe] ?? 5000, before);
      const current = analysisContextRef.current;
      if (current.symbol !== requestedSymbol || current.timeframe !== requestedTimeframe) return;
      const older = data?.bars || [];
      setBars(previous => {
        const merged = [...previous, ...older];
        const seen = new Set<string>();
        return merged.filter(bar => {
          const key = bar.timestamp;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        }).sort((a, b) => parseET(b.timestamp).getTime() - parseET(a.timestamp).getTime());
      });
      setHasOlderBars(older.length >= (LIMITS[requestedTimeframe] ?? 5000));
    } catch (err) {
      console.error('Failed to load older bars:', err);
    } finally {
      setLoadingOlderBars(false);
    }
  }, [bars, symbol, timeframe]);

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
      clearLoadError('tape');
    } catch (err: any) {
      if (tapeRequestSymbolRef.current !== requestedSymbol) return;
      // 503 = tape streaming off; anything else is a real error.
      if (String(err?.message || '').includes('503')) {
        setTapeDisabled(true);
        setTapeError(null);
        clearLoadError('tape');
      } else {
        console.error('Failed to load tape:', err);
        setTapeError(err?.message || 'Failed to load tape data');
        setLoadError('tape', 'Time and sales data is temporarily unavailable.');
      }
      setTape(null);
    }
  }, [symbol, clearLoadError, setLoadError]);

  const fetchScan = useCallback(async () => {
    const requestedSymbol = symbol;
    scanRequestSymbolRef.current = requestedSymbol;
    setScanLoading(true);
    try {
      const data = await api.getScanResult(requestedSymbol, true);
      if (scanRequestSymbolRef.current !== requestedSymbol) return;
      setScanResult(data);
      clearLoadError('scanner');
    } catch (err: any) {
      if (scanRequestSymbolRef.current !== requestedSymbol) return;
      console.error('Failed to load scan:', err);
      setLoadError('scanner', 'Signal analysis is temporarily unavailable.');
    } finally {
      if (scanRequestSymbolRef.current === requestedSymbol) setScanLoading(false);
    }
  }, [symbol, clearLoadError, setLoadError]);

  const fetchMTF = useCallback(async () => {
    const requestedSymbol = symbol;
    const requestedPreset = mtfPreset;
    mtfRequestSymbolRef.current = requestedSymbol;
    mtfPresetRef.current = requestedPreset;
    setMtfLoading(true);
    setMtfError(null);
    try {
      const response = await api.getMTFSnapshot(requestedSymbol, requestedPreset);
      if (mtfRequestSymbolRef.current !== requestedSymbol || mtfPresetRef.current !== requestedPreset) return;
      const snap = response.snapshot;
      if (snap) {
        // Transform snapshot to ConfluenceData format
        const data = {
          symbol: snap.symbol,
          direction: snap.direction,
          strength: snap.strength,
          alignment_score: snap.alignment_score,
          timeframe_signals: Object.fromEntries(
            Object.entries(snap.timeframe_snapshots).map(([tf, tfSnap]) => [
              tf,
              {
                direction: tfSnap.direction,
                score: tfSnap.score,
                strength: tfSnap.strength,
                confidence: tfSnap.confidence,
                timestamp: tfSnap.timestamp,
                data_quality: tfSnap.data_quality,
                data_age_seconds: tfSnap.data_age_seconds,
                bar_closed: tfSnap.bar_closed,
                is_warmed_up: tfSnap.is_warmed_up,
                valid: tfSnap.valid,
                quality_weight: tfSnap.quality_weight,
              },
            ])
          ),
          timestamp: snap.timestamp,
          bullish_alignment: snap.bullish_alignment,
          bearish_alignment: snap.bearish_alignment,
          conflicting: snap.conflicting,
          short_term_direction: snap.short_term_direction,
          intermediate_direction: snap.intermediate_direction,
          higher_direction: snap.higher_direction,
          preset: snap.preset,
          short_term_state: snap.short_term_state,
          intermediate_state: snap.intermediate_state,
          higher_state: snap.higher_state,
          valid_coverage: snap.valid_coverage,
          quality_weighted_score: snap.quality_weighted_score,
        };
        setMtfConfluence(data);
      } else {
        setMtfConfluence(null);
      }
      clearLoadError('multi-timeframe analysis');
    } catch (err: any) {
      if (mtfRequestSymbolRef.current !== requestedSymbol || mtfPresetRef.current !== requestedPreset) return;
      setMtfError(err?.message || 'Failed to load MTF confluence');
      setLoadError('multi-timeframe analysis', 'Multi-timeframe analysis is temporarily unavailable.');
    } finally {
      if (mtfRequestSymbolRef.current === requestedSymbol && mtfPresetRef.current === requestedPreset) setMtfLoading(false);
    }
  }, [symbol, mtfPreset, clearLoadError, setLoadError]);

  const fetchTrends = useCallback(async () => {
    const requestedSymbol = symbol;
    setTrendsLoading(true);
    setTrendsError(null);
    try {
      const data = await api.getTrends(requestedSymbol, ['1m', '2m', '3m', '5m', '15m', '30m', '1h', '4h', '1d']);
      if (requestedSymbol !== symbol) return;
      setTrends(data);
    } catch (err: any) {
      if (requestedSymbol !== symbol) return;
      setTrendsError(err?.message || 'Failed to load trends');
    } finally {
      if (requestedSymbol === symbol) setTrendsLoading(false);
    }
  }, [symbol]);

  const fetchRegime = useCallback(async () => {
    const requestedSymbol = symbol;
    setRegimeLoading(true);
    setRegimeError(null);
    try {
      const data = await api.getRegime(requestedSymbol);
      if (currentSymbolRef.current !== requestedSymbol) return;
      setRegime(data);
    } catch (err: any) {
      if (currentSymbolRef.current !== requestedSymbol) return;
      setRegimeError(err?.message || 'Failed to load regime');
    } finally {
      if (currentSymbolRef.current === requestedSymbol) setRegimeLoading(false);
    }
  }, [symbol]);

  const fetchSector = useCallback(async () => {
    const requestedSymbol = symbol;
    try {
      const data = await api.getSector(requestedSymbol);
      if (currentSymbolRef.current !== requestedSymbol) return;
      setSectorData(data);
    } catch {
      if (currentSymbolRef.current !== requestedSymbol) return;
      setSectorData(null);
    }
  }, [symbol]);

  const fetchStrategy = useCallback(async () => {
    const requestedSymbol = symbol;
    setStrategyLoading(true);
    setStrategyError(null);
    try {
      const data = await api.getStrategy(requestedSymbol);
      if (currentSymbolRef.current !== requestedSymbol) return;
      setStrategy(data);
    } catch (err: any) {
      if (currentSymbolRef.current !== requestedSymbol) return;
      setStrategyError(err?.message || 'Failed to load strategy');
    } finally {
      if (currentSymbolRef.current === requestedSymbol) setStrategyLoading(false);
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
    fetchMTF();
    fetchTrends();
    fetchRegime();
    fetchSector();
    fetchStrategy();
  }, [fetchQuote, fetchTransitions, fetchSR, fetchDivergences, fetchBars, fetchScan, fetchTape, fetchMTF, fetchTrends, fetchRegime, fetchSector, fetchStrategy]);

  const retryLoad = useCallback((source: string) => {
    const retries: Record<string, () => void> = {
      quote: fetchQuote,
      transitions: fetchTransitions,
      'price history': fetchSR,
      divergences: fetchDivergences,
      bars: fetchBars,
      scanner: fetchScan,
      tape: fetchTape,
      'multi-timeframe analysis': fetchMTF,
      trends: fetchTrends,
      regime: fetchRegime,
      strategy: fetchStrategy,
    };
    retries[source]?.();
  }, [fetchQuote, fetchTransitions, fetchSR, fetchDivergences, fetchBars, fetchScan, fetchTape, fetchMTF, fetchTrends, fetchRegime, fetchStrategy]);

  // Do not render the previous selection's values under a newly-selected
  // symbol/timeframe while the replacement requests are in flight.
  useEffect(() => {
    setQuote(null);
    setLiveQuote(null);
    setScanResult(null);
    setTape(null);
    setTapeDisabled(false);
    setTapeError(null);
    setMtfConfluence(null);
    setMtfError(null);
    setTrends([]);
    setTrendsError(null);
    setRegime(null);
    setRegimeError(null);
    setStrategy(null);
    setStrategyError(null);
    setSectorData(null);
    setLoadErrors({});
  }, [symbol]);

  useEffect(() => {
    setTransitions([]);
    setLatestScore(0);
    setLatestTimestamp(null);
    setSrLevels([]);
    setPriceHistory([]);
    setLatestClose(null);
    setLatestCloseTimestamp(null);
    setFetchedAt(null);
    setPrevClose(null);
    setDivergences([]);
    setBars([]);
    lastLiveTradeKeyRef.current = null;
    lastSrBarTimestampRef.current = null;
  }, [symbol, timeframe]);

  useEffect(() => {
    fetchQuote();
    // REST remains a slow fallback; live Webull snapshots/trades arrive over
    // the shared realtime channel whenever streaming is available.
    const id = setInterval(fetchQuote, 30000);
    const subscriber = api.createRealtimeSubscriber?.();
    if (!subscriber) {
      return () => clearInterval(id);
    }
    const unsubscribe = subscriber.onEvent((event: RealtimeEvent) => {
      if (event.type === 'bar_update') {
        const current = analysisContextRef.current;
        if (
          event.symbol === symbol.toUpperCase()
          && event.timeframe === '1m'
          && event.data.timestamp
          && event.data.timestamp !== lastSrBarTimestampRef.current
        ) {
          lastSrBarTimestampRef.current = event.data.timestamp;
          setMtfBarVersion(version => version + 1);
          if (srRefreshTimerRef.current !== null) clearTimeout(srRefreshTimerRef.current);
          // Give the backend live-bar persistence a moment to commit the
          // completed candle before re-reading the price-range analysis.
          srRefreshTimerRef.current = setTimeout(() => {
            srRefreshTimerRef.current = null;
            void fetchSRRef.current();
          }, 750);
        }
        if (
          event.symbol === symbol.toUpperCase()
          && event.timeframe === '1m'
          && current.timeframe === '1m'
        ) {
          setBars(previous => mergeLiveBarIntoMinuteBars(previous, event.data));
        }
        return;
      }
      if (event.type !== 'quote_update' || event.symbol !== symbol.toUpperCase()) return;
      const live = event.data;
      setLiveQuote(live);
      // Build the active 1-minute candle locally from trade prints. BBO and
      // snapshot events update the quote panel, but must not be counted as
      // bar volume because their volume semantics differ.
      if (analysisContextRef.current.timeframe === '1m' && live.event_type === 'trade') {
        const tradeKey = `${event.symbol}:${live.timestamp}:${live.price}:${live.volume}`;
        if (lastLiveTradeKeyRef.current !== tradeKey) {
          lastLiveTradeKeyRef.current = tradeKey;
          setBars(previous => mergeLiveTradeIntoMinuteBars(previous, live));
        }
      }
      setQuote(previous => ({
        ...(previous || { symbol: symbol.toUpperCase() }),
        symbol: symbol.toUpperCase(),
        price: live.price,
        bid: live.bid ?? previous?.bid ?? null,
        ask: live.ask ?? previous?.ask ?? null,
        volume: live.volume ?? previous?.volume ?? null,
        timestamp: live.timestamp,
        provider: live.provider,
        data_status: 'LIVE',
      }));
    });
    const unsubscribeStatus = subscriber.onStatus(setQuoteConnectionStatus);
    subscriber.subscribeQuote(symbol);
    // The backend aggregates the same shared Webull trades into a 1-minute
    // candle and pushes it to every chart subscriber. Keep the client-side
    // trade merge above as a graceful fallback when the bar channel is stale.
    subscriber.subscribe(symbol, '1m');
    return () => {
      clearInterval(id);
      if (srRefreshTimerRef.current !== null) {
        clearTimeout(srRefreshTimerRef.current);
        srRefreshTimerRef.current = null;
      }
      unsubscribe();
      unsubscribeStatus();
      subscriber.unsubscribeQuote(symbol);
      subscriber.unsubscribe(symbol, '1m');
      subscriber.disconnect();
    };
  }, [fetchQuote, symbol]);

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
    fetchMTF();
  }, [fetchMTF]);

  useEffect(() => {
    fetchTrends();
  }, [fetchTrends]);

  useEffect(() => {
    fetchRegime();
  }, [fetchRegime]);

  useEffect(() => {
    fetchSector();
  }, [fetchSector]);

  useEffect(() => {
    fetchStrategy();
  }, [fetchStrategy]);

  // Refresh confluence after the shared realtime pipeline closes a new
  // 1-minute bucket. The snapshot is computed from in-memory trend state, so
  // this does not create a provider request for every tick.
  useEffect(() => {
    if (mtfBarVersion === 0) return;
    void fetchMTF();
  }, [mtfBarVersion, fetchMTF]);

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

  // Period-over-period change for the S/R panel: compare the latest bar's
  // close to the prior bar's close (bars arrive newest→oldest). The backend
  // Quote model has no change field, so we derive it here — the same delta
  // the BarsTable shows per row.
  const barsChange =
    bars.length >= 2 && typeof bars[0].close === 'number' && typeof bars[1].close === 'number' && bars[1].close !== 0
      ? bars[0].close - bars[1].close
      : null;
  const barsChangePct = barsChange != null ? (barsChange / bars[1].close) * 100 : null;

  // Live change from quote price vs prev_close (matches Dashboard)
  const liveChange = quote?.price != null && prevClose != null
    ? quote.price - prevClose
    : null;
  const liveChangePct = liveChange != null && prevClose != null
    ? (liveChange / prevClose) * 100
    : null;
  const srDisplayClose = liveQuote?.price ?? quote?.price ?? latestClose;
  const displayQuote = liveQuote || (quote ? {
    price: quote.price,
    volume: quote.volume,
    bid: quote.bid,
    ask: quote.ask,
    bid_size: null,
    ask_size: null,
    timestamp: quote.timestamp,
    received_at: Date.now() / 1000,
    provider: quote.provider || 'rest',
    event_type: 'rest_fallback',
  } : null);
  const sessionStats = useMemo(() => {
    const visible = bars.filter((bar) => sessionMatchesPreference(bar, sessionFilter));
    if (!visible.length) return null;
    return {
      bars: visible.length,
      high: Math.max(...visible.map((bar) => bar.high)),
      low: Math.min(...visible.map((bar) => bar.low)),
      volume: visible.reduce((sum, bar) => sum + (bar.volume || 0), 0),
    };
  }, [bars, sessionFilter]);

  return (
    <div className="symbol-page">
      <div className="dashboard-header">
        <div>
          <h1>{symbol} Analysis <EarningsBadge events={calendarEvents} /></h1>
          <p className="subtitle">
            {quote?.price != null ? (
              <>
                <span className="price-label">Latest Price</span>{' '}
                <span>${quote.price.toFixed(4)}</span>
                {liveChange != null && (
                  <span style={{ color: liveChange >= 0 ? '#10b981' : '#ef4444', marginLeft: 8 }}>
                    {liveChange >= 0 ? '+' : ''}{liveChange.toFixed(4)}
                  </span>
                )}
                {liveChangePct != null && (
                  <span style={{ color: liveChangePct >= 0 ? '#10b981' : '#ef4444', marginLeft: 4 }}>
                    ({liveChangePct >= 0 ? '+' : ''}{liveChangePct.toFixed(2)}%)
                  </span>
                )}
                <MarketDataUpdateStatus
                  dataStatus={quote.data_status}
                  timestamp={quote.timestamp}
                  connectionStatus={quoteConnectionStatus}
                  provider={quote.provider}
                  marketSession={marketSession?.session}
                />
              </>
            ) : '—'}
            {quote?.price == null && (
              <MarketDataUpdateStatus
                dataStatus={quote?.data_status}
                timestamp={quote?.timestamp}
                connectionStatus={quoteConnectionStatus}
                provider={quote?.provider}
                marketSession={marketSession?.session}
              />
            )}
          </p>
          {displayQuote && (
            <div className="symbol-bbo-panel" style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', alignItems: 'center', marginTop: 8, color: 'var(--text-muted)', fontSize: 12 }}>
              <span style={{ color: displayQuote.event_type === 'rest_fallback' ? '#f59e0b' : '#10b981', fontWeight: 600 }}>● {displayQuote.event_type === 'rest_fallback' ? 'REST FALLBACK' : (displayQuote.bid != null && displayQuote.ask != null ? 'LIVE BBO' : 'LIVE QUOTE')}</span>
              <span>Bid <strong>{displayQuote.bid != null ? `$${displayQuote.bid.toFixed(4)}` : '—'}</strong>{displayQuote.bid_size != null ? ` × ${displayQuote.bid_size}` : ''}</span>
              <span>Ask <strong>{displayQuote.ask != null ? `$${displayQuote.ask.toFixed(4)}` : '—'}</strong>{displayQuote.ask_size != null ? ` × ${displayQuote.ask_size}` : ''}</span>
              <span>Mid <strong>{displayQuote.bid != null && displayQuote.ask != null ? `$${((displayQuote.bid + displayQuote.ask) / 2).toFixed(4)}` : '—'}</strong></span>
              <span>Spread <strong>{displayQuote.bid != null && displayQuote.ask != null ? `$${(displayQuote.ask - displayQuote.bid).toFixed(4)}` : '—'}</strong></span>
              <span title={displayQuote.timestamp || undefined}>{displayQuote.provider} · {displayQuote.event_type}</span>
            </div>
          )}
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
          <SymbolInput
            ref={tickerSearchRef}
            symbol={symbol}
            onChange={onSymbolChange}
            onSubmit={handleRefresh}
          />
          <button className="btn" onClick={handleRefresh}>↻ Refresh</button>
          {onPlanTrade && (
            <button
              className="btn"
              onClick={() => onPlanTrade(symbol, timeframe)}
              title="Open Trade Planning with this symbol and timeframe"
            >
              🎯 Plan this trade
            </button>
          )}
        </div>
      </div>
      {Object.entries(loadErrors).map(([source, message]) => (
        <ErrorBanner
          key={source}
          message={message}
          onDismiss={() => clearLoadError(source)}
          onRetry={() => retryLoad(source)}
        />
      ))}

      <div className="symbol-grid">

        {/* ── 1. Spatial awareness: where is price relative to structure ── */}
        <div className={srLoading && srLevels.length === 0 ? 'card-loading-skeleton' : ''}>
          <SRPanel
            levels={srLevels}
            latestClose={srDisplayClose}
          />
        </div>
        <PriceHistoryPanel
          history={priceHistory}
          latestClose={latestClose}
          latestCloseTimestamp={latestCloseTimestamp}
          fetchedAt={fetchedAt}
          change={barsChange}
          changePercent={barsChangePct}
        />

        {/* ── 2. Primary visual: chart with session controls directly above ── */}
        <div className="session-filter-bar" aria-label="Market session filter">
          <label htmlFor="symbol-session-filter"><strong>Market session:</strong></label>
          <select id="symbol-session-filter" value={sessionFilter} onChange={event => { const value = event.target.value as SessionFilter; setSessionFilter(value); window.localStorage.setItem(SESSION_PREFERENCE_KEY, value); }}>
            {(Object.keys(SESSION_LABELS) as SessionFilter[]).map(value => <option key={value} value={value}>{SESSION_LABELS[value]}</option>)}
          </select>
          {hasOlderBars && (
            <button className="btn btn-small" onClick={loadOlderBars} disabled={loadingOlderBars}>
              {loadingOlderBars ? 'Loading older bars…' : 'Load older bars'}
            </button>
          )}
          <span className="session-legend">Times shown in US/Eastern</span>
        </div>
        {sessionStats && (
          <div className="session-stats" aria-label="Selected session statistics">
            <span><small>Bars</small><strong>{sessionStats.bars.toLocaleString()}</strong></span>
            <span><small>High</small><strong>{sessionStats.high.toFixed(2)}</strong></span>
            <span><small>Low</small><strong>{sessionStats.low.toFixed(2)}</strong></span>
            <span><small>Volume</small><strong>{sessionStats.volume.toLocaleString()}</strong></span>
          </div>
        )}
        {chartMode === 'single' ? (
          <CandlestickChart
            bars={bars.filter(bar => sessionMatchesPreference(bar, sessionFilter))}
            symbol={symbol}
            transitions={transitions}
            marketSession={marketSession?.session}
            initialActiveOverlays={['supertrend']}
            timeframe={timeframe}
            sessionFilter={sessionFilter}
            drawings={drawings}
            onStateChange={handleChartStateChange}
            onTimeframeChange={setTimeframe}
            timeframeOptions={TIMEFRAMES.map(tf => ({ value: tf, label: TIMEFRAME_LABELS[tf] || tf }))}
            chartMode={chartMode}
            onChartModeChange={setChartMode}
            tickerSearch={(
              <SymbolInput
                ref={chartTickerSearchRef}
                symbol={symbol}
                onChange={onSymbolChange}
                onSubmit={handleRefresh}
              />
            )}
          />
        ) : (
          <MultiTimeframeChartGrid
            symbol={symbol}
            timeframes={DEFAULT_GRID_TIMEFRAMES}
            chartMode={chartMode}
            onChartModeChange={setChartMode}
            sessionFilter={sessionFilter}
            tickerSearch={(
              <SymbolInput
                ref={chartTickerSearchRef}
                symbol={symbol}
                onChange={onSymbolChange}
                onSubmit={handleRefresh}
              />
            )}
          />
        )}

        {/* ── 3. Directional bias: regime → MTF confluence → trend per TF ── */}
        <div className={regimeLoading && !regime ? 'card-loading-skeleton' : ''}>
          <RegimeCard regime={regime} sectorData={sectorData} error={regimeError} onRetry={fetchRegime} />
        </div>
        <div className={mtfLoading && !mtfConfluence ? 'card-loading-skeleton' : ''}>
          <ConfluenceCard
            confluence={mtfConfluence}
            error={mtfError}
            selectedPreset={mtfPreset}
            onPresetChange={setMtfPreset}
          />
        </div>
        <div className="trends-section">
          <div className="trends-section-header">
            <h2>Trend by Timeframe</h2>
          </div>
          {trendsLoading && trends.length === 0 ? (
            <div className="trend-grid">
              {Array.from({ length: 5 }).map((_, i) => <TrendCardSkeleton key={i} />)}
            </div>
          ) : (
            <div className="trend-timeframe-groups">
              {TREND_TIMEFRAME_GROUPS.map(group => {
                const groupTrends = trends.filter(t => group.timeframes.includes(t.timeframe));
                if (groupTrends.length === 0) return null;
                return (
                  <section className="trend-timeframe-group" key={group.label} aria-label={group.label}>
                    <h3>{group.label}</h3>
                    <div className="trend-grid">
                      {groupTrends.map(trend => (
                        <TrendCard
                          key={trend.timeframe}
                          trend={trend}
                          confluenceRole={mtfConfluence?.timeframe_signals && trend.timeframe in mtfConfluence.timeframe_signals ? 'input' : 'out_of_scope'}
                          onOpenChart={() => setTimeframe(trend.timeframe)}
                        />
                      ))}
                    </div>
                  </section>
                );
              })}
            </div>
          )}
          {trends.length === 0 && !trendsLoading && !trendsError && (
            <p className="empty-state">No trend data available</p>
          )}
          {trendsError && (
            <div className="empty-state">
              <p>Failed to load trends: {trendsError}</p>
              <button className="btn btn-small" onClick={fetchTrends}>Retry</button>
            </div>
          )}
        </div>

        {/* ── 4. Order flow: tape pressure + momentum transitions ── */}
        <TapePressureCard tape={tape} disabled={tapeDisabled} error={tapeError} />
        <div className={transitionsLoading && transitions.length === 0 ? 'card-loading-skeleton' : ''}>
          <TransitionsPanel
            transitions={transitions}
            latestScore={latestScore}
            latestTimestamp={latestTimestamp}
            symbol={symbol}
            timeframe={timeframe}
          />
        </div>

        {/* ── 5. Signal & strategy: what the scan says and what to do ── */}
        <div className={`symbol-grid-pair-panel${scanLoading && !scanResult ? ' card-loading-skeleton' : ''}`}>
          <SignalExplanationPanel
            symbol={symbol}
            explanation={scanResult?.explanation}
            liveQuote={liveQuote}
            tape={tape}
          />
        </div>
        <div className={scanLoading && !scanResult ? 'card-loading-skeleton' : ''}>
          <ScoreDetailPanel
            totalScore={scanResult?.total_score ?? 0}
            scores={scanResult?.scores ?? {}}
            signals={scanResult?.signals}
            confidence={scanResult?.explanation?.confidence}
            symbol={symbol}
          />
        </div>
        <div className={strategyLoading && !strategy ? 'card-loading-skeleton' : ''}>
          <StrategyCard strategy={strategy} error={strategyError} onRetry={fetchStrategy} />
        </div>

        {/* ── 6. Context: catalysts + divergences ── */}
        <div className="symbol-grid-pair-panel">
          <Suspense fallback={<div className="panel-skeleton">Loading catalyst timeline…</div>}>
            <CatalystTimelinePanel symbol={symbol} scanResult={scanResult} />
          </Suspense>
        </div>
        <div className={divergencesLoading && divergences.length === 0 ? 'card-loading-skeleton' : ''}>
          <DivergencesPanel divergences={divergences} />
        </div>

        {/* ── 7. Deep research: AI, indicators, options ── */}
        <Suspense fallback={<div className="panel-skeleton">Loading AI analysis…</div>}>
          <AIAnalysisPanel symbol={symbol} timeframe={timeframe} />
        </Suspense>
        <Suspense fallback={<div className="panel-skeleton">Loading indicators…</div>}>
          <CustomIndicatorsPanel symbol={symbol} timeframe={timeframe} />
        </Suspense>
        <Suspense fallback={<div className="panel-skeleton">Loading options snapshot…</div>}>
          <OptionsPanel symbol={symbol} underlyingPrice={quote?.price ?? scanResult?.quote?.price} />
        </Suspense>

        {/* ── 8. Raw data reference ── */}
        <div className={barsLoading && bars.length === 0 ? 'card-loading-skeleton' : ''}>
          <BarsTable
            bars={bars.filter(bar => sessionMatchesPreference(bar, sessionFilter)).slice(0, 1000)}
            sessionFilter={sessionFilter}
          />
        </div>
      </div>
    </div>
  );
}
