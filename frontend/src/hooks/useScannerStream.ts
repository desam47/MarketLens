/**
 * useScannerStream — React hook for the scanner WebSocket.
 *
 * Manages a ScannerSubscriber for a list of symbols and exposes:
 *   - liveResults: Record<symbol, ScanResult>   — latest scan per symbol
 *   - connectionStatus: 'connecting' | 'open' | 'closed'
 *   - errors: Record<symbol, string>            — last scan_error per symbol
 *
 * Usage:
 *   const { liveResults, connectionStatus, errors } = useScannerStream(['AAPL', 'TSLA']);
 *   // Component re-renders only when a symbol's result changes.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import api, { ScanResult, ScannerEvent, ScannerSubscriber } from '../services/api';

export type ConnectionStatus = 'connecting' | 'open' | 'closed';

export interface UseScannerStreamOptions {
  /** Symbols to subscribe to. Empty array = no subscriptions. */
  symbols: string[];
  /**
   * Called with the full per-symbol map whenever any subscribed symbol updates.
   * Use for side-effects (sound, notification) — the hook also returns
   * ``liveResults`` for direct render use.
   */
  onUpdate?: (results: Record<string, ScanResult>) => void;
  /** Called when a scan_error message arrives for any symbol. */
  onError?: (symbol: string, error: string) => void;
}

export function useScannerStream({
  symbols,
  onUpdate,
  onError,
}: UseScannerStreamOptions) {
  const [liveResults, setLiveResults] = useState<Record<string, ScanResult>>({});
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('closed');
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Keep a ref to the subscriber so we can disconnect without tearing down
  // the component's render state. Created lazily rather than passed as the
  // useRef argument: an argument expression is evaluated on every render, so
  // that form allocated a throwaway, never-connected subscriber each time.
  const subRef = useRef<ScannerSubscriber | null>(null);
  if (subRef.current === null) {
    subRef.current = api.createScannerSubscriber();
  }
  const sub = subRef.current;

  // Track which symbols are "wanted" so we can diff on symbols change.
  const prevSymbolsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const prevSymbols = prevSymbolsRef.current;

    // Unsubscribe from symbols that are no longer wanted.
    for (const sym of Array.from(prevSymbols)) {
      if (!symbols.includes(sym)) {
        sub.unsubscribe(sym);
      }
    }

    // Subscribe to newly wanted symbols.
    for (const sym of symbols) {
      if (!prevSymbols.has(sym)) {
        sub.subscribe(sym);
      }
    }

    prevSymbolsRef.current = new Set(symbols);

    // If nothing is wanted, disconnect entirely.
    if (symbols.length === 0) {
      sub.disconnect();
    } else {
      // Connect the socket if it's not already open. Reset explicitlyClosed
      // in case the component re-mounted after an unmount-disconnect, so the
      // socket can reconnect without needing a manual refresh().
      (sub as any).explicitlyClosed = false;
      sub.connect();
    }

    // Deliberately no cleanup here. Removals are handled by the diff above,
    // and unmounting disconnects the socket in the effect below. An earlier
    // version unsubscribed every symbol in this cleanup, which permanently
    // killed live updates for any symbol present in BOTH the outgoing and
    // incoming lists: ``unsubscribe`` deletes from the subscriber's Set, so
    // the next diff classified that symbol as "already wanted" and never
    // re-subscribed it (found live 2026-09-13 — switching between two
    // watchlists that share a symbol stopped that symbol's pushes until a
    // full reload, because ``connect``'s onopen only replays the Set).
  }, [symbols]);

  // Wire the event listener (stable callback ref pattern).
  useEffect(() => {
    const handleEvent = (evt: ScannerEvent) => {
      if (evt.type === 'scan_result') {
        setLiveResults((prev) => {
          const next = { ...prev, [evt.symbol]: evt.data };
          onUpdate?.(next);
          return next;
        });
        // Clear any previous error for this symbol on a new result.
        setErrors((prev) => {
          if (evt.symbol in prev) {
            const next = { ...prev };
            delete next[evt.symbol];
            return next;
          }
          return prev;
        });
      } else if (evt.type === 'scan_error') {
        setErrors((prev) => ({ ...prev, [evt.symbol]: evt.error }));
        onError?.(evt.symbol, evt.error);
      }
    };

    const unsubscribe = sub.onEvent(handleEvent);
    const unsubStatus = sub.onStatus(setConnectionStatus);

    // No extra status sync needed: onStatus() replays the subscriber's
    // current status to the listener immediately, so the registration above
    // already covers the "socket opened before this effect ran" case.

    return () => {
      unsubStatus();
      unsubscribe();
    };
  }, [onUpdate, onError]);

  // Cleanup on unmount — disconnect the socket. Capture the ref locally
  // so the cleanup reads the same instance React mounted with, not a later
  // (potentially remounted) one. The eslint disable is the standard
  // pattern for unmount-only effects.
  useEffect(() => {
    return () => {
      sub.disconnect();
    };
  }, []);

  /**
   * Manual refresh: disconnect, wipe live results, and reconnect so the
   * subscriber re-subscribes on open. Useful after the backend restarts.
   */
  const refresh = useCallback(() => {
    sub.disconnect();
    setLiveResults({});
    setErrors({});
    // Reset the "explicitly closed" flag so connect() actually opens the socket.
    // The subscriber instance is reused across mounts so this state persists.
    (sub as any).explicitlyClosed = false;
    sub.connect();
  }, []);

  return { liveResults, connectionStatus, errors, refresh };
}
