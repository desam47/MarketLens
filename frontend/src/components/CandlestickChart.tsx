import React, { useEffect, useRef, useState, useMemo } from 'react';
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
}

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

function toChartData(bars: Bar[]) {
  const sorted = [...bars].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );
  return sorted.map(b => ({
    time: Math.floor(new Date(b.timestamp).getTime() / 1000),
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  }));
}

function toTime(b: Bar): number {
  return Math.floor(new Date(b.timestamp).getTime() / 1000);
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

function applyColoredData(
  series: SeriesLike,
  data: { time: number; value: number; color?: string }[],
) {
  // Emitting many small data points is the standard way to get multi-color
  // line series in lightweight-charts. Each entry becomes a line segment.
  series.setData(data);
}

export function CandlestickChart({
  bars,
  symbol,
  transitions = [],
  height = 400,
  onError,
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

  const sortedBars = useMemo(
    () => [...bars].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()),
    [bars],
  );

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

        const candleSeries = chart.addCandlestickSeries({
          upColor: UP_COLOR,
          downColor: DOWN_COLOR,
          borderUpColor: UP_COLOR,
          borderDownColor: DOWN_COLOR,
          wickUpColor: UP_COLOR,
          wickDownColor: DOWN_COLOR,
        });

        const volumeSeries = chart.addHistogramSeries({
          priceFormat: { type: 'volume' },
          priceScaleId: 'volume',
        });
        chart.priceScale('volume').applyOptions({
          scaleMargins: { top: 0.8, bottom: 0 },
        });

        chartRef.current = chart;
        seriesRef.current = candleSeries;
        volumeRef.current = volumeSeries;
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
      // eslint-disable-next-line react-hooks/exhaustive-deps
      const chart = chartRef.current;
      // eslint-disable-next-line react-hooks/exhaustive-deps
      const overlayMap = overlaySeriesRef.current;
      if (chart) {
        try {
          chart.remove();
        } catch {
          // ignore — chart may have already been removed
        }
      }
      // Nullify refs directly so subsequent renders don't try to use a removed chart.
      chartRef.current = null;
      seriesRef.current = null;
      volumeRef.current = null;
      overlayMap.clear();
    };
  }, [height, onError]);

  // Push bar data + manage overlay series when bars or activeOverlays change.
  useEffect(() => {
    if (!ready || !chartRef.current || !seriesRef.current || !volumeRef.current) return;

    const data = toChartData(bars);
    if (data.length === 0) {
      seriesRef.current.setData([]);
      volumeRef.current.setData([]);
      return;
    }
    seriesRef.current.setData(data);

    const volData = data.map(d => ({
      time: d.time,
      value: bars.find(b => Math.floor(new Date(b.timestamp).getTime() / 1000) === d.time)?.volume ?? 0,
      color: d.close >= d.open ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)',
    }));
    volumeRef.current.setData(volData);

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
        let overlayData: { time: number; value: number; color?: string }[];
        switch (def.key) {
          case 'ema9': overlayData = computeEMA(sortedBars, 9); break;
          case 'ema21': overlayData = computeEMA(sortedBars, 21).map(d => ({ ...d, color: def.color })); break;
          case 'sma50': overlayData = computeSMA(sortedBars, 50).map(d => ({ ...d, color: def.color })); break;
          case 'sma200': overlayData = computeSMA(sortedBars, 200).map(d => ({ ...d, color: def.color })); break;
          case 'supertrend': overlayData = computeSuperTrend(sortedBars, 7, 3); break;
        }
        applyColoredData(series, overlayData);
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
  }, [bars, transitions, ready, activeOverlays, sortedBars]);

  const toggleOverlay = (key: OverlayKey) => {
    setActiveOverlays(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

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

export default CandlestickChart;
