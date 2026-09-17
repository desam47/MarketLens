import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Bar, Transition } from '../services/api';
import {
  type ChartType,
  CHART_TYPES,
  OVERLAYS,
  UP_COLOR,
  DOWN_COLOR,
  TEXT_COLOR,
  toTime,
  sortedBars,
  dedupByTime,
  getChartData,
  getHeikinAshi,
  getOverlayData,
  parseET,
  etTickMarkFormatter,
  etTimeFormatter,
  type OverlayKey,
  type OverlayPoint,
} from './chartMath';

// Lightweight-charts v4 ships ESM so a static import works under CRA.
// We use a dynamic import inside useEffect to avoid SSR/loader issues.

type ChartLike = any;
type SeriesLike = any;

interface CandlestickChartProps {
  bars: Bar[];
  symbol: string;
  transitions?: Transition[];
  height?: number;
  onError?: (err: Error) => void;
  initialChartType?: ChartType;
  initialActiveOverlays?: OverlayKey[];
  showVolume?: boolean;
  showMarkers?: boolean;
  timeframe?: string;
  onTimeframeChange?: (tf: string) => void;
  timeframeOptions?: { value: string; label: string }[];
  chartMode?: 'single' | 'multi';
  onChartModeChange?: (mode: 'single' | 'multi') => void;
}

const LINE_COLOR = '#60a5fa';

function CandlestickChartImpl({
  bars,
  symbol,
  transitions = [],
  height = 400,
  onError,
  initialChartType = 'heikin-ashi',
  initialActiveOverlays = ['ema9', 'ema21'],
  showVolume = true,
  showMarkers = false,
  timeframe,
  onTimeframeChange,
  timeframeOptions,
  chartMode,
  onChartModeChange,
}: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ChartLike | null>(null);
  const seriesRef = useRef<SeriesLike | null>(null);
  const volumeRef = useRef<SeriesLike | null>(null);
  const overlaySeriesRef = useRef<Map<OverlayKey, SeriesLike>>(new Map());
  const [err, setErr] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [activeOverlays, setActiveOverlays] = useState<Set<OverlayKey>>(
    () => new Set<OverlayKey>(initialActiveOverlays),
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
            vertLines: { color: '#1f2937' },
            horzLines: { color: '#1f2937' },
          },
          timeScale: {
            borderColor: '#1f2937',
            timeVisible: true,
            secondsVisible: false,
            // Force ET display — lightweight-charts otherwise formats axis
            // labels using the viewer's browser/OS timezone, which shows
            // UTC (or whatever the machine is set to) instead of market time.
            tickMarkFormatter: etTickMarkFormatter,
          },
          localization: {
            timeFormatter: etTimeFormatter,
          },
          rightPriceScale: {
            borderColor: '#1f2937',
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
        color: LINE_COLOR,
        lineWidth: 2,
      });
    } else if (chartType === 'area') {
      seriesRef.current = chart.addAreaSeries({
        lineColor: LINE_COLOR,
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
    // unique timestamps. The chartMath helpers already dedupe, but this
    // guards against any path that bypasses them.
    const dedupedAsc = dedupByTime(data);

    if (chartType === 'line' || chartType === 'area') {
      seriesRef.current.setData(dedupedAsc.map(d => ({ time: d.time, value: d.close })));
    } else {
      seriesRef.current.setData(dedupedAsc);
    }

    if (volumeRef.current) {
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

    // Markers — only shown when showMarkers is true
    if (showMarkers && transitions.length > 0) {
      const markers = transitions
        .filter(t => t.timestamp)
        .map(t => {
          const ts = Math.floor(parseET(t.timestamp!).getTime() / 1000);
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
        const overlayData = dedupByTime<OverlayPoint>(getOverlayData(def.key, bars));
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
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="bar-count">{bars.length} bars</span>
          {timeframeOptions && timeframe !== undefined && onTimeframeChange && (
            <select
              className="timeframe-select chart-timeframe-select"
              value={timeframe}
              onChange={e => onTimeframeChange(e.target.value)}
              title="Chart timeframe"
            >
              {timeframeOptions.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          )}
          {chartMode && onChartModeChange && (
            <div className="chart-mode-toggle" role="group" aria-label="Chart layout">
              <button
                type="button"
                className={`chart-mode-btn${chartMode === 'single' ? ' active' : ''}`}
                onClick={() => onChartModeChange('single')}
                title="Single timeframe chart"
              >
                Single
              </button>
              <button
                type="button"
                className={`chart-mode-btn${chartMode === 'multi' ? ' active' : ''}`}
                onClick={() => onChartModeChange('multi')}
                title="Multi-timeframe grid (4 charts side by side)"
              >
                Multi-TF
              </button>
            </div>
          )}
        </span>
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
    prev.showVolume === next.showVolume &&
    prev.showMarkers === next.showMarkers &&
    prev.timeframe === next.timeframe &&
    prev.onTimeframeChange === next.onTimeframeChange &&
    prev.chartMode === next.chartMode &&
    prev.onChartModeChange === next.onChartModeChange
  );
});

export { CandlestickChart };
export type { ChartType, OverlayKey, OverlayPoint } from './chartMath';
export default CandlestickChart;
