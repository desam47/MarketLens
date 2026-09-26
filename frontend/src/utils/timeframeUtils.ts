/** All supported chart timeframes, in descending order of granularity. */
export const TIMEFRAMES = [
  '1m', '2m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '1wk',
] as const;
export type Timeframe = typeof TIMEFRAMES[number];

/** Timeframes that carry recorded signals — use this, not TIMEFRAMES, for any
 * selector that filters signals, signal research or signal replay.
 *
 * Weekly is excluded: the backend records no weekly signal (the trend engine
 * never clears indicator warm-up on the ~156 weekly bars three years of
 * history yields, so every score is null). Weekly *bars* are still stored and
 * charted, which is why TIMEFRAMES keeps it. */
export const SIGNAL_TIMEFRAMES = TIMEFRAMES.filter(tf => tf !== '1wk');

/** Default timeframe for every timeframe selector (chart interval the
 * symbol-scoped AI + analysis views open on). */
export const DEFAULT_TIMEFRAME: Timeframe = '1d';

/** Human-readable labels for timeframe selectors. */
export const TIMEFRAME_LABELS: Record<string, string> = {
  '1m':  '1 Min',
  '2m':  '2 Min',
  '3m':  '3 Min',
  '5m':  '5 Min',
  '15m': '15 Min',
  '30m': '30 Min',
  '1h':  '1 Hour',
  '4h':  '4 Hour',
  '1d':  'Daily',
  '1wk': 'Weekly',
};

/** Default timeframes shown in the multi-chart grid. */
export const DEFAULT_GRID_TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'];
