import { CHART_STATE_STORAGE_KEY, loadChartState, saveChartState } from './chartState';

beforeEach(() => window.localStorage.clear());

test('round-trips bounded chart state for Chat', () => {
  saveChartState({
    symbol: 'aapl', timeframe: '1h', session: 'regular', chart_type: 'candlestick',
    active_indicators: ['ema9'], visible_range: { from: 1, to: 20 },
    selected_candle: { timestamp: 10, close: 201 }, drawings: [], updated_at: '2026-09-23T12:00:00Z',
  });
  const loaded = loadChartState();
  expect(loaded?.symbol).toBe('AAPL');
  expect(loaded?.visible_range).toEqual({ from: 1, to: 20 });
  expect(window.localStorage.getItem(CHART_STATE_STORAGE_KEY)).toContain('candlestick');
});

test('corrupt or incomplete chart state degrades to null', () => {
  window.localStorage.setItem(CHART_STATE_STORAGE_KEY, '{bad json');
  expect(loadChartState()).toBeNull();
  window.localStorage.setItem(CHART_STATE_STORAGE_KEY, JSON.stringify({ timeframe: '1d' }));
  expect(loadChartState()).toBeNull();
});
