import type { AlertTrigger } from '../services/api';

/** One-shot hand-off from an alert surface to the AI Hub. */
export const PENDING_ALERT_CHAT_KEY = 'marketlens.ai-hub.pending-alert';

export interface PendingAlertChat {
  triggerId: number;
  symbol: string;
}

export function openAlertConversation(trigger: Pick<AlertTrigger, 'id' | 'symbol'>): void {
  const pending: PendingAlertChat = {
    triggerId: trigger.id,
    symbol: trigger.symbol.toUpperCase(),
  };
  try {
    window.sessionStorage.setItem(PENDING_ALERT_CHAT_KEY, JSON.stringify(pending));
  } catch {
    // Navigation still works when storage is blocked; Chat can open without
    // the optional snapshot and the user can ask about the symbol manually.
  }
  window.location.hash = '#ai-hub';
}

export function consumePendingAlertChat(): PendingAlertChat | null {
  try {
    const raw = window.sessionStorage.getItem(PENDING_ALERT_CHAT_KEY);
    if (!raw) return null;
    window.sessionStorage.removeItem(PENDING_ALERT_CHAT_KEY);
    const parsed = JSON.parse(raw) as Partial<PendingAlertChat>;
    const triggerId = parsed.triggerId;
    if (typeof triggerId !== 'number' || !Number.isInteger(triggerId) || typeof parsed.symbol !== 'string' || !parsed.symbol.trim()) {
      return null;
    }
    return { triggerId, symbol: parsed.symbol.trim().toUpperCase() };
  } catch {
    return null;
  }
}
