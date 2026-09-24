import { HistoricalSignal } from '../services/api';

export type SignalOutcomeField = 'return_5b' | 'return_10b' | 'return_20b' | 'mfe' | 'mae';

export function isDirectionalSignal(signal: Pick<HistoricalSignal, 'trend_state'>): boolean {
  return signal.trend_state === 'bullish' || signal.trend_state === 'bearish';
}

export function isSignalOutcomeComplete(signal: Pick<HistoricalSignal, SignalOutcomeField>): boolean {
  return signal.return_5b != null
    && signal.return_10b != null
    && signal.return_20b != null
    && signal.mfe != null
    && signal.mae != null;
}

/**
 * Convert raw underlying movement into the outcome of the signal's stated
 * direction. Positive values are favorable; negative values are adverse.
 * Neutral/unknown rows have no directional outcome and return null.
 */
export function directionalOutcome(
  signal: Pick<HistoricalSignal, 'trend_state' | SignalOutcomeField>,
  field: SignalOutcomeField,
): number | null {
  const value = signal[field];
  if (value == null || !isDirectionalSignal(signal)) return null;
  if (signal.trend_state === 'bullish') return value;

  // A bearish call profits from a price decline. Its favorable excursion is
  // the raw low-side MAE, while its adverse excursion is the raw high-side MFE.
  if (field === 'mfe') return signal.mae == null ? null : -signal.mae;
  if (field === 'mae') return signal.mfe == null ? null : -signal.mfe;
  return -value;
}

export function isDirectionalWin(signal: Pick<HistoricalSignal, 'trend_state' | 'return_5b'>): boolean {
  return (signal.trend_state === 'bullish' && (signal.return_5b ?? 0) > 0)
    || (signal.trend_state === 'bearish' && (signal.return_5b ?? 0) < 0);
}
