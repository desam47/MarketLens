/**
 * Chat personal preferences (Version 5, Phase 5.7.3) — the trader's
 * operating mode, timeframes, risk-per-trade limit, primary watchlist,
 * answer detail level, and preferred units.
 *
 * Browser-local only (localStorage), same convention as the Trade
 * Journal and Risk Dashboard — no server table. Sent with each Chat turn
 * (see ChatPanel) so the backend can tailor the deterministic
 * suggested_followups block; never used to change a verified calculation
 * or evidence-derived conclusion (see backend/ai/response_blocks.py's
 * build_response_blocks docstring).
 */
import { ChatPreferences } from '../services/api';

const STORAGE_KEY = 'marketlens.chat.preferences';

export const DEFAULT_CHAT_PREFERENCES: ChatPreferences = {
  mode: null,
  preferred_timeframes: [],
  default_session: null,
  risk_per_trade_percent: null,
  primary_watchlist: null,
  answer_detail_level: null,
  preferred_units: null,
};

export const TRADING_MODES: { value: NonNullable<ChatPreferences['mode']>; label: string }[] = [
  { value: 'day_trading', label: 'Day trading' },
  { value: 'swing_trading', label: 'Swing trading' },
  { value: 'options', label: 'Options' },
  { value: 'long_term_investing', label: 'Long-term investing' },
];

export const PREFERENCE_TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1wk'];

/** Reads stored preferences, filling in any missing/invalid field from
 * the default so a partial or stale-shaped record never crashes a
 * consumer that expects every field to be present. */
export function loadChatPreferences(): ChatPreferences {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_CHAT_PREFERENCES };
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return { ...DEFAULT_CHAT_PREFERENCES };
    return {
      ...DEFAULT_CHAT_PREFERENCES,
      ...parsed,
      preferred_timeframes: Array.isArray(parsed.preferred_timeframes) ? parsed.preferred_timeframes : [],
    };
  } catch {
    return { ...DEFAULT_CHAT_PREFERENCES };
  }
}

export function saveChatPreferences(preferences: ChatPreferences): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    /* best effort — private browsing / storage full / disabled */
  }
}

export function resetChatPreferences(): ChatPreferences {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* best effort */
  }
  return { ...DEFAULT_CHAT_PREFERENCES };
}

/** True when every field is at its default — used to decide whether
 * there's anything worth sending with a turn / showing a "set" badge. */
export function isDefaultChatPreferences(preferences: ChatPreferences): boolean {
  return (
    preferences.mode == null
    && preferences.preferred_timeframes.length === 0
    && preferences.default_session == null
    && preferences.risk_per_trade_percent == null
    && preferences.primary_watchlist == null
    && preferences.answer_detail_level == null
    && preferences.preferred_units == null
  );
}
