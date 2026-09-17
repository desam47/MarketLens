import React, { useState, useEffect, useCallback, useMemo } from 'react';
import api, { Bar, BarsResult } from '../services/api';
import { CandlestickChart, ChartType } from './CandlestickChart';
import { OVERLAYS, type OverlayKey } from './chartMath';
import { useMarketStream, MarketSub } from '../hooks/useMarketStream';
import { DEFAULT_GRID_TIMEFRAMES, TIMEFRAME_LABELS } from '../utils/timeframeUtils';

interface MultiTimeframeChartGridProps {
  symbol: string;
  /** Timeframes to display in the grid. Default: 4 standard ones. */
  timeframes?: string[];
  /** Initial chart type for all panels. */
  initialChartType?: ChartType;
  /** Per-panel height in pixels. */
  panelHeight?: number;
  /** Enable live bar updates via WebSocket. Default: true. */
  liveUpdate?: boolean;
  /** Ticker search box rendered in this card's header (same as the
  single-chart card). */
  tickerSearch?: React.ReactNode;
  /** Current chart layout mode, for the Single/Multi-TF toggle. */
  chartMode?: 'single' | 'multi';
  /** Switch back to the single-chart card. */
  onChartModeChange?: (mode: 'single' | 'multi') => void;
}

const DEFAULT_TIMEFRAMES = DEFAULT_GRID_TIMEFRAMES;

interface PanelState {
  timeframe: string;
  bars: Bar[];
  loading: boolean;
  error: string | null;
}

const TF_LABELS = TIMEFRAME_LABELS;

/**
 * Multi-timeframe chart grid: 1, 2, or 4 charts side-by-side so traders can
 * see the same symbol across short-term and long-term horizons at once.
 * All panels share the chart type (candlestick/line/area/etc.).
 */
export function MultiTimeframeChartGrid({
  symbol,
  timeframes = DEFAULT_TIMEFRAMES,
  initialChartType = 'heikin-ashi',
  panelHeight = 320,
  liveUpdate = true,
  tickerSearch,
  chartMode,
  onChartModeChange,
}: MultiTimeframeChartGridProps) {
  const [chartType, setChartType] = useState<ChartType>(initialChartType);
  // Shared overlay state — one toolbar at the top of the grid toggles
  // every panel's indicators at once instead of each panel managing its
  // own (which would let them drift apart).
  const [activeOverlays, setActiveOverlays] = useState<Set<OverlayKey>>(
    () => new Set<OverlayKey>(['supertrend']),
  );
  const [panels, setPanels] = useState<PanelState[]>(() =>
    timeframes.map(tf => ({ timeframe: tf, bars: [], loading: true, error: null })),
  );

  const handleToggleOverlay = useCallback((key: OverlayKey) => {
    setActiveOverlays(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const handleChartTypeChange = useCallback((next: ChartType) => setChartType(next), []);

  // ── Live bar updates via WebSocket ──────────────────────────────────────
  const subscriptions: MarketSub[] = useMemo(
    () => liveUpdate ? timeframes.map(tf => ({ symbol, timeframe: tf })) : [],
    [liveUpdate, symbol, timeframes.join(',')],
  );

  const { latestBars } = useMarketStream({ subscriptions });

  // Apply incoming bars to panels. The incoming bar either replaces the last
  // bar (same timestamp = tick update) or appends a new one (new bar close).
  useEffect(() => {
    setPanels(prev => {
      let changed = false;
      const next = prev.map(panel => {
        const key = `${symbol.toUpperCase()}:${panel.timeframe.toLowerCase()}`;
        const update = latestBars[key];
        if (!update || panel.loading || panel.bars.length === 0) return panel;

        const lastBar = panel.bars[panel.bars.length - 1];
        const updateTs = update.timestamp;

        // Match by timestamp string (ISO format from backend).
        if (lastBar.timestamp === updateTs) {
          // Same bar — update its fields in-place.
          const updatedBar: Bar = {
            ...lastBar,
            open: update.open ?? lastBar.open,
            high: update.high ?? lastBar.high,
            low: update.low ?? lastBar.low,
            close: update.close ?? lastBar.close,
            volume: update.volume ?? lastBar.volume,
          };
          changed = true;
          return { ...panel, bars: [...panel.bars.slice(0, -1), updatedBar] };
        } else {
          // New bar — append.
          const newBar: Bar = {
            timestamp: updateTs ?? lastBar.timestamp,
            open: update.open ?? lastBar.open,
            high: update.high ?? lastBar.high,
            low: update.low ?? lastBar.low,
            close: update.close ?? lastBar.close,
            volume: update.volume ?? 0,
          };
          changed = true;
          return { ...panel, bars: [...panel.bars, newBar] };
        }
      });
      return changed ? next : prev;
    });
  }, [latestBars, symbol]);

  // ── Initial data fetch ──────────────────────────────────────────────────
  const fetchPanel = useCallback(async (tf: string) => {
    setPanels(prev => prev.map(p =>
      p.timeframe === tf ? { ...p, loading: true, error: null } : p,
    ));
    try {
      const res: BarsResult = await api.getAnalysisBars(symbol, tf, 10000);
      const bars = res?.bars ?? [];
      setPanels(prev => prev.map(p =>
        p.timeframe === tf
          ? { ...p, bars, loading: false, error: null }
          : p,
      ));
    } catch (e: any) {
      setPanels(prev => prev.map(p =>
        p.timeframe === tf
          ? { ...p, loading: false, error: e?.message || 'Failed to load' }
          : p,
      ));
    }
  }, [symbol]);

  useEffect(() => {
    // Reset panels when symbol changes
    setPanels(timeframes.map(tf => ({ timeframe: tf, bars: [], loading: true, error: null })));
    // Fetch all in parallel
    timeframes.forEach(tf => { fetchPanel(tf); });
  }, [symbol, timeframes, fetchPanel]);

  // Determine grid layout: 1 panel = full width, 2 = side-by-side, 4 = 2x2.
  const layoutClass = useMemo(() => {
    switch (panels.length) {
      case 1: return 'mtf-grid mtf-grid-1';
      case 2: return 'mtf-grid mtf-grid-2';
      case 3: return 'mtf-grid mtf-grid-3';
      default: return 'mtf-grid mtf-grid-4';
    }
  }, [panels.length]);

  return (
    <div className="card analysis-card mtf-card">
      <div className="card-header-row">
        <h2>{symbol} — Multi-Timeframe</h2>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span className="bar-count">{panels.length} charts</span>
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
      <div className="mtf-shared-toolbars">
        <div className="chart-type-toolbar">
          <span className="mtf-shared-label">All panels:</span>
          {(['candlestick', 'bar', 'line', 'area', 'heikin-ashi'] as ChartType[]).map(t => (
            <button
              key={t}
              type="button"
              className={`chart-type-btn${chartType === t ? ' active' : ''}`}
              onClick={() => handleChartTypeChange(t)}
            >
              {t === 'heikin-ashi' ? 'HA' : t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
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
                onClick={() => handleToggleOverlay(def.key)}
              >
                <span className="overlay-dot" style={{ backgroundColor: def.color }} />
                {def.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className={layoutClass}>
        {panels.map(panel => (
          <div key={panel.timeframe} className="mtf-panel">
            <div className="mtf-panel-header">
              <span className="mtf-tf-badge">{TF_LABELS[panel.timeframe] ?? panel.timeframe}</span>
              {panel.loading && <span className="mtf-loading">Loading…</span>}
              {panel.error && <span className="mtf-error">⚠ {panel.error}</span>}
              {!panel.loading && !panel.error && (
                <span className="mtf-bar-count">{panel.bars.length} bars</span>
              )}
            </div>
            {panel.error ? (
              <div className="mtf-panel-error">Unable to load {panel.timeframe} data.</div>
            ) : (
              <CandlestickChart
                bars={panel.bars}
                symbol={symbol}
                height={panelHeight}
                initialChartType={chartType}
                initialActiveOverlays={['ema9', 'ema21', 'supertrend']}
                showVolume={false}
                showOverlayToolbar={false}
                activeOverlays={activeOverlays}
                onToggleOverlay={handleToggleOverlay}
                hideHeader
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default React.memo(MultiTimeframeChartGrid);
