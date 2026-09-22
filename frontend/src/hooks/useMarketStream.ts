/**
 * useMarketStream — React hook for the realtime bar WebSocket.
 *
 * Manages a RealtimeSubscriber for a list of symbol+timeframe subscriptions
 * and exposes:
 *   - latestBars: Record<"SYMBOL:TF", BarUpdateData> — latest bar per sub
 *   - connectionStatus: 'connecting' | 'open' | 'closed'
 *   - errors: Record<"SYMBOL:TF", string>               — last error per key
 *
 * Usage:
 *   const sub = { symbol: 'SPY', timeframe: '1m' };
 *   const { latestBars, connectionStatus } = useMarketStream([sub]);
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import api, { BarUpdateData, RealtimeConnectionStatus, RealtimeEvent } from '../services/api';

export type ConnectionStatus = RealtimeConnectionStatus;

export interface MarketSub {
  symbol: string;
  timeframe: string;
}

/** "SYMBOL:TF" subscription key. */
const subKey = (s: string, tf: string) => `${s.toUpperCase()}:${tf.toLowerCase()}`;

export interface UseMarketStreamOptions {
  /** Subscriptions to maintain. Empty array = disconnect entirely. */
  subscriptions: MarketSub[];
  /**
   * Called with the full per-key map whenever any subscription updates.
   */
  onUpdate?: (bars: Record<string, BarUpdateData>) => void;
  /** Called when an error message arrives for any key. */
  onError?: (key: string, message: string) => void;
}

export function useMarketStream({
  subscriptions,
  onUpdate,
  onError,
}: UseMarketStreamOptions) {
  const [latestBars, setLatestBars] = useState<Record<string, BarUpdateData>>({});
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('closed');
  const [errors, setErrors] = useState<Record<string, string>>({});

  const subRef = useRef(api.createRealtimeSubscriber());

  // Subscribe/unsubscribe when the subscription list changes.
  //
  // Every run subscribes to the full current list and cleanup unsubscribes
  // exactly that — no cross-run diffing. subscribe()/unsubscribe() on
  // RealtimeSubscriber are Set-based and idempotent, so resubscribing to a
  // symbol+timeframe pair that's already active is a harmless no-op; this
  // trades a few redundant WS messages on a real membership change for
  // correctness. (A prior version tracked prev-vs-next subscriptions in a
  // ref to only send the delta, but its cleanup unconditionally
  // unsubscribed everything without updating that ref — so after any
  // cleanup+re-run with the same subscriptions (e.g. React 18 StrictMode's
  // dev-only mount→cleanup→mount, or any parent that doesn't memoize the
  // subscriptions array) the diff believed those keys were still
  // subscribed and never resubscribed them, silently dropping live
  // updates. This mirrors the always-resubscribe pattern already used
  // safely elsewhere for the same subscriber, e.g. useLiveQuotes.ts.)
  useEffect(() => {
    const sub = subRef.current;

    for (const s of subscriptions) {
      sub.subscribe(s.symbol, s.timeframe);
    }

    if (subscriptions.length === 0) {
      sub.disconnect();
    }

    return () => {
      // Unsubscribe on cleanup (but don't disconnect — the subscriber may
      // be reused on re-mount with the same subscriptions).
      for (const s of subscriptions) {
        sub.unsubscribe(s.symbol, s.timeframe);
      }
    };
  }, [subscriptions]);

  // Wire event + status listeners.
  useEffect(() => {
    const sub = subRef.current;

    const handleEvent = (evt: RealtimeEvent) => {
      if (evt.type === 'bar_update') {
        const key = subKey(evt.symbol, evt.timeframe);
        setLatestBars(prev => {
          const next = { ...prev, [key]: evt.data };
          onUpdate?.(next);
          return next;
        });
        // Clear previous error for this key on a new bar.
        setErrors(prev => {
          if (key in prev) {
            const next = { ...prev };
            delete next[key];
            return next;
          }
          return prev;
        });
      } else if (evt.type === 'error') {
        // Broadcast error to all active subscriptions.
        setErrors(prev => ({ ...prev, _global: evt.message }));
        onError?.('_global', evt.message);
      }
    };

    const unsubEvent = sub.onEvent(handleEvent);
    const unsubStatus = sub.onStatus(setConnectionStatus);

    return () => {
      unsubStatus();
      unsubEvent();
    };
  }, [onUpdate, onError]);

  // Disconnect on unmount.
  useEffect(() => {
    const sub = subRef.current;
    return () => {
      sub.disconnect();
    };
  }, []);

  /**
   * Manual reconnect after backend restart.
   */
  const refresh = useCallback(() => {
    subRef.current.disconnect();
    setLatestBars({});
    setErrors({});
    subRef.current.connect();
  }, []);

  return { latestBars, connectionStatus, errors, refresh };
}
