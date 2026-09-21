import { useEffect, useRef, useState } from 'react';
import api, { LiveQuoteUpdateData, RealtimeConnectionStatus, RealtimeEvent, RealtimeSubscriber } from '../services/api';

/** Subscribe to one shared realtime socket for the supplied symbol set. */
export function useLiveQuotes(symbols: string[]) {
  const [quotes, setQuotes] = useState<Record<string, LiveQuoteUpdateData>>({});
  const [status, setStatus] = useState<RealtimeConnectionStatus>('closed');
  const subscriberRef = useRef<RealtimeSubscriber | null>(null);
  const normalized = Array.from(new Set(symbols.map(s => s.trim().toUpperCase()).filter(Boolean))).sort();
  const key = normalized.join(',');

  useEffect(() => {
    // Some isolated component tests provide a reduced API mock. Treat the
    // realtime channel as optional there; production ApiService always has it.
    const subscriber = subscriberRef.current || api.createRealtimeSubscriber?.();
    if (!subscriber) return;
    subscriberRef.current = subscriber;
    const wanted = new Set(normalized);
    const removeEvent = subscriber.onEvent((event: RealtimeEvent) => {
      if (event.type !== 'quote_update' || !wanted.has(event.symbol)) return;
      setQuotes(previous => ({ ...previous, [event.symbol]: event.data }));
    });
    const removeStatus = subscriber.onStatus(setStatus);
    normalized.forEach(symbol => subscriber.subscribeQuote(symbol));
    return () => {
      removeEvent();
      removeStatus();
      normalized.forEach(symbol => subscriber.unsubscribeQuote(symbol));
    };
  // `key` is the stable representation of the normalized symbol list.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => () => subscriberRef.current?.disconnect(), []);

  return { quotes, status };
}
