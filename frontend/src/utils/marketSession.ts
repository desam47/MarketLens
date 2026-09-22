import { Bar } from '../services/api';

export type SessionPreference = 'premarket' | 'regular' | 'after_hours' | 'all';

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
