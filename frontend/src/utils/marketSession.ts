import { Bar } from '../services/api';

export type SessionPreference = 'premarket' | 'regular' | 'after_hours' | 'all';
export type ExchangeSession = 'premarket' | 'regular' | 'after_hours' | 'closed';

// US/Eastern session boundaries, in minutes-since-midnight. Mirrors
// backend/engines/market_calendar.py's _PREMARKET_OPEN (04:00) /
// _REGULAR_OPEN (09:30) / _REGULAR_CLOSE (16:00) / _AFTER_HOURS_CLOSE (20:00) —
// kept as one named frontend copy (used by every timestamp→session
// classification in the app) instead of being hand-duplicated per
// component, which is what let WatchlistTable and SymbolPage drift into
// three independent copies of the same four numbers.
const PREMARKET_OPEN_MIN = 4 * 60;
const REGULAR_OPEN_MIN = 9 * 60 + 30;
const REGULAR_CLOSE_MIN = 16 * 60;
const AFTER_HOURS_CLOSE_MIN = 20 * 60;

/** Classify a timestamp's US/Eastern time-of-day into an exchange session.
 * Weekday time-of-day only — does not know about weekends/holidays. For
 * "is the market open right now" (which does), use useMarketSession()
 * instead (backend USMarketCalendar, the actual source of truth). */
export function classifySessionFromTimestamp(timestamp: string | null | undefined): ExchangeSession | null {
  if (!timestamp) return null;
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(new Date(timestamp));
  const hour = Number(parts.find(part => part.type === 'hour')?.value ?? 0);
  const minute = Number(parts.find(part => part.type === 'minute')?.value ?? 0);
  const totalMinutes = hour * 60 + minute;
  if (totalMinutes >= PREMARKET_OPEN_MIN && totalMinutes < REGULAR_OPEN_MIN) return 'premarket';
  if (totalMinutes >= REGULAR_OPEN_MIN && totalMinutes < REGULAR_CLOSE_MIN) return 'regular';
  if (totalMinutes >= REGULAR_CLOSE_MIN && totalMinutes < AFTER_HOURS_CLOSE_MIN) return 'after_hours';
  return 'closed';
}

export const SESSION_PREFERENCE_KEY = 'marketlens.market-session-preference';

export function readSessionPreference(): SessionPreference {
  if (typeof window === 'undefined') return 'all';
  const value = window.localStorage.getItem(SESSION_PREFERENCE_KEY);
  return value === 'premarket' || value === 'regular' || value === 'after_hours' || value === 'all' ? value : 'all';
}

export function sessionMatchesPreference(bar: Pick<Bar, 'session'>, preference: SessionPreference): boolean {
  if (preference === 'all') return true;
  if (preference === 'regular') return bar.session === 'regular' || !bar.session;
  return bar.session === preference;
}
