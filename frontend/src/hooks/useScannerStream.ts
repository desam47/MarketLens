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
import api, { ScanResult, ScannerEvent } from '../services/api';

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
  // the component's render state.
  const subRef = useRef(api.createScannerSubscriber());

  // Track which symbols are "wanted" so we can diff on symbols change.
  const prevSymbolsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const sub = subRef.current;
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

    return () => {
      // Only unsubscribe — don't disconnect the socket. The subscriber may
      // be reused if the parent re-mounts with the same symbols.
      for (const sym of symbols) {
        sub.unsubscribe(sym);
      }
    };
  }, [symbols]);

  // Wire the event listener (stable callback ref pattern).
  useEffect(() => {
    const sub = subRef.current;

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

    // The subscriber may already be open (e.g. if symbols were set before
    // this effect ran). Sync the current status.
    sub.onStatus(setConnectionStatus);

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
    const sub = subRef.current;
    return () => {
      sub.disconnect();
    };
  }, []);

  /**
   * Manual refresh: disconnect, wipe live results, and reconnect so the
   * subscriber re-subscribes on open. Useful after the backend restarts.
   */
  const refresh = useCallback(() => {
    subRef.current.disconnect();
    setLiveResults({});
    setErrors({});
    // Reset the "explicitly closed" flag so connect() actually opens the socket.
    // The subscriber instance is reused across mounts so this state persists.
    (subRef.current as any).explicitlyClosed = false;
    subRef.current.connect();
  }, []);

  return { liveResults, connectionStatus, errors, refresh };
}
