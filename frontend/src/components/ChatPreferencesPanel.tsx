/**
 * Chat personal preferences panel (Version 5, Phase 5.7.3) — view, edit,
 * and reset the trader's operating mode, timeframes, risk-per-trade
 * limit, primary watchlist, answer detail level, and preferred units.
 *
 * Every field is optional and visible here — nothing is inferred or set
 * silently (the plan's own constraint: "do not infer high-impact risk
 * settings silently"). See utils/chatPreferences.ts for storage and
 * ChatPanel.tsx for how these are sent with each turn.
 */
import React from 'react';
import { ChatPreferences } from '../services/api';
import { PREFERENCE_TIMEFRAMES, TRADING_MODES, isDefaultChatPreferences } from '../utils/chatPreferences';

interface ChatPreferencesPanelProps {
  preferences: ChatPreferences;
  onChange: (next: ChatPreferences) => void;
  onReset: () => void;
  onClose: () => void;
}

export function ChatPreferencesPanel({ preferences, onChange, onReset, onClose }: ChatPreferencesPanelProps) {
  const toggleTimeframe = (tf: string) => {
    const next = preferences.preferred_timeframes.includes(tf)
      ? preferences.preferred_timeframes.filter(t => t !== tf)
      : [...preferences.preferred_timeframes, tf];
    onChange({ ...preferences, preferred_timeframes: next });
  };

  return (
    <section className="chat-preferences-panel" aria-label="Chat preferences">
      <div className="chat-preferences-heading">
        <span>Preferences</span>
        <button type="button" className="chat-quick-action-btn" onClick={onClose}>✕</button>
      </div>
      <p className="info-text">
        Tailors terminology and suggested follow-ups only — never changes a verified
        calculation or the underlying data.
      </p>

      <label className="chat-pref-field">
        <span>Mode</span>
        <select
          value={preferences.mode ?? ''}
          onChange={e => onChange({ ...preferences, mode: (e.target.value || null) as ChatPreferences['mode'] })}
          aria-label="Trading mode"
        >
          <option value="">Not set</option>
          {TRADING_MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </label>

      <fieldset className="chat-pref-field">
        <legend>Preferred timeframes</legend>
        <div className="chat-pref-timeframes">
          {PREFERENCE_TIMEFRAMES.map(tf => (
            <label key={tf} className="chat-pref-timeframe-chip">
              <input
                type="checkbox"
                checked={preferences.preferred_timeframes.includes(tf)}
                onChange={() => toggleTimeframe(tf)}
              />
              {tf}
            </label>
          ))}
        </div>
      </fieldset>

      <label className="chat-pref-field">
        <span>Default session</span>
        <select
          value={preferences.default_session ?? ''}
          onChange={e => onChange({ ...preferences, default_session: (e.target.value || null) as ChatPreferences['default_session'] })}
          aria-label="Default session"
        >
          <option value="">Not set</option>
          <option value="premarket">Premarket</option>
          <option value="regular">Regular</option>
          <option value="after_hours">After-hours</option>
          <option value="auto">Auto (current session)</option>
        </select>
      </label>

      <label className="chat-pref-field">
        <span>Risk per trade (%)</span>
        <input
          type="number"
          min={0}
          max={100}
          step={0.1}
          value={preferences.risk_per_trade_percent ?? ''}
          onChange={e => {
            const value = e.target.value;
            onChange({ ...preferences, risk_per_trade_percent: value === '' ? null : Number(value) });
          }}
          aria-label="Risk per trade percent"
        />
      </label>

      <label className="chat-pref-field">
        <span>Primary watchlist</span>
        <input
          type="text"
          value={preferences.primary_watchlist ?? ''}
          onChange={e => onChange({ ...preferences, primary_watchlist: e.target.value || null })}
          aria-label="Primary watchlist name"
          maxLength={120}
        />
      </label>

      <label className="chat-pref-field">
        <span>Answer detail</span>
        <select
          value={preferences.answer_detail_level ?? ''}
          onChange={e => onChange({ ...preferences, answer_detail_level: (e.target.value || null) as ChatPreferences['answer_detail_level'] })}
          aria-label="Answer detail level"
        >
          <option value="">Not set</option>
          <option value="concise">Concise</option>
          <option value="standard">Standard</option>
          <option value="detailed">Detailed</option>
        </select>
      </label>

      <label className="chat-pref-field">
        <span>Preferred units</span>
        <select
          value={preferences.preferred_units ?? ''}
          onChange={e => onChange({ ...preferences, preferred_units: (e.target.value || null) as ChatPreferences['preferred_units'] })}
          aria-label="Preferred units"
        >
          <option value="">Not set</option>
          <option value="percent">Percent</option>
          <option value="dollars">Dollars</option>
        </select>
      </label>

      <button
        type="button"
        className="chat-quick-action-btn"
        onClick={onReset}
        disabled={isDefaultChatPreferences(preferences)}
      >
        Reset to defaults
      </button>
    </section>
  );
}

export default ChatPreferencesPanel;
