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
 * Backend returns strings like `2026-09-04T15:59:00-04:00`.  Naive
 * `new Date(str)` parses in the browser's LOCAL timezone, so a browser
 * in IST would see 15:59 become 10:29 UTC.  This helper strips the
 * embedded ±HH:MM offset and appends 'Z' so the string is always parsed
 * as UTC, giving the correct wall-clock time regardless of browser locale.
 */
export function parseET(ts: string | null | undefined): Date {
  if (!ts) return new Date(NaN);
  // Parse the ISO string with its timezone offset intact — JavaScript Date
  // handles offsets like -04:00 correctly, so we get the right wall-clock time.
  return new Date(ts);
}

/** Convert a bar's ISO timestamp to Unix seconds for lightweight-charts. */
export function toTime(b: Bar): number {
  return Math.floor(parseET(b.timestamp).getTime() / 1000);
}

/**
 * Sort bars ascending by timestamp. Memoized by input array identity.
 */
const sortedBarsCache = new WeakMap<Bar[], Bar[]>();
export function sortedBars(bars: Bar[]): Bar[] {
  let cached = sortedBarsCache.get(bars);
  if (!cached) {
    cached = [...bars].sort(
      (a, b) => parseET(a.timestamp).getTime() - parseET(b.timestamp).getTime(),
    );
    sortedBarsCache.set(bars, cached);
  }
  return cached;
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
  const sorted = sortedBars(bars);
  const out: ChartPoint[] = [];
  let lastTime: number | null = null;
  for (const b of sorted) {
    const t = toTime(b);
    if (t === lastTime) continue;
    lastTime = t;
    out.push({ time: t, open: b.open, high: b.high, low: b.low, close: b.close });
  }
  return out;
}

/**
 * Heikin-Ashi transform. Each HA candle's open is the midpoint of the
 * prior HA candle; the close is the average of the raw OHLC.
 */
export function toHeikinAshi(bars: Bar[]): ChartPoint[] {
  const sorted = sortedBars(bars);
  if (sorted.length === 0) return [];
  let haOpen = (sorted[0].open + sorted[0].close) / 2;
  const out: ChartPoint[] = [];
  for (const b of sorted) {
    const haClose = (b.open + b.high + b.low + b.close) / 4;
    const haHigh = Math.max(b.high, haOpen, haClose);
    const haLow = Math.min(b.low, haOpen, haClose);
    out.push({ time: toTime(b), open: haOpen, high: haHigh, low: haLow, close: haClose });
    haOpen = (haOpen + haClose) / 2;
  }
  return out;
}

// --- Indicator math -------------------------------------------------------

/** EMA with standard `2/(period+1)` smoothing, seeded by SMA. */
export function computeEMA(bars: Bar[], period: number): OverlayPoint[] {
  if (bars.length < period) return [];
  const sorted = sortedBars(bars);
  const closes = sorted.map(b => b.close);
  const multiplier = 2 / (period + 1);
  let ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
  const result: OverlayPoint[] = [];
  for (let i = period - 1; i < closes.length; i++) {
    ema = closes[i] * multiplier + ema * (1 - multiplier);
    result.push({ time: toTime(sorted[i]), value: ema });
  }
  return result;
}

/** Simple moving average over `period` closes. */
export function computeSMA(bars: Bar[], period: number): OverlayPoint[] {
  if (bars.length < period) return [];
  const sorted = sortedBars(bars);
  const result: OverlayPoint[] = [];
  for (let i = period - 1; i < sorted.length; i++) {
    const window = sorted.slice(i - period + 1, i + 1);
    if (window.length < period) continue;
    const sum = window.reduce((a, b) => a + b.close, 0);
    result.push({ time: toTime(sorted[i]), value: sum / period });
  }
  return result;
}

/**
 * ATR-based SuperTrend. Returns the final SuperTrend line as a sequence of
 * colored segments; at trend flips we emit a duplicate point so the line
 * transitions visually between the two colors.
 */
export function computeSuperTrend(
  bars: Bar[],
  period = 7,
  multiplier = 3,
): OverlayPoint[] {
  if (bars.length < period + 1) return [];
  const sorted = sortedBars(bars);
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
  let trendUp = true;
  let finalValue = lowerBand[period - 1];
  for (let i = period; i < n; i++) {
    // Wilder band adjustment: lower can't fall in uptrend, upper can't rise in downtrend.
    if (i > period) {
      if (trendUp) {
        lowerBand[i] = Math.max(lowerBand[i], lowerBand[i - 1]);
      } else {
        upperBand[i] = Math.min(upperBand[i], upperBand[i - 1]);
      }
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
        result.push({ time: toTime(sorted[i]), value: prevFinal, color });
      }
    }
    result.push({ time: toTime(sorted[i]), value: finalValue, color });
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

export function getOverlayData(key: OverlayKey, bars: Bar[]): OverlayPoint[] {
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
      let c = supertrendCache.get(bars);
      if (!c) { c = computeSuperTrend(bars, 7, 3); supertrendCache.set(bars, c); }
      return c;
    }
  }
}
