import React, { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { Bar, Transition } from '../services/api';

// Lightweight-charts v4 — using the production build for tree-shaking.
// We import dynamically in a useEffect to avoid SSR/loader issues, but
// lightweight-charts v4 ships ESM so a static import works under CRA.

type ChartLike = any;
type SeriesLike = any;

interface CandlestickChartProps {
  bars: Bar[];
  symbol: string;
  transitions?: Transition[];
  height?: number;
  onError?: (err: Error) => void;
  initialChartType?: ChartType;
  showVolume?: boolean;
}

// --- Chart type config ---
export type ChartType = 'candlestick' | 'bar' | 'line' | 'area' | 'heikin-ashi';

interface ChartTypeDef {
  key: ChartType;
  label: string;
  shortLabel: string;
}

const CHART_TYPES: ChartTypeDef[] = [
  { key: 'candlestick', label: 'Candlestick', shortLabel: 'Candle' },
  { key: 'bar', label: 'OHLC Bars', shortLabel: 'Bars' },
  { key: 'line', label: 'Line', shortLabel: 'Line' },
  { key: 'area', label: 'Area', shortLabel: 'Area' },
  { key: 'heikin-ashi', label: 'Heikin-Ashi', shortLabel: 'HA' },
];

// --- Overlay config ---
type OverlayKey = 'ema9' | 'ema21' | 'sma50' | 'sma200' | 'supertrend';

interface OverlayDef {
  key: OverlayKey;
  label: string;
  color: string;
  lineWidth?: number;
}

const OVERLAYS: OverlayDef[] = [
  { key: 'ema9', label: 'EMA 9', color: '#3b82f6', lineWidth: 1 },
  { key: 'ema21', label: 'EMA 21', color: '#8b5cf6', lineWidth: 1 },
  { key: 'sma50', label: 'SMA 50', color: '#f59e0b', lineWidth: 1 },
  { key: 'sma200', label: 'SMA 200', color: '#f97316', lineWidth: 1 },
  { key: 'supertrend', label: 'SuperTrend', color: '#ec4899', lineWidth: 2 },
];

// Color palette — match the rest of the app.
const UP_COLOR = '#10b981';
const DOWN_COLOR = '#ef4444';
const GRID_COLOR = '#1f2937';
const TEXT_COLOR = '#9ca3af';

interface ChartPoint {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

function toChartData(bars: Bar[]): ChartPoint[] {
  const sorted = [...bars].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );
  // Deduplicate by timestamp — keep the last bar for each unique time.
  // lightweight-charts requires strictly ascending, unique timestamps.
  const deduped: ChartPoint[] = [];
  let lastTime: number | null = null;
  for (const b of sorted) {
    const t = Math.floor(new Date(b.timestamp).getTime() / 1000);
    if (t === lastTime) continue; // skip duplicate
    lastTime = t;
    deduped.push({
      time: t,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    });
  }
  return deduped;
}

function toTime(b: Bar): number {
  return Math.floor(new Date(b.timestamp).getTime() / 1000);
}

// Final dedup guard: strips any remaining duplicate timestamps from an already-
// sorted-by-time array. lightweight-charts asserts ascending, unique timestamps.
function dedupByTime(data: ChartPoint[]): ChartPoint[] {
  const out: ChartPoint[] = [];
  let last = -1;
  for (const p of data) {
    if (p.time === last) continue;
    last = p.time;
    out.push(p);
  }
  return out;
}

// Dedupe overlay time/value points (same pattern, different type).
function dedupByTimeOverlay(data: { time: number; value: number }[]): { time: number; value: number }[] {
  const out: { time: number; value: number }[] = [];
  let last = -1;
  for (const p of data) {
    if (p.time === last) continue;
    last = p.time;
    out.push(p);
  }
  return out;
}

// Heikin-Ashi transform. Each HA candle's open is the midpoint of the
// prior HA candle (so HA series is recursive: we walk the original
// series forward, building each HA bar from the prior HA bar's open/close
// and the current raw bar's high/low/close).
function toHeikinAshi(bars: Bar[]): ChartPoint[] {
  const sorted = [...bars].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );
  // Deduplicate: keep last bar per timestamp (same guard as toChartData).
  const deduped: Bar[] = [];
  let lastTs: number | null = null;
  for (const b of sorted) {
    const t = new Date(b.timestamp).getTime();
    if (t === lastTs) continue;
    lastTs = t;
    deduped.push(b);
  }
  if (deduped.length === 0) return [];
  const out: ChartPoint[] = [];
  // First HA bar uses its raw open as open, midpoint as close.
  let haOpen = (deduped[0].open + deduped[0].close) / 2;
  for (const b of deduped) {
    const haClose = (b.open + b.high + b.low + b.close) / 4;
    const haHigh = Math.max(b.high, haOpen, haClose);
    const haLow = Math.min(b.low, haOpen, haClose);
    out.push({
      time: toTime(b),
      open: haOpen,
      high: haHigh,
      low: haLow,
      close: haClose,
    });
    haOpen = (haOpen + haClose) / 2;
  }
  return out;
}

function computeEMA(bars: Bar[], period: number): { time: number; value: number }[] {
  if (bars.length < period) return [];
  const sorted = [...bars].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  const closes = sorted.map(b => b.close);
  const multiplier = 2 / (period + 1);
  let ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
  const result: { time: number; value: number }[] = [];
  for (let i = period - 1; i < closes.length; i++) {
    ema = closes[i] * multiplier + ema * (1 - multiplier);
    result.push({ time: toTime(sorted[i]), value: ema });
  }
  return result;
}

function computeSMA(bars: Bar[], period: number): { time: number; value: number }[] {
  if (bars.length < period) return [];
  const sorted = [...bars].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  const result: { time: number; value: number }[] = [];
  for (let i = period - 1; i < sorted.length; i++) {
    const sum = sorted.slice(i - period + 1, i + 1).reduce((a, b) => a + b.close, 0);
    const len = sorted.slice(i - period + 1, i + 1).length;
    if (len < period) continue;
    result.push({ time: toTime(sorted[i]), value: sum / period });
  }
  return result;
}

// ATR-based SuperTrend. Returns the final SuperTrend line with two-color
// segments (green for uptrend, red for downtrend) by emitting adjacent points
// with the appropriate color value (lightweight-charts draws a polyline so
// sharp color changes are best handled by emitting two points at the flip).
function computeSuperTrend(
  bars: Bar[],
  period = 7,
  multiplier = 3,
): { time: number; value: number; color?: string }[] {
  if (bars.length < period + 1) return [];
  const sorted = [...bars].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  const n = sorted.length;
  const highs = sorted.map(b => b.high);
  const lows = sorted.map(b => b.low);
  const closes = sorted.map(b => b.close);

  // True Range series.
  const tr: number[] = new Array(n).fill(0);
  tr[0] = highs[0] - lows[0];
  for (let i = 1; i < n; i++) {
    tr[i] = Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1]),
    );
  }
  // ATR via RMA (Wilder smoothing).
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

  // Final SuperTrend with flip logic. Output: lower band in uptrend,
  // upper band in downtrend. We emit colored segments at flips so the
  // line visually transitions green ↔ red.
  const result: { time: number; value: number; color?: string }[] = [];
  const upColor = UP_COLOR;
  const downColor = DOWN_COLOR;
  let trendUp = true;
  let finalValue = lowerBand[period - 1];
  for (let i = period; i < n; i++) {
    // Wilder band adjustment: in an uptrend the lower band cannot go down,
    // in a downtrend the upper band cannot go up.
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

    const color = trendUp ? upColor : downColor;
    if (result.length > 0) {
      const last = result[result.length - 1];
      const lastColor = last.color;
      if (lastColor !== color) {
        // emit duplicate point at prev value with new color, then new value
        result.push({ time: toTime(sorted[i]), value: prevFinal, color });
      }
    }
    result.push({ time: toTime(sorted[i]), value: finalValue, color });
  }
  return result;
}

// Memoization helpers (top-level so referential identity is stable).
const memo = {
  bars: new WeakMap<Bar[], Bar[]>(),
  data: new WeakMap<Bar[], ChartPoint[]>(),
  ha: new WeakMap<Bar[], ChartPoint[]>(),
  ema9: new WeakMap<Bar[], { time: number; value: number }[]>(),
  ema21: new WeakMap<Bar[], { time: number; value: number }[]>(),
  sma50: new WeakMap<Bar[], { time: number; value: number }[]>(),
  sma200: new WeakMap<Bar[], { time: number; value: number }[]>(),
  supertrend: new WeakMap<Bar[], { time: number; value: number; color?: string }[]>(),
};

function sortedBars(bars: Bar[]): Bar[] {
  let cached = memo.bars.get(bars);
  if (!cached) {
    cached = [...bars].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
    memo.bars.set(bars, cached);
  }
  return cached;
}

function getChartData(bars: Bar[]): ChartPoint[] {
  let cached = memo.data.get(bars);
  if (!cached) {
    cached = toChartData(bars);
    memo.data.set(bars, cached);
  }
  return cached;
}

function getHeikinAshi(bars: Bar[]): ChartPoint[] {
  let cached = memo.ha.get(bars);
  if (!cached) {
    cached = toHeikinAshi(bars);
    memo.ha.set(bars, cached);
  }
  return cached;
}

function getOverlayData(key: OverlayKey, bars: Bar[]) {
  switch (key) {
    case 'ema9': {
      let c = memo.ema9.get(bars);
      if (!c) { c = computeEMA(bars, 9); memo.ema9.set(bars, c); }
      return c;
    }
    case 'ema21': {
      let c = memo.ema21.get(bars);
      if (!c) { c = computeEMA(bars, 21); memo.ema21.set(bars, c); }
      return c;
    }
    case 'sma50': {
      let c = memo.sma50.get(bars);
      if (!c) { c = computeSMA(bars, 50); memo.sma50.set(bars, c); }
      return c;
    }
    case 'sma200': {
      let c = memo.sma200.get(bars);
      if (!c) { c = computeSMA(bars, 200); memo.sma200.set(bars, c); }
      return c;
    }
    case 'supertrend': {
      let c = memo.supertrend.get(bars);
      if (!c) { c = computeSuperTrend(bars, 7, 3); memo.supertrend.set(bars, c); }
      return c;
    }
  }
}

function CandlestickChartImpl({
  bars,
  symbol,
  transitions = [],
  height = 400,
  onError,
  initialChartType = 'candlestick',
  showVolume = true,
}: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ChartLike | null>(null);
  const seriesRef = useRef<SeriesLike | null>(null);
  const volumeRef = useRef<SeriesLike | null>(null);
  const overlaySeriesRef = useRef<Map<OverlayKey, SeriesLike>>(new Map());
  const [err, setErr] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [activeOverlays, setActiveOverlays] = useState<Set<OverlayKey>>(
    () => new Set<OverlayKey>(['ema9', 'ema21']),
  );
  const [chartType, setChartType] = useState<ChartType>(initialChartType);

  // Stable callbacks — these are passed as props and would otherwise create
  // new function identities on every render.
  const handleChartTypeChange = useCallback((next: ChartType) => {
    setChartType(next);
  }, []);

  const toggleOverlay = useCallback((key: OverlayKey) => {
    setActiveOverlays(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  // Build the chart once.
  useEffect(() => {
    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;

    (async () => {
      try {
        const lwc = await import('lightweight-charts');
        if (cancelled || !containerRef.current) return;

        const chart = lwc.createChart(containerRef.current, {
          width: containerRef.current.clientWidth,
          height,
          layout: {
            background: { type: lwc.ColorType.Solid, color: '#0f172a' },
            textColor: TEXT_COLOR,
            fontSize: 12,
          },
          grid: {
            vertLines: { color: GRID_COLOR },
            horzLines: { color: GRID_COLOR },
          },
          timeScale: {
            borderColor: GRID_COLOR,
            timeVisible: true,
            secondsVisible: false,
          },
          rightPriceScale: {
            borderColor: GRID_COLOR,
          },
          crosshair: {
            mode: lwc.CrosshairMode.Normal,
          },
        });

        if (showVolume) {
          const volumeSeries = chart.addHistogramSeries({
            priceFormat: { type: 'volume' },
            priceScaleId: 'volume',
          });
          chart.priceScale('volume').applyOptions({
            scaleMargins: { top: 0.8, bottom: 0 },
          });
          volumeRef.current = volumeSeries;
        }

        chartRef.current = chart;
        setReady(true);

        resizeObserver = new ResizeObserver(entries => {
          for (const entry of entries) {
            const w = entry.contentRect.width;
            if (w > 0 && chartRef.current) {
              chartRef.current.applyOptions({ width: w });
            }
          }
        });
        resizeObserver.observe(containerRef.current);
      } catch (e: any) {
        const msg = e?.message || 'Failed to load chart library';
        setErr(msg);
        onError?.(e);
      }
    })();

    return () => {
      cancelled = true;
      if (resizeObserver) resizeObserver.disconnect();
      const chart = chartRef.current;
      const overlayMap = overlaySeriesRef.current;
      if (chart) {
        try {
          chart.remove();
        } catch {
          // ignore — chart may have already been removed
        }
      }
      chartRef.current = null;
      seriesRef.current = null;
      volumeRef.current = null;
      overlayMap.clear();
    };
  }, [height, onError, showVolume]);

  // (Re)create the main price series when chartType changes.
  useEffect(() => {
    if (!ready || !chartRef.current) return;
    const chart = chartRef.current;

    // Remove the existing price series if any.
    if (seriesRef.current) {
      try { chart.removeSeries(seriesRef.current); } catch { /* ignore */ }
      seriesRef.current = null;
    }

    const lwc = (chart as any).constructor.prototype;
    // The series type determines which lightweight-charts factory we use.
    if (chartType === 'candlestick') {
      seriesRef.current = chart.addCandlestickSeries({
        upColor: UP_COLOR,
        downColor: DOWN_COLOR,
        borderUpColor: UP_COLOR,
        borderDownColor: DOWN_COLOR,
        wickUpColor: UP_COLOR,
        wickDownColor: DOWN_COLOR,
      });
    } else if (chartType === 'bar') {
      seriesRef.current = chart.addBarSeries({
        upColor: UP_COLOR,
        downColor: DOWN_COLOR,
      });
    } else if (chartType === 'line') {
      seriesRef.current = chart.addLineSeries({
        color: '#60a5fa',
        lineWidth: 2,
      });
    } else if (chartType === 'area') {
      seriesRef.current = chart.addAreaSeries({
        lineColor: '#60a5fa',
        topColor: 'rgba(96, 165, 250, 0.4)',
        bottomColor: 'rgba(96, 165, 250, 0.05)',
        lineWidth: 2,
      });
    } else if (chartType === 'heikin-ashi') {
      seriesRef.current = chart.addCandlestickSeries({
        upColor: UP_COLOR,
        downColor: DOWN_COLOR,
        borderUpColor: UP_COLOR,
        borderDownColor: DOWN_COLOR,
        wickUpColor: UP_COLOR,
        wickDownColor: DOWN_COLOR,
      });
    }
  }, [chartType, ready]);

  // Push bar data + manage overlay series when bars or activeOverlays change.
  useEffect(() => {
    if (!ready || !chartRef.current || !seriesRef.current) return;

    const data = chartType === 'heikin-ashi' ? getHeikinAshi(bars) : getChartData(bars);
    if (data.length === 0) {
      seriesRef.current.setData([]);
      if (volumeRef.current) volumeRef.current.setData([]);
      return;
    }

    // Final safety net: lightweight-charts requires strictly ascending,
    // unique timestamps. The toChartData/toHeikinAshi helpers already dedupe,
    // but this guards against any path that bypasses them and any future
    // data source that returns non-deduped bars.
    const dedupedAsc = dedupByTime(data);

    if (chartType === 'line' || chartType === 'area') {
      // Line/area series expects { time, value } pairs.
      seriesRef.current.setData(dedupedAsc.map(d => ({ time: d.time, value: d.close })));
    } else {
      // Candlestick / bar / heikin-ashi: OHLC.
      seriesRef.current.setData(dedupedAsc);
    }

    if (volumeRef.current) {
      // Volume data comes from the deduped series, indexed by timestamp.
      const sorted = sortedBars(bars);
      const volData = dedupedAsc.map(d => {
        const matching = sorted.find(b => toTime(b) === d.time);
        return {
          time: d.time,
          value: matching?.volume ?? 0,
          color: (matching?.close ?? 0) >= (matching?.open ?? 0)
            ? 'rgba(16, 185, 129, 0.4)'
            : 'rgba(239, 68, 68, 0.4)',
        };
      });
      volumeRef.current.setData(volData);
    }

    // Markers
    if (transitions.length > 0) {
      const markers = transitions
        .filter(t => t.timestamp)
        .map(t => {
          const ts = Math.floor(new Date(t.timestamp!).getTime() / 1000);
          const isBull = t.direction === 'bullish';
          return {
            time: ts,
            position: isBull ? 'belowBar' : 'aboveBar',
            color: isBull ? UP_COLOR : DOWN_COLOR,
            shape: isBull ? 'arrowUp' : 'arrowDown',
            text: t.type.replace(/_/g, ' '),
          };
        })
        .sort((a, b) => a.time - b.time)
        .filter((m, i, arr) => i === arr.findIndex(x => x.time === m.time));
      try {
        seriesRef.current.setMarkers(markers);
      } catch {
        // ignore
      }
    }

    // Overlay management: create series on first activation, set data, remove when off.
    const chart = chartRef.current;
    const overlayMap = overlaySeriesRef.current;
    const active = activeOverlays;

    for (const def of OVERLAYS) {
      let series = overlayMap.get(def.key);
      if (active.has(def.key)) {
        if (!series) {
          series = chart.addLineSeries({
            color: def.color,
            lineWidth: def.lineWidth ?? 1,
            priceLineVisible: false,
            lastValueVisible: false,
          });
          overlayMap.set(def.key, series);
        }
        const overlayData = dedupByTimeOverlay(getOverlayData(def.key, bars));
        series.setData(overlayData);
      } else if (series) {
        try {
          chart.removeSeries(series);
        } catch {
          // ignore
        }
        overlayMap.delete(def.key);
      }
    }

    chart.timeScale().fitContent();
  }, [bars, transitions, ready, activeOverlays, chartType]);

  if (err) {
    return (
      <div className="card analysis-card chart-error">
        <h2>{symbol} Price Chart</h2>
        <p className="empty-state">⚠ Chart unavailable: {err}</p>
      </div>
    );
  }

  return (
    <div className="card analysis-card candlestick-card">
      <div className="card-header-row">
        <h2>{symbol} Price Chart</h2>
        <span className="bar-count">{bars.length} bars</span>
      </div>
      <div className="chart-type-toolbar">
        {CHART_TYPES.map(def => {
          const active = chartType === def.key;
          return (
            <button
              key={def.key}
              type="button"
              className={`chart-type-btn${active ? ' active' : ''}`}
              onClick={() => handleChartTypeChange(def.key)}
              title={def.label}
            >
              {def.shortLabel}
            </button>
          );
        })}
      </div>
      <div className="chart-overlay-toolbar">
        {OVERLAYS.map(def => {
          const active = activeOverlays.has(def.key);
          return (
            <button
              key={def.key}
              type="button"
              className={`overlay-btn${active ? ' active' : ''}`}
              style={{ borderColor: active ? def.color : 'transparent' }}
              onClick={() => toggleOverlay(def.key)}
            >
              <span className="overlay-dot" style={{ backgroundColor: def.color }} />
              {def.label}
            </button>
          );
        })}
      </div>
      <div
        ref={containerRef}
        className="candlestick-container"
        style={{ width: '100%', height: `${height}px` }}
      />
    </div>
  );
}

// React.memo: skip re-render when props are shallow-equal. The chart effect
// already diffs bars/transitions/activeOverlays/chartType internally so
// most prop changes (e.g. parent re-renders with new closures) will be
// no-ops here. bars is the only prop that changes meaningfully for a
// live data tick — and we want to re-render on that.
const CandlestickChart = React.memo(CandlestickChartImpl, (prev, next) => {
  return (
    prev.bars === next.bars &&
    prev.transitions === next.transitions &&
    prev.symbol === next.symbol &&
    prev.height === next.height &&
    prev.onError === next.onError &&
    prev.initialChartType === next.initialChartType &&
    prev.showVolume === next.showVolume
  );
});

export { CandlestickChart };
export default CandlestickChart;
