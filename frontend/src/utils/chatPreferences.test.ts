import {
  DEFAULT_CHAT_PREFERENCES,
  isDefaultChatPreferences,
  loadChatPreferences,
  resetChatPreferences,
  saveChatPreferences,
} from './chatPreferences';

beforeEach(() => {
  window.localStorage.clear();
});

describe('loadChatPreferences', () => {
  it('returns the defaults when nothing is stored', () => {
    expect(loadChatPreferences()).toEqual(DEFAULT_CHAT_PREFERENCES);
  });

  it('round-trips a saved value', () => {
    saveChatPreferences({ ...DEFAULT_CHAT_PREFERENCES, mode: 'swing_trading', risk_per_trade_percent: 1.5 });
    const loaded = loadChatPreferences();
    expect(loaded.mode).toBe('swing_trading');
    expect(loaded.risk_per_trade_percent).toBe(1.5);
  });

  it('fills in missing fields from a partial/stale-shaped stored record instead of crashing', () => {
    window.localStorage.setItem('marketlens.chat.preferences', JSON.stringify({ mode: 'options' }));
    const loaded = loadChatPreferences();
    expect(loaded.mode).toBe('options');
    expect(loaded.preferred_timeframes).toEqual([]);
    expect(loaded.default_session).toBeNull();
  });

  it('degrades to defaults on corrupted JSON rather than throwing', () => {
    window.localStorage.setItem('marketlens.chat.preferences', '{not valid json');
    expect(loadChatPreferences()).toEqual(DEFAULT_CHAT_PREFERENCES);
  });

  it('degrades to defaults when the stored value is not an object', () => {
    window.localStorage.setItem('marketlens.chat.preferences', '"just a string"');
    expect(loadChatPreferences()).toEqual(DEFAULT_CHAT_PREFERENCES);
  });

  it('coerces a non-array preferred_timeframes to an empty array', () => {
    window.localStorage.setItem('marketlens.chat.preferences', JSON.stringify({ preferred_timeframes: 'not-an-array' }));
    expect(loadChatPreferences().preferred_timeframes).toEqual([]);
  });
});

describe('resetChatPreferences', () => {
  it('clears storage and returns the defaults', () => {
    saveChatPreferences({ ...DEFAULT_CHAT_PREFERENCES, mode: 'day_trading' });
    const reset = resetChatPreferences();
    expect(reset).toEqual(DEFAULT_CHAT_PREFERENCES);
    expect(loadChatPreferences()).toEqual(DEFAULT_CHAT_PREFERENCES);
  });
});

describe('isDefaultChatPreferences', () => {
  it('is true for the defaults', () => {
    expect(isDefaultChatPreferences(DEFAULT_CHAT_PREFERENCES)).toBe(true);
  });

  it('is false once any single field is set', () => {
    expect(isDefaultChatPreferences({ ...DEFAULT_CHAT_PREFERENCES, mode: 'day_trading' })).toBe(false);
    expect(isDefaultChatPreferences({ ...DEFAULT_CHAT_PREFERENCES, preferred_timeframes: ['1h'] })).toBe(false);
    expect(isDefaultChatPreferences({ ...DEFAULT_CHAT_PREFERENCES, risk_per_trade_percent: 1 })).toBe(false);
  });
});
