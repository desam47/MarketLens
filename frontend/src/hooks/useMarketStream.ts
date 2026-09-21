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
  const prevSubsRef = useRef<Set<string>>(new Set());

  // Subscribe/unsubscribe when the subscription list changes.
  useEffect(() => {
    const sub = subRef.current;
    const prev = prevSubsRef.current;
    const next = new Set(subscriptions.map(s => subKey(s.symbol, s.timeframe)));

    for (const key of Array.from(prev)) {
      if (!next.has(key)) {
        const [sym, tf] = key.split(':');
        sub.unsubscribe(sym, tf);
      }
    }

    for (const s of subscriptions) {
      const key = subKey(s.symbol, s.timeframe);
      if (!prev.has(key)) {
        sub.subscribe(s.symbol, s.timeframe);
      }
    }

    prevSubsRef.current = next;

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
