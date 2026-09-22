import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Bar, Transition } from '../services/api';
import { MarketDataFreshnessBadge } from './MarketDataFreshnessBadge';
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
  tickerSearch?: React.ReactNode;
  /** When false, hide this card's own overlay toolbar (used by the
  multi-TF grid, which renders one shared toolbar instead). */
  showOverlayToolbar?: boolean;
  /** Controlled overlay set — when provided, the card doesn't manage
  its own overlay state and just renders the given set. */
  activeOverlays?: Set<OverlayKey>;
  onToggleOverlay?: (key: OverlayKey) => void;
  /** Hide the card header entirely — used by the multi-TF grid, which
  # renders one shared header instead of a per-panel one. */
  hideHeader?: boolean;
  /** Render only the chart canvas, no card/header/toolbar wrapper.
  Used by the multi-TF grid so each panel is just the container. */
  bare?: boolean;
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
  tickerSearch,
  showOverlayToolbar = true,
  activeOverlays: activeOverlaysProp,
  onToggleOverlay,
  hideHeader = false,
  bare = false,
}: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ChartLike | null>(null);
  const seriesRef = useRef<SeriesLike | null>(null);
  const volumeRef = useRef<SeriesLike | null>(null);
  const overlaySeriesRef = useRef<Map<OverlayKey, SeriesLike>>(new Map());
  const fittedDataKeyRef = useRef<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [internalOverlays, setInternalOverlays] = useState<Set<OverlayKey>>(
    () => new Set<OverlayKey>(initialActiveOverlays),
  );
  const [chartType, setChartType] = useState<ChartType>(initialChartType);
  const sessionCounts = bars.reduce<Record<string, number>>((counts, bar) => {
    if (bar.session) counts[bar.session] = (counts[bar.session] || 0) + 1;
    return counts;
  }, {});
  const sessionBands = useMemo(() => bars.length > 0 ? (() => {
    const sorted = [...bars].sort((a, b) => toTime(a) - toTime(b));
    const bands: Array<{ session: string; start: number; end: number }> = [];
    sorted.forEach((bar, index) => {
      const session = bar.session || 'regular';
      const previous = bands[bands.length - 1];
      if (previous?.session === session) previous.end = index + 1;
      else bands.push({ session, start: index, end: index + 1 });
    });
    return bands;
  })() : [], [bars]);
  const [sessionBandPositions, setSessionBandPositions] = useState<Array<{ session: string; left: number; width: number }>>([]);

  // The multi-timeframe grid controls all child chart types from one shared
  // toolbar. Keep the local chart state in sync when that controlled initial
  // value changes after mount; useState only consumes its initializer once.
  useEffect(() => {
    setChartType(initialChartType);
  }, [initialChartType]);

  // Controlled vs uncontrolled overlay mode. When the parent (the
  // multi-TF grid) passes activeOverlays + onToggleOverlay, the grid owns
  // the set and every panel reflects the same toggles; otherwise this card
  // manages its own state like the single-chart card does.
  const activeOverlays = activeOverlaysProp ?? internalOverlays;
  const toggleOverlay = useCallback((key: OverlayKey) => {
    if (onToggleOverlay) {
      onToggleOverlay(key);
      return;
    }
    setInternalOverlays(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, [onToggleOverlay]);

  // Stable callbacks — these are passed as props and would otherwise create
  // new function identities on every render.
  const handleChartTypeChange = useCallback((next: ChartType) => {
    setChartType(next);
  }, []);

  // Build the chart once.
  useEffect(() => {
    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;
    let wheelContainer: HTMLDivElement | null = null;
    let wheelHandler: ((event: WheelEvent) => void) | null = null;
    const overlayMap = overlaySeriesRef.current;

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
          // Keep navigation explicit. This matters inside the multi-TF grid,
          // where parent layout handlers/browser trackpad defaults can
          // otherwise prevent lightweight-charts from receiving scale input.
          handleScale: {
            mouseWheel: true,
            pinch: true,
            axisPressedMouseMove: true,
            axisDoubleClickReset: true,
          },
          handleScroll: {
            mouseWheel: true,
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: true,
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

        // Fallback wheel navigation for embedded/multi-panel layouts. Some
        // browsers route wheel events to the scrolling page before the
        // library's default handler; handling it on the chart element keeps
        // zoom reliable without changing the surrounding page scroll.
        wheelContainer = containerRef.current;
        wheelHandler = (event: WheelEvent) => {
          const range = chart.timeScale().getVisibleLogicalRange();
          if (!range || !wheelContainer || wheelContainer.clientWidth <= 0) return;
          event.preventDefault();
          const span = Math.max(2, range.to - range.from);
          const factor = event.deltaY > 0 ? 1.15 : 0.87;
          const anchor = Math.max(0, Math.min(1, event.offsetX / wheelContainer.clientWidth));
          const nextSpan = Math.max(2, Math.min(span * factor, 10000));
          const anchorTime = range.from + span * anchor;
          chart.timeScale().setVisibleLogicalRange({
            from: anchorTime - nextSpan * anchor,
            to: anchorTime + nextSpan * (1 - anchor),
          });
        };
        wheelContainer?.addEventListener('wheel', wheelHandler, { passive: false });

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
      if (wheelContainer && wheelHandler) wheelContainer.removeEventListener('wheel', wheelHandler);
      const chart = chartRef.current;
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
      fittedDataKeyRef.current = null;
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
      // O(n) lookup instead of sorted.find() per point (was O(n²)).
      const byTime = new Map<number, Bar>();
      for (const b of sorted) byTime.set(toTime(b), b);
      const volData = dedupedAsc.map(d => {
        const matching = byTime.get(d.time);
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
      const seen = new Set<number>();
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
        .filter(m => {
          if (seen.has(m.time)) return false;
          seen.add(m.time);
          return true;
        })
        .sort((a, b) => a.time - b.time);
      try {
        seriesRef.current.setMarkers(markers);
      } catch {
        // ignore
      }
    } else {
      // Clear markers when the control is switched off or the transition
      // collection becomes empty; otherwise old markers remain on the series.
      try {
        seriesRef.current.setMarkers([]);
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
        const overlayData = dedupByTime<OverlayPoint>(getOverlayData(def.key, bars, timeframe));
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

    // Fit only when this chart first receives data or its symbol/timeframe
    // changes. Calling fitContent for every live candle update resets a
    // user's zoom immediately, which made multi-TF panels appear unzoomable.
    const dataKey = `${symbol}:${timeframe || 'default'}`;
    if (fittedDataKeyRef.current !== dataKey) {
      chart.timeScale().fitContent();
      fittedDataKeyRef.current = dataKey;
    }
  }, [bars, transitions, ready, activeOverlays, chartType, showMarkers, timeframe, symbol]);

  // Session shading must follow the chart's actual time scale. Percentage
  // positions based on the loaded array drift as the user pans/zooms, so map
  // each contiguous session range through lightweight-charts coordinates and
  // recompute whenever the visible range or chart size changes.
  useEffect(() => {
    if (!ready || !chartRef.current || !sessionBands.length) {
      setSessionBandPositions([]);
      return undefined;
    }
    const chart = chartRef.current;
    const updatePositions = () => {
      const width = containerRef.current?.clientWidth || 0;
      if (!width) return;
      const scale = chart.timeScale();
      const sorted = bars.slice().sort((a, b) => toTime(a) - toTime(b));
      const next = sessionBands.flatMap((band) => {
        const left = scale.timeToCoordinate(toTime(sorted[band.start]));
        const endBar = sorted[Math.min(band.end, sorted.length - 1)];
        const right = band.end >= sorted.length ? width : scale.timeToCoordinate(toTime(endBar));
        if (left == null || right == null) return [];
        const start = Math.max(0, Math.min(width, Math.min(left, right)));
        const end = Math.max(0, Math.min(width, Math.max(left, right)));
        return end > start ? [{ session: band.session, left: start, width: end - start }] : [];
      });
      setSessionBandPositions(next);
    };
    updatePositions();
    const timeScale = chart.timeScale();
    timeScale.subscribeVisibleTimeRangeChange(updatePositions);
    const resizeObserver = containerRef.current ? new ResizeObserver(updatePositions) : null;
    if (resizeObserver && containerRef.current) resizeObserver.observe(containerRef.current);
    return () => {
      timeScale.unsubscribeVisibleTimeRangeChange(updatePositions);
      resizeObserver?.disconnect();
    };
  }, [bars, ready, sessionBands]);

  if (err) {
    return (
      <div className="card analysis-card chart-error">
        <h2>{symbol} Price Chart</h2>
        <p className="empty-state">⚠ Chart unavailable: {err}</p>
      </div>
    );
  }

  if (bare) {
    return (
      <div className="chart-viewport" style={{ height: `${height}px` }}>
        <div className="chart-session-bands" aria-hidden="true">
          {sessionBandPositions.map((band, index) => <span key={`${band.session}-${index}`} className={`chart-session-band session-${band.session}`} style={{ left: `${band.left}px`, width: `${band.width}px` }} />)}
        </div>
        <div ref={containerRef} className="candlestick-container" style={{ width: '100%', height: `${height}px` }} />
      </div>
    );
  }

  return (
    <div className="card analysis-card candlestick-card">
      {hideHeader ? null : (
        <div className="card-header-row">
          <h2>{symbol} Price Chart</h2>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="bar-count">{bars.length} bars</span>
            <MarketDataFreshnessBadge
              dataStatus={bars[0]?.data_status}
              timestamp={bars[0]?.timestamp}
            />
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
            {tickerSearch}
          </span>
        </div>
      )}
      {showOverlayToolbar && (
        <>
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
        </>
      )}
      {!bare && Object.keys(sessionCounts).length > 0 && (
        <div className="chart-session-legend" aria-label="Chart market session legend">
          <span className="chart-session-legend-title">Sessions</span>
          {(['premarket', 'regular', 'after_hours'] as const).filter((session) => sessionCounts[session]).map((session) => (
            <span key={session} className={`chart-session-key session-${session}`}>
              <span className="chart-session-swatch" />
              {session.replace('_', ' ')} <small>{sessionCounts[session].toLocaleString()}</small>
            </span>
          ))}
        </div>
      )}
      <div className="chart-viewport" style={{ height: `${height}px` }}>
        <div className="chart-session-bands" aria-hidden="true">
          {sessionBandPositions.map((band, index) => <span key={`${band.session}-${index}`} className={`chart-session-band session-${band.session}`} style={{ left: `${band.left}px`, width: `${band.width}px` }} />)}
        </div>
        <div ref={containerRef} className="candlestick-container" style={{ width: '100%', height: `${height}px` }} />
      </div>
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
    prev.onChartModeChange === next.onChartModeChange &&
    prev.tickerSearch === next.tickerSearch &&
    prev.showOverlayToolbar === next.showOverlayToolbar &&
    prev.activeOverlays === next.activeOverlays &&
    prev.onToggleOverlay === next.onToggleOverlay &&
    prev.hideHeader === next.hideHeader &&
    prev.bare === next.bare
  );
});

export { CandlestickChart };
export type { ChartType, OverlayKey, OverlayPoint } from './chartMath';
export default CandlestickChart;
