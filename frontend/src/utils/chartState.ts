/** Explicit browser-local handoff from the chart to AI Hub Chat. */
export interface ChartState {
  symbol: string;
  timeframe: string;
  session: string;
  chart_type?: string;
  active_indicators?: string[];
  visible_range?: { from: number; to: number } | null;
  selected_candle?: {
    timestamp: string | number;
    open?: number | null;
    high?: number | null;
    low?: number | null;
    close?: number | null;
    volume?: number | null;
  } | null;
  drawings?: Array<{ type: string; label?: string | null; visible?: boolean }>;
  updated_at: string;
}

export const CHART_STATE_STORAGE_KEY = 'marketlens.chat.chart-state';

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function saveChartState(state: ChartState): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(CHART_STATE_STORAGE_KEY, JSON.stringify({
      ...state,
      symbol: state.symbol.toUpperCase(),
      active_indicators: (state.active_indicators ?? []).slice(0, 20),
      drawings: (state.drawings ?? []).slice(0, 50),
    }));
  } catch { /* optional browser storage */ }
}

export function loadChartState(): ChartState | null {
  if (typeof window === 'undefined') return null;
  try {
    const value = JSON.parse(window.localStorage.getItem(CHART_STATE_STORAGE_KEY) || 'null');
    if (!value || typeof value !== 'object' || typeof value.symbol !== 'string') return null;
    const visible = value.visible_range;
    return {
      symbol: value.symbol.toUpperCase(),
      timeframe: typeof value.timeframe === 'string' ? value.timeframe : '1d',
      session: typeof value.session === 'string' ? value.session : 'all',
      chart_type: typeof value.chart_type === 'string' ? value.chart_type : undefined,
      active_indicators: Array.isArray(value.active_indicators)
        ? value.active_indicators.filter((item: unknown): item is string => typeof item === 'string').slice(0, 20)
        : [],
      visible_range: visible && finite(visible.from) && finite(visible.to)
        ? { from: visible.from, to: visible.to }
        : null,
      selected_candle: value.selected_candle && typeof value.selected_candle === 'object' ? value.selected_candle : null,
      drawings: Array.isArray(value.drawings) ? value.drawings.slice(0, 50) : [],
      updated_at: typeof value.updated_at === 'string' ? value.updated_at : new Date().toISOString(),
    };
  } catch {
    return null;
  }
}
