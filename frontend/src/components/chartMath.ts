/**
 * Pure-function helpers for the price chart.
 *
 * Extracted from CandlestickChart.tsx so the component file holds only the
 * React layer (effect-driven chart lifecycle + UI). All functions here are
 * deterministic and free of side effects, so they're trivially testable
 * in isolation and can be reused by other chart components (e.g. the
 * multi-timeframe grid).
 *
 * Conventions:
 * - Time values are Unix seconds (lightweight-charts requirement).
 * - All functions assume unsorted, possibly-duplicated input and produce
 *   sorted, deduplicated output.
 * - Memoization lives at module scope — keyed by the input array identity,
 *   not the contents, so callers must keep stable references for cache hits.
 */
import type { Bar } from '../services/api';

// --- Public types ---------------------------------------------------------

export type ChartType = 'candlestick' | 'bar' | 'line' | 'area' | 'heikin-ashi';

export type OverlayKey = 'ema9' | 'ema21' | 'sma50' | 'sma200' | 'supertrend';

export interface ChartPoint {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface OverlayPoint {
  time: number;
  value: number;
  color?: string;
}

export interface OverlayDef {
  key: OverlayKey;
  label: string;
  color: string;
  lineWidth?: number;
}

// --- Constants ------------------------------------------------------------

/** Color palette shared with the rest of the app's chart surfaces. */
export const UP_COLOR = '#10b981';
export const DOWN_COLOR = '#ef4444';
export const GRID_COLOR = '#1f2937';
export const TEXT_COLOR = '#9ca3af';
export const LINE_COLOR = '#60a5fa';

export const CHART_TYPES: { key: ChartType; label: string; shortLabel: string }[] = [
  { key: 'candlestick', label: 'Candlestick', shortLabel: 'Candle' },
  { key: 'bar', label: 'OHLC Bars', shortLabel: 'Bars' },
  { key: 'line', label: 'Line', shortLabel: 'Line' },
  { key: 'area', label: 'Area', shortLabel: 'Area' },
  { key: 'heikin-ashi', label: 'Heikin-Ashi', shortLabel: 'HA' },
];

export const OVERLAYS: OverlayDef[] = [
  { key: 'ema9', label: 'EMA 9', color: '#3b82f6', lineWidth: 1 },
  { key: 'ema21', label: 'EMA 21', color: '#8b5cf6', lineWidth: 1 },
  { key: 'sma50', label: 'SMA 50', color: '#f59e0b', lineWidth: 1 },
  { key: 'sma200', label: 'SMA 200', color: '#f97316', lineWidth: 1 },
  { key: 'supertrend', label: 'SuperTrend', color: '#ec4899', lineWidth: 2 },
];

// --- Time / dedup helpers -------------------------------------------------

/**
 * Normalise a backend ET-offset ISO timestamp to a UTC Date.
 *
 * Backend returns strings like `2026-09-04T15:59:00-04:00` — an explicit
 * ±HH:MM offset already baked in. `new Date(str)` parses that offset
 * correctly on its own (JS Date handles embedded offsets natively), giving
 * the right absolute instant regardless of browser locale.
 */
export function parseET(ts: string | null | undefined): Date {
  if (!ts) return new Date(NaN);
  return new Date(ts);
}

/** Convert a bar's ISO timestamp to Unix seconds for lightweight-charts. */
export function toTime(b: Bar): number {
  return Math.floor(parseET(b.timestamp).getTime() / 1000);
}

/** IANA zone every chart display formatter below renders into. */
const ET_TIME_ZONE = 'America/New_York';

/**
 * lightweight-charts' `Time` values are Unix seconds — an absolute instant,
 * timezone-agnostic. But the library's DEFAULT axis/tooltip formatting
 * renders that instant using the *viewer's browser/OS* timezone, not ET —
 * so anyone not physically set to America/New_York sees UTC (or whatever
 * their system is set to) on the chart instead of market time. These
 * formatters are passed to `timeScale.tickMarkFormatter` / `localization.
 * timeFormatter` in chart options to force ET display for every viewer.
 *
 * tickMarkType mirrors lightweight-charts' TickMarkType enum (Year=0,
 * Month=1, DayOfMonth=2, Time=3, TimeWithSeconds=4) — passed as a plain
 * number rather than importing the enum, since this module is statically
 * imported while the chart library itself is loaded dynamically (see
 * CandlestickChart.tsx) to avoid pulling it into the main bundle.
 */
export function etTickMarkFormatter(time: number, tickMarkType: number): string {
  const d = new Date(time * 1000);
  if (tickMarkType <= 2) {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: ET_TIME_ZONE,
      year: tickMarkType === 0 ? 'numeric' : undefined,
      month: 'short',
      day: tickMarkType === 2 ? 'numeric' : undefined,
    }).format(d);
  }
  return new Intl.DateTimeFormat('en-US', {
    timeZone: ET_TIME_ZONE,
    hour: 'numeric',
    minute: '2-digit',
    second: tickMarkType === 4 ? '2-digit' : undefined,
    hour12: false,
  }).format(d);
}

/** Full date+time ET formatter for the crosshair/tooltip. */
export function etTimeFormatter(time: number): string {
  const d = new Date(time * 1000);
  const formatted = new Intl.DateTimeFormat('en-US', {
    timeZone: ET_TIME_ZONE,
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: false,
  }).format(d);
  return `${formatted} ET`;
}

/**
 * String-timestamp counterparts of the formatters above, for the many
 * "Updated: {time}" / "Scanned {time}" style displays outside the chart
 * itself (cards, tables). These previously called `parseET(ts).
 * toLocaleString()` (or `toLocaleDateString`/`toLocaleTimeString`) with no
 * `timeZone` option — `parseET` correctly resolves the absolute instant,
 * but `.toLocaleString()` etc. without an explicit zone format it using
 * the *viewer's browser/OS* timezone, showing UTC (or whatever the
 * viewer's machine is set to) instead of ET for anyone not physically in
 * America/New_York. Use these instead of bare `.toLocaleString()` calls
 * on a `parseET()` result.
 */
export function formatETDateTime(ts: string | null | undefined): string {
  const d = parseET(ts);
  if (isNaN(d.getTime())) return '—';
  const formatted = new Intl.DateTimeFormat('en-US', {
    timeZone: ET_TIME_ZONE,
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(d);
  return `${formatted} ET`;
}

/** ET date only (no time) — for transition/divergence/backtest date columns. */
export function formatETDate(ts: string | null | undefined): string {
  const d = parseET(ts);
  if (isNaN(d.getTime())) return '—';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: ET_TIME_ZONE,
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(d);
}

/** ET time only (no date) — for short "Scanned HH:MM" style displays. */
export function formatETTime(ts: string | null | undefined): string {
  const d = parseET(ts);
  if (isNaN(d.getTime())) return '—';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: ET_TIME_ZONE,
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(d);
}

/**
 * Sort bars ascending by timestamp. Memoized by input array identity.
 *
 * Sorting used to call parseET() inside the comparator, which is O(n log n)
 * Date parses per sort — and sortedBars() is called from every overlay
 * compute (chart data, HA, EMA, SuperTrend) on every bars change. Precompute
 * the Unix-second times once per bar array and sort by that instead.
 */
const sortedBarsCache = new WeakMap<Bar[], { sorted: Bar[]; times: number[] }>();
function sortedBarsImpl(bars: Bar[]): { sorted: Bar[]; times: number[] } {
  let cached = sortedBarsCache.get(bars);
  if (!cached) {
    // times are computed from the ORIGINAL order, so sort the index
    // array by them and then map both `sorted` and `times` through the
    // same permutation — otherwise sorted[i] and times[i] would be
    // paired across different bars and every overlay consumer (chart
    // data, HA, EMA, SuperTrend, SMA) would emit out-of-order
    // timestamps, which lightweight-charts rejects as "data must be
    // asc ordered by time".
    const times = bars.map(b => Math.floor(parseET(b.timestamp).getTime() / 1000));
    const idx = bars.map((_, i) => i);
    idx.sort((a, b) => times[a] - times[b]);
    const sorted = idx.map(i => bars[i]);
    const sortedTimes = idx.map(i => times[i]);
    cached = { sorted, times: sortedTimes };
    sortedBarsCache.set(bars, cached);
  }
  return cached;
}
/**
 * Sort bars ascending by timestamp. Memoized by input array identity.
 */
export function sortedBars(bars: Bar[]): Bar[] {
  return sortedBarsImpl(bars).sorted;
}
/**
 * Sort bars ascending by timestamp and return them alongside their
 * precomputed Unix-second times. The times are shared by every overlay
 * compute on the same bar array, so each bar's timestamp is parsed once
 * instead of once per overlay (chart data, HA, EMA, SuperTrend all used
 * to re-parse it).
 */
export function sortedBarsWithTimes(bars: Bar[]): { sorted: Bar[]; times: number[] } {
  return sortedBarsImpl(bars);
}

/**
 * Final dedup guard: lightweight-charts asserts ascending, unique timestamps.
 * Even if `sortedBars` already removed dupes at the ms level, two bars can
 * land on the same Unix second if their timestamps cross a second boundary.
 */
export function dedupByTime<T extends { time: number }>(data: T[]): T[] {
  const out: T[] = [];
  let last = -1;
  for (const p of data) {
    if (p.time === last) continue;
    last = p.time;
    out.push(p);
  }
  return out;
}

// --- Bar -> ChartPoint transforms ---------------------------------------

/** Standard OHLC transform. Returns ascending, deduped ChartPoints. */
export function toChartData(bars: Bar[]): ChartPoint[] {
  const { sorted, times } = sortedBarsWithTimes(bars);
  const out: ChartPoint[] = [];
  let lastTime: number | null = null;
  for (let i = 0; i < sorted.length; i++) {
    const t = times[i];
    if (t === lastTime) continue;
    lastTime = t;
    const b = sorted[i];
    out.push({ time: t, open: b.open, high: b.high, low: b.low, close: b.close });
  }
  return out;
}

/**
 * Heikin-Ashi transform. Each HA candle's open is the midpoint of the
 * prior HA candle; the close is the average of the raw OHLC.
 */
export function toHeikinAshi(bars: Bar[]): ChartPoint[] {
  const { sorted, times } = sortedBarsWithTimes(bars);
  if (sorted.length === 0) return [];
  let haOpen = (sorted[0].open + sorted[0].close) / 2;
  const out: ChartPoint[] = [];
  for (let i = 0; i < sorted.length; i++) {
    const b = sorted[i];
    const haClose = (b.open + b.high + b.low + b.close) / 4;
    const haHigh = Math.max(b.high, haOpen, haClose);
    const haLow = Math.min(b.low, haOpen, haClose);
    out.push({ time: times[i], open: haOpen, high: haHigh, low: haLow, close: haClose });
    haOpen = (haOpen + haClose) / 2;
  }
  return out;
}

// --- Indicator math -------------------------------------------------------

/** EMA with standard `2/(period+1)` smoothing, seeded by SMA. */
export function computeEMA(bars: Bar[], period: number): OverlayPoint[] {
  if (bars.length < period) return [];
  const { sorted, times } = sortedBarsWithTimes(bars);
  const closes = sorted.map(b => b.close);
  const multiplier = 2 / (period + 1);
  let ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
  const result: OverlayPoint[] = [];
  for (let i = period - 1; i < closes.length; i++) {
    ema = closes[i] * multiplier + ema * (1 - multiplier);
    result.push({ time: times[i], value: ema });
  }
  return result;
}

/** Simple moving average over `period` closes. Uses a running sum so
  it's O(n) instead of O(n·period) from slicing a window per bar. */
  export function computeSMA(bars: Bar[], period: number): OverlayPoint[] {
    if (bars.length < period) return [];
    const { sorted, times } = sortedBarsWithTimes(bars);
    const result: OverlayPoint[] = [];
    let sum = 0;
    for (let i = 0; i < sorted.length; i++) {
      sum += sorted[i].close;
      if (i >= period) sum -= sorted[i - period].close;
      if (i >= period - 1) {
        result.push({ time: times[i], value: sum / period });
      }
    }
    return result;
  }

/**
 * ATR-based SuperTrend. Returns the final SuperTrend line as a sequence of
 * colored segments; at trend flips we emit a duplicate point so the line
 * transitions visually between the two colors.
 *
 * Parameters scale with timeframe: ATR is in price-per-candle units, so a
 * fixed period/multiplier is wrong across horizons. 1m ATR is a few cents
 * and a period-7/3x band whipsaws on every tick; 1d ATR spans weeks and the
 * same band lags months. Pick a period roughly matching the number of
 * candles in a few days and a multiplier that keeps the band outside
 * normal noise without chasing every spike.
 *
 * The band adjustment follows Goessman's published algorithm (the previous
 * version omitted the previous final value, so the bands drifted and flipped
 * more often than the standard implementation). Trend is seeded from the
 * first close vs the bands rather than forced bullish, so a chart that opens
 * in a downtrend doesn't get a phantom first flip.
 */
export function computeSuperTrend(
  bars: Bar[],
  period = 7,
  multiplier = 3,
  timeframe?: string,
): OverlayPoint[] {
  if (bars.length < period + 1) return [];
  const { sorted, times } = sortedBarsWithTimes(bars);
  const n = sorted.length;
  const highs = sorted.map(b => b.high);
  const lows = sorted.map(b => b.low);
  const closes = sorted.map(b => b.close);

  // True Range.
  const tr: number[] = new Array(n).fill(0);
  tr[0] = highs[0] - lows[0];
  for (let i = 1; i < n; i++) {
    tr[i] = Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1]),
    );
  }
  // ATR via Wilder smoothing (RMA).
  const atr: number[] = new Array(n).fill(0);
  let sum = 0;
  for (let i = 0; i < period; i++) sum += tr[i];
  atr[period - 1] = sum / period;
  for (let i = period; i < n; i++) {
    atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period;
  }

  const hl2 = sorted.map((_, i) => (highs[i] + lows[i]) / 2);
  const upperBand: number[] = new Array(n).fill(NaN);
  const lowerBand: number[] = new Array(n).fill(NaN);
  for (let i = period - 1; i < n; i++) {
    upperBand[i] = hl2[i] + multiplier * atr[i];
    lowerBand[i] = hl2[i] - multiplier * atr[i];
  }

  const result: OverlayPoint[] = [];
  let trendUp = closes[period] > upperBand[period - 1];
  let finalValue = trendUp ? lowerBand[period - 1] : upperBand[period - 1];
  for (let i = period; i < n; i++) {
    // Goessman band adjustment: in an uptrend the lower band can only
    // rise (never fall back toward price), and in a downtrend the upper
    // band can only fall. The reference is the PREVIOUS final value,
    // not the previous raw band — that's what keeps the line from
    // drifting and flipping on every bar.
    if (trendUp) {
      lowerBand[i] = Math.max(lowerBand[i], finalValue);
    } else {
      upperBand[i] = Math.min(upperBand[i], finalValue);
    }
    const prevFinal = finalValue;
    if (closes[i] > upperBand[i - 1]) {
      trendUp = true;
    } else if (closes[i] < lowerBand[i - 1]) {
      trendUp = false;
    }
    finalValue = trendUp ? lowerBand[i] : upperBand[i];

    const color = trendUp ? UP_COLOR : DOWN_COLOR;
    if (result.length > 0) {
      const last = result[result.length - 1];
      if (last.color !== color) {
        // emit duplicate point at the previous value with the new color, then advance.
        result.push({ time: times[i - 1], value: prevFinal, color });
      }
    }
    result.push({ time: times[i], value: finalValue, color });
  }
  return result;
}

// --- Memoized accessors used by the React component -----------------------

const dataCache = new WeakMap<Bar[], ChartPoint[]>();
const haCache = new WeakMap<Bar[], ChartPoint[]>();
const ema9Cache = new WeakMap<Bar[], OverlayPoint[]>();
const ema21Cache = new WeakMap<Bar[], OverlayPoint[]>();
const sma50Cache = new WeakMap<Bar[], OverlayPoint[]>();
const sma200Cache = new WeakMap<Bar[], OverlayPoint[]>();
const supertrendCache = new WeakMap<Bar[], OverlayPoint[]>();

export function getChartData(bars: Bar[]): ChartPoint[] {
  let c = dataCache.get(bars);
  if (!c) {
    c = toChartData(bars);
    dataCache.set(bars, c);
  }
  return c;
}

export function getHeikinAshi(bars: Bar[]): ChartPoint[] {
  let c = haCache.get(bars);
  if (!c) {
    c = toHeikinAshi(bars);
    haCache.set(bars, c);
  }
  return c;
}

export function getOverlayData(key: OverlayKey, bars: Bar[], timeframe?: string): OverlayPoint[] {
  switch (key) {
    case 'ema9': {
      let c = ema9Cache.get(bars);
      if (!c) { c = computeEMA(bars, 9); ema9Cache.set(bars, c); }
      return c;
    }
    case 'ema21': {
      let c = ema21Cache.get(bars);
      if (!c) { c = computeEMA(bars, 21); ema21Cache.set(bars, c); }
      return c;
    }
    case 'sma50': {
      let c = sma50Cache.get(bars);
      if (!c) { c = computeSMA(bars, 50); sma50Cache.set(bars, c); }
      return c;
    }
    case 'sma200': {
      let c = sma200Cache.get(bars);
      if (!c) { c = computeSMA(bars, 200); sma200Cache.set(bars, c); }
      return c;
    }
    case 'supertrend': {
      // Timeframe-aware params: ATR is in price-per-candle units, so a
      // fixed period/multiplier is wrong across horizons. 1m ATR is a few
      // cents and a period-7/3x band whipsaws on every tick; 1d ATR spans
      // weeks and the same band lags months. Pick a period matching the
      // number of candles in a few days and a multiplier that sits outside
      // normal noise without chasing every spike.
      const { period, multiplier } = superTrendParams(timeframe);
      const c = supertrendCache.get(bars);
      if (c) return c;
      const computed = computeSuperTrend(bars, period, multiplier, timeframe);
      supertrendCache.set(bars, computed);
      return computed;
    }
  }
}

/**
 * SuperTrend period/multiplier per timeframe. Longer horizons use a
 * longer lookback (more candles per day) and a wider multiple so the
 * band doesn't flip on every bar.
 */
export function superTrendParams(timeframe?: string): { period: number; multiplier: number } {
  switch (timeframe) {
    case '1m': return { period: 14, multiplier: 2.5 };
    case '5m': return { period: 14, multiplier: 2.5 };
    case '15m': return { period: 14, multiplier: 3 };
    case '30m': return { period: 14, multiplier: 3 };
    case '1h': return { period: 14, multiplier: 3 };
    case '4h': return { period: 14, multiplier: 3.5 };
    case '1d': return { period: 10, multiplier: 4 };
    default: return { period: 10, multiplier: 3 };
  }
}
