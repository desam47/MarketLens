/**
 * Shared utilities for the watchlist table components.
 *
 * Extracted from WatchlistTable.tsx so the pure functions are testable in
 * isolation and reusable if other parts of the app need the same logic.
 */
import type { RelativeStrengthSignal } from '../services/api';

// ── Formatting ────────────────────────────────────────────────────────────────

/** Format a number for display, returning '—' for null/undefined. */
export function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return '—';
  return trimZeros(n.toFixed(decimals));
}

/** Strip unnecessary trailing zeros from a fixed-point string (e.g. "0.2900" → "0.29"). */
function trimZeros(s: string): string {
  if (s.includes('.')) {
    s = s.replace(/0+$/, '');   // trailing zeros
    s = s.replace(/\.$/, '');   // lone decimal point
  }
  return s;
}

/** Format a price with up to 4 significant decimals, no trailing zeros. */
export function fmtPrice(p: number | null | undefined): string {
  if (p == null) return '—';
  return trimZeros(p.toFixed(4));
}

// ── Signal → trend direction ─────────────────────────────────────────────────

const BULLISH_SIGNALS = new Set([
  'daily_bullish', 'mtf_bullish', 'breakout', 'strong_trend',
  'trend_strengthens', 'full_alignment', 'bullish_divergence',
  'trend_crosses_above_70',
]);

const BEARISH_SIGNALS = new Set([
  'daily_bearish', 'mtf_bearish', 'breakdown', 'weak_trend',
  'trend_weakens', 'timeframe_conflict', 'bearish_divergence',
  'trend_crosses_below_70',
]);

/** Derive a trend direction from a list of signal names. */
export function deriveDirection(signals: string[]): 'bullish' | 'bearish' | 'neutral' {
  if (signals.length === 0) return 'neutral';
  let bullCount = 0;
  let bearCount = 0;
  for (const s of signals) {
    if (BULLISH_SIGNALS.has(s)) bullCount++;
    else if (BEARISH_SIGNALS.has(s)) bearCount++;
  }
  if (bullCount > bearCount) return 'bullish';
  if (bearCount > bullCount) return 'bearish';
  return 'neutral';
}

// ── Confidence estimation ─────────────────────────────────────────────────────

/** Estimate confidence (0–100) from the magnitude of a score. */
export function estimateConfidence(score: number): number {
  const abs = Math.abs(score);
  if (abs >= 70) return 90;
  if (abs >= 50) return 75;
  if (abs >= 30) return 60;
  if (abs >= 15) return 45;
  return 30;
}

// ── Display maps ──────────────────────────────────────────────────────────────

export const TREND_ICONS: Record<string, string> = {
  bullish: '🐂',
  bearish: '🐻',
  neutral: '➡',
};

export const TREND_LABELS: Record<string, string> = {
  bullish: 'Uptrend',
  bearish: 'Downtrend',
  neutral: 'Neutral',
};

export const RS_CLASS_LABELS: Record<string, string> = {
  strong_outperformer: 'Strong Outperformer',
  outperformer: 'Outperformer',
  inline: 'Inline',
  underperformer: 'Underperformer',
  strong_underperformer: 'Strong Underperformer',
  unknown: '—',
};

/** CSS class for relative strength cell (used for color coding). */
export function rsCellClass(rs: RelativeStrengthSignal | null): string {
  if (!rs) return '';
  const c = rs.classification ?? 'unknown';
  if (c === 'strong_outperformer' || c === 'outperformer') return 'rs-bullish';
  if (c === 'strong_underperformer' || c === 'underperformer') return 'rs-bearish';
  return '';
}

/** Display label for a relative strength signal. */
export function rsCellLabel(rs: RelativeStrengthSignal | null): string {
  if (!rs) return '—';
  return `${fmt(rs.rs_pct)}% ${RS_CLASS_LABELS[rs.classification] ?? ''}`;
}

// ── Row-level helpers ────────────────────────────────────────────────────────

/** CSS class for the price cell color. */
export function priceCellClass(changePct: number | null | undefined): string {
  if (changePct == null) return '';
  if (changePct > 0) return 'price-up';
  if (changePct < 0) return 'price-down';
  return '';
}

/** Whether a watchlist row's symbol is currently enabled. */
export function isRowEnabled(raw: { is_enabled?: boolean | null }): boolean {
  return raw.is_enabled !== false;
}
