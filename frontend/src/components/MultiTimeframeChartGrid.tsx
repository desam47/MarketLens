import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import api, { Bar, BarsResult } from '../services/api';
import { CandlestickChart, ChartType } from './CandlestickChart';
import { MarketDataFreshnessBadge } from './MarketDataFreshnessBadge';
import { OVERLAYS, parseET, type OverlayKey } from './chartMath';
import { useMarketStream, MarketSub } from '../hooks/useMarketStream';
import { useMarketSession } from '../hooks/useMarketSession';
import { DEFAULT_GRID_TIMEFRAMES, TIMEFRAME_LABELS } from '../utils/timeframeUtils';
import { sessionMatchesPreference, type SessionPreference } from '../utils/marketSession';

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
  sessionFilter?: SessionPreference;
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
  sessionFilter = 'all',
}: MultiTimeframeChartGridProps) {
  const [chartType, setChartType] = useState<ChartType>(initialChartType);
  const marketSession = useMarketSession();
  // Shared overlay state — one toolbar at the top of the grid toggles
  // every panel's indicators at once instead of each panel managing its
  // own (which would let them drift apart).
  const [activeOverlays, setActiveOverlays] = useState<Set<OverlayKey>>(
    () => new Set<OverlayKey>(['supertrend']),
  );
  const [panels, setPanels] = useState<PanelState[]>(() =>
    timeframes.map(tf => ({ timeframe: tf, bars: [], loading: true, error: null })),
  );
  const requestGenerationRef = useRef(0);

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
    [liveUpdate, symbol, timeframes],
  );

  const { latestBars } = useMarketStream({ subscriptions });

  // Apply incoming bars to panels. API history is newest-first, so locate the
  // matching minute bucket instead of assuming the last array element is the
  // active candle. The local Webull stream can update that candle repeatedly.
  useEffect(() => {
    setPanels(prev => {
      let changed = false;
      const next = prev.map(panel => {
        const key = `${symbol.toUpperCase()}:${panel.timeframe.toLowerCase()}`;
        const update = latestBars[key];
        if (!update || panel.loading || panel.bars.length === 0) return panel;

        const updateTs = update.timestamp;
        if (updateTs == null || update.close == null) return panel;
        const updateBucket = Math.floor(parseET(updateTs).getTime() / 60_000);
        const index = panel.bars.findIndex(
          bar => Math.floor(parseET(bar.timestamp).getTime() / 60_000) === updateBucket,
        );
        const existing = index >= 0 ? panel.bars[index] : null;

        if (existing) {
          const updatedBar: Bar = {
            ...existing,
            open: update.open ?? existing.open,
            high: update.high ?? existing.high,
            low: update.low ?? existing.low,
            close: update.close,
            volume: update.volume ?? existing.volume,
            data_status: update.data_status ?? 'LIVE',
            source: update.source ?? 'webull_stream',
          };
          changed = true;
          const next = [...panel.bars];
          next[index] = updatedBar;
          return { ...panel, bars: next };
        }

        const newBar: Bar = {
          timestamp: updateTs,
          open: update.open ?? update.close,
          high: update.high ?? update.close,
          low: update.low ?? update.close,
          close: update.close,
          volume: update.volume ?? 0,
          data_status: update.data_status ?? 'LIVE',
          source: update.source ?? 'webull_stream',
        };
        changed = true;
        return {
          ...panel,
          bars: [...panel.bars, newBar].sort(
            (a, b) => parseET(b.timestamp).getTime() - parseET(a.timestamp).getTime(),
          ),
        };
      });
      return changed ? next : prev;
    });
  }, [latestBars, symbol]);

  // ── Initial data fetch ──────────────────────────────────────────────────
  const fetchPanel = useCallback(async (tf: string, requestSymbol: string, generation: number) => {
    if (requestGenerationRef.current !== generation) return;
    setPanels(prev => prev.map(p =>
      p.timeframe === tf ? { ...p, loading: true, error: null } : p,
    ));
    try {
      // Per-timeframe cap — 1m alone is 10000+ rows and ~1.5MB of JSON,
      // which took 2-40s on the wire. Cap each panel so the grid of 6
      // loads fast instead of one slow 1m panel blocking everything.
      const LIMITS: Record<string, number> = { '1m': 2000, '5m': 3000, '15m': 4000, '30m': 4000, '1h': 4000, '4h': 3000, '1d': 2000 };
      const res: BarsResult = await api.getAnalysisBars(requestSymbol, tf, LIMITS[tf] ?? 5000);
      if (requestGenerationRef.current !== generation) return;
      const bars = res?.bars ?? [];
      setPanels(prev => prev.map(p =>
        p.timeframe === tf
          ? { ...p, bars, loading: false, error: null }
          : p,
      ));
    } catch (e: any) {
      if (requestGenerationRef.current !== generation) return;
      setPanels(prev => prev.map(p =>
        p.timeframe === tf
          ? { ...p, loading: false, error: e?.message || 'Failed to load' }
          : p,
      ));
    }
  }, []);

  useEffect(() => {
    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    // Reset panels when symbol changes
    setPanels(timeframes.map(tf => ({ timeframe: tf, bars: [], loading: true, error: null })));
    // Fetch all in parallel
    timeframes.forEach(tf => { fetchPanel(tf, symbol, generation); });
    return () => {
      if (requestGenerationRef.current === generation) {
        requestGenerationRef.current += 1;
      }
    };
  }, [symbol, timeframes, fetchPanel]);

  // Always 2 columns — 6 timeframes stack as 3 rows of 2. The grid
  // reflows on its own as the browser resizes.
  const layoutClass = 'mtf-grid mtf-grid-2';

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
        <div className="chart-session-legend" aria-label="Multi-timeframe market session legend">
          <span className="chart-session-legend-title">Session: {sessionFilter}</span>
          <span className="chart-session-key session-premarket"><span className="chart-session-swatch" />Premarket</span>
          <span className="chart-session-key session-regular"><span className="chart-session-swatch" />Regular</span>
          <span className="chart-session-key session-after_hours"><span className="chart-session-swatch" />After-hours</span>
        </div>
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
                <>
                  <span className="mtf-bar-count">{panel.bars.length} bars</span>
                  <MarketDataFreshnessBadge
                    dataStatus={panel.bars[0]?.data_status}
                    timestamp={panel.bars[0]?.timestamp}
                    marketSession={marketSession?.session}
                  />
                </>
              )}
            </div>
            {panel.error ? (
              <div className="mtf-panel-error">Unable to load {panel.timeframe} data.</div>
            ) : (
              <CandlestickChart
                bars={panel.bars.filter((bar) => sessionMatchesPreference(bar, sessionFilter))}
                symbol={symbol}
                height={panelHeight}
                initialChartType={chartType}
                timeframe={panel.timeframe}
                initialActiveOverlays={['ema9', 'ema21', 'supertrend']}
                showVolume={false}
                showOverlayToolbar={false}
                activeOverlays={activeOverlays}
                onToggleOverlay={handleToggleOverlay}
                marketSession={marketSession?.session}
                bare
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default React.memo(MultiTimeframeChartGrid);
