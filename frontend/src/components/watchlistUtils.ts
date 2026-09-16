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

// ── Display maps ──────────────────────────────────────────────────────────────

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

/** CSS class for a change cell color. */
export function changeCellClass(changePct: number | null | undefined): string {
  if (changePct == null) return '';
  if (changePct > 0) return 'change-up';
  if (changePct < 0) return 'change-down';
  return '';
}

/** Whether a watchlist row's symbol is currently enabled. */
export function isRowEnabled(raw: { is_enabled?: boolean | null }): boolean {
  return raw.is_enabled !== false;
}
