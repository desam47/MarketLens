import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { FixedSizeList, ListChildComponentProps } from 'react-window';
import api, {
  LiveQuoteUpdateData,
  RealtimeConnectionStatus,
  RealtimeEvent,
  CalendarEvent,
  WatchlistScanResult,
  WatchlistIntelligence as WatchlistIntelligenceData,
  WatchlistSessionPrice,
  RelativeStrengthData,
  RelativeStrengthSignal,
  MarketSessionType,
} from '../services/api';
import { formatETTime } from './chartMath';
import { clearSymbolCatalogCache } from './SymbolAutocompleteInput';
import { MarketDataFreshnessBadge } from './MarketDataFreshnessBadge';
import { useMarketSession } from '../hooks/useMarketSession';
import { classifySessionFromTimestamp } from '../utils/marketSession';
import {
  fmt,
  fmtPrice,
  changeCellClass,
  rsCellClass,
  rsCellLabel,
  isRowEnabled,
} from './watchlistUtils';
import {
  WATCHLIST_COLUMNS,
  getVisibleColumns,
  buildVirtGridTemplateColumns,
} from './watchlistColumns';
import { WatchlistIntelligence } from './WatchlistIntelligence';

const TREND_TFS = [
  { key: 'ONE_MINUTE', short: '1m' },
  { key: 'FIVE_MINUTE', short: '5m' },
  { key: 'FIFTEEN_MINUTE', short: '15m' },
  { key: 'ONE_HOUR', short: '1h' },
  { key: 'FOUR_HOUR', short: '4h' },
  { key: 'ONE_DAY', short: '1d' },
];
const MARKET_SESSIONS = [
  { key: 'premarket', label: 'Premarket' },
  { key: 'regular', label: 'Regular' },
  { key: 'after_hours', label: 'After-hours' },
] as const;

function tradingDateFromTimestamp(timestamp: string | null): string | null {
  if (!timestamp) return null;
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date(timestamp));
  const year = parts.find(part => part.type === 'year')?.value;
  const month = parts.find(part => part.type === 'month')?.value;
  const day = parts.find(part => part.type === 'day')?.value;
  return year && month && day ? `${year}-${month}-${day}` : null;
}

function trendBadge(direction: string | undefined): { className: string; arrow: string; title: string } {
  const arrow = direction === 'uptrend' ? '▲'
             : direction === 'downtrend' ? '▼'
             : '◆';
  const cls = direction === 'uptrend'   ? 'trend-badge trend-up'
            : direction === 'downtrend' ? 'trend-badge trend-down'
            : 'trend-badge trend-neutral';
  return { className: cls, arrow, title: direction ?? 'unknown' };
}

function TrendColumn({ trendSignals }: { trendSignals: Record<string, any> }) {
  return (
    <div className="td-trend">
      {TREND_TFS.map(({ key, short }) => {
        const sig = trendSignals?.[key];
        const b = trendBadge(sig?.direction);
        return (
          <span
            key={key}
            className={b.className}
            title={sig ? `${short}: ${b.title} (conf ${(sig.confidence * 100).toFixed(0)}%)` : `${short}: no data`}
          >
            <span className="trend-arrow">{b.arrow}</span>
            <span className="trend-label">{short}</span>
          </span>
        );
      })}
    </div>
  );
}

interface WatchlistTableProps {
  watchlistId: number;
  onSelectSymbol: (symbol: string) => void;
  sortColumn?: 'symbol' | 'price' | 'change' | 'rs';
  sortDirection?: 'asc' | 'desc';
}

interface RowData {
  symbol: string;
  entityType: 'stock' | 'etf' | null;
  price: number | null;
  change: number | null;
  changePct: number | null;
  rs: RelativeStrengthSignal | null;
  // All benchmarks' signals; `rs` is picked from these for the selected benchmark.
  rsSignals: RelativeStrengthSignal[];
  trendSignals: Record<string, any>;
  liveQuote?: LiveQuoteUpdateData;
  sessionSnapshot?: WatchlistSessionPrice;
  raw: WatchlistScanResult;
}

function RowFreshness({ row, connectionStatus, marketSession }: { row: RowData; connectionStatus: RealtimeConnectionStatus; marketSession?: MarketSessionType | null }) {
  const live = row.liveQuote;
  const quote = row.raw.quote;
  return (
    <MarketDataFreshnessBadge
      dataStatus={live ? 'LIVE' : quote?.data_status}
      timestamp={live?.timestamp ?? quote?.timestamp ?? row.raw.timestamp}
      showAge
      connectionStatus={connectionStatus}
      provider={live?.provider ?? quote?.provider}
      marketSession={marketSession}
    />
  );
}

// Threshold: above this row count, switch to a virtualized list. Below, a
// regular table renders more cleanly (sticky header, full column widths).
const VIRT_THRESHOLD = 30;
const VIRT_ROW_HEIGHT = 56;
const VIRT_HEIGHT = 480;

// Trend is the widest column; below this width it is dropped so the rest of
// the table stays usable. Done in JS (not CSS display:none) so the virtualized
// grid template and the header/cells stay in sync.
const NARROW_QUERY = '(max-width: 1100px)';

function useMediaQuery(query: string): boolean {
  const supported = typeof window !== 'undefined' && typeof window.matchMedia === 'function';
  const [matches, setMatches] = useState(() => supported && window.matchMedia(query).matches);
  useEffect(() => {
    if (!supported) return;
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query, supported]);
  return matches;
}

const SortIcon = React.memo(function SortIcon({ column, sortCol, sortDir }: {
  column: string;
  sortCol: string;
  sortDir: 'asc' | 'desc';
}) {
  if (sortCol !== column) return <span className="sort-icon">⇅</span>;
  return <span className="sort-icon sort-active">{sortDir === 'asc' ? '↑' : '↓'}</span>;
});

// Row renderer for the virtualized list. Mirrors WatchlistRow's column
// layout but uses absolute positioning (react-window handles it) and
// CSS grid instead of <tr>/<td> so columns align with the header.
const VirtualizedRow = React.memo(function VirtualizedRow({
  index,
  style,
  data,
}: ListChildComponentProps<{
  rows: RowData[];
  onSelectSymbol: (symbol: string) => void;
  onToggleSymbol: (symbol: string) => void;
  onDeleteSymbol: (symbol: string) => void;
  togglingSymbol: string | null;
  pendingSymbol: string | null;
  pendingAction: 'delete' | null;
  visibleColKeys: Set<string>;
  gridTemplate: string;
  quoteConnectionStatus: RealtimeConnectionStatus;
  marketSession: MarketSessionType | null;
}>) {
  const { rows, onSelectSymbol, onToggleSymbol, onDeleteSymbol,
    togglingSymbol, pendingSymbol, pendingAction, visibleColKeys, gridTemplate, quoteConnectionStatus, marketSession } = data;
  const row = rows[index];

  const isPending = pendingSymbol === row.symbol;
  const isDeleting = isPending && pendingAction === 'delete';
  const isToggling = togglingSymbol === row.symbol;
  const rowEnabled = isRowEnabled(row.raw);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Ignore keys bubbling up from the row's buttons (toggle/delete).
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelectSymbol(row.symbol);
    }
  };

  return (
    <div
      style={{ ...style, gridTemplateColumns: gridTemplate }}
      className={`virt-row watchlist-table-row${rowEnabled ? '' : ' row-disabled'}`}
      onClick={() => onSelectSymbol(row.symbol)}
      onKeyDown={handleKeyDown}
      tabIndex={0}
    >
      {visibleColKeys.has('symbol') && (
        <div className="virt-cell td-symbol">
          {row.symbol}
          {!rowEnabled && <span className="row-disabled-badge" title="Disabled">⏸</span>}
        </div>
      )}
      {visibleColKeys.has('type') && (
        <div className="virt-cell td-type">
          {row.entityType === 'etf' ? (
            <span className="entity-tag entity-tag-etf">ETF</span>
          ) : (
            <span className="entity-tag entity-tag-stock">Stock</span>
          )}
        </div>
      )}
      {visibleColKeys.has('price') && (
        <div className="virt-cell td-price" title={row.sessionSnapshot ? `${row.sessionSnapshot.session.replace('_', ' ')} · ${row.sessionSnapshot.trading_date}` : undefined}>
          {row.price != null ? `$${fmtPrice(row.price)}` : '—'}
        </div>
      )}
      {visibleColKeys.has('change') && (
        <div className={`virt-cell td-change ${changeCellClass(row.changePct)}`} title={row.sessionSnapshot?.baseline_label ?? undefined}>
          {row.changePct != null
            ? `${row.changePct > 0 ? '+' : ''}${fmt(row.changePct)}%`
            : '—'}
        </div>
      )}
      {visibleColKeys.has('freshness') && (
        <div className="virt-cell td-freshness"><RowFreshness row={row} connectionStatus={quoteConnectionStatus} marketSession={marketSession} /></div>
      )}
      {visibleColKeys.has('trend') && (
        <div className="virt-cell td-trend"><TrendColumn trendSignals={row.trendSignals} /></div>
      )}
      {visibleColKeys.has('rs') && (
        <div className={`virt-cell td-rs ${rsCellClass(row.rs)}`}>{rsCellLabel(row.rs)}</div>
      )}
      {visibleColKeys.has('actions') && (
        <div
          className="virt-cell td-actions"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="actions-inner">
            <button
              className={`row-action-btn${rowEnabled ? ' row-action-toggle-active' : ''}`}
              title={rowEnabled ? 'Disable symbol' : 'Enable symbol'}
              disabled={isToggling}
              onClick={() => onToggleSymbol(row.symbol)}
            >
              {isToggling ? '…' : rowEnabled ? '⏸' : '▶'}
            </button>
            <button
              className="row-action-btn row-action-danger"
              title="Remove from watchlist"
              disabled={isDeleting}
              onClick={() => onDeleteSymbol(row.symbol)}
            >
              {isDeleting ? '…' : '×'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
});

export function WatchlistTable({
  watchlistId,
  onSelectSymbol,
  sortColumn = 'symbol',
  sortDirection = 'asc',
}: WatchlistTableProps) {
  const [rows, setRows] = useState<RowData[]>([]);
  const [scanTimestamp, setScanTimestamp] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const marketSessionInfo = useMarketSession();
  const marketSession = marketSessionInfo?.session ?? null;
  const [selectedSessions, setSelectedSessions] = useState<Set<string>>(
    () => new Set(MARKET_SESSIONS.map(session => session.key)),
  );
  const [sessionPrices, setSessionPrices] = useState<Record<string, WatchlistSessionPrice>>({});
  const [missingSessionSymbols, setMissingSessionSymbols] = useState<string[]>([]);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [intelligence, setIntelligence] = useState<WatchlistIntelligenceData | null>(null);
  const [intelligenceEvents, setIntelligenceEvents] = useState<CalendarEvent[]>([]);
  const [intelligenceLoading, setIntelligenceLoading] = useState(false);
  const [intelligenceError, setIntelligenceError] = useState<string | null>(null);
  const sessionRequestIdRef = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [sortCol, setSortCol] = useState(sortColumn);
  const [sortDir, setSortDir] = useState(sortDirection);
  const [pendingSymbol, setPendingSymbol] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<'delete' | null>(null);
  const [togglingSymbol, setTogglingSymbol] = useState<string | null>(null);
  const [rsBenchmark, setRsBenchmark] = useState('SPY');
  const [showColToggle, setShowColToggle] = useState(false);
  const [visibleColKeys, setVisibleColKeys] = useState<Set<string>>(() => {
    return new Set(WATCHLIST_COLUMNS.map(c => c.key));
  });
  const listRef = useRef<FixedSizeList>(null);

  const isNarrow = useMediaQuery(NARROW_QUERY);
  const effectiveColKeys = useMemo(() => {
    if (!isNarrow) return visibleColKeys;
    const next = new Set(visibleColKeys);
    next.delete('trend');
    return next;
  }, [visibleColKeys, isNarrow]);
  const shownColumns = useMemo(
    () => getVisibleColumns().filter(c => effectiveColKeys.has(c.key)),
    [effectiveColKeys],
  );
  const virtGridTemplate = useMemo(
    () => buildVirtGridTemplateColumns(effectiveColKeys),
    [effectiveColKeys],
  );

  const colToggleRef = useRef<HTMLDivElement>(null);
  const sessionSelectionKey = useMemo(
    () => Array.from(selectedSessions).sort().join(','),
    [selectedSessions],
  );
  // Only "all 3 checked" is the unfiltered default view. 0 checked must NOT
  // be treated the same as "show everything" — that reads as a no-op to a
  // user trying to filter down to nothing. It instead falls through to the
  // filtered path below with an always-empty session set, which correctly
  // shows no price for any row (see the `!snapshot` branch in displayRows).
  const combinedSessionView = selectedSessions.size === MARKET_SESSIONS.length;
  const noSessionsSelected = selectedSessions.size === 0;
  const intelligenceSessionScope = useMemo(
    () => noSessionsSelected ? 'none' : combinedSessionView ? 'all' : sessionSelectionKey,
    [combinedSessionView, noSessionsSelected, sessionSelectionKey],
  );

  const liveSymbols = useMemo(
    () => Array.from(new Set(rows.map(row => row.symbol.trim().toUpperCase()).filter(Boolean))).sort(),
    [rows],
  );
  const liveSymbolsKey = liveSymbols.join(',');
  const [liveQuotes, setLiveQuotes] = useState<Record<string, LiveQuoteUpdateData>>({});
  const [quoteConnectionStatus, setQuoteConnectionStatus] = useState<RealtimeConnectionStatus>('closed');

  // Use the same direct realtime subscriber lifecycle as Dashboard and
  // Symbol Page. One socket subscribes to every symbol currently displayed
  // in this Watchlist table.
  useEffect(() => {
    if (!liveSymbols.length) return;
    const subscriber = api.createRealtimeSubscriber?.();
    if (!subscriber) return;
    const unsubscribe = subscriber.onEvent((event: RealtimeEvent) => {
      if (event.type !== 'quote_update') return;
      setLiveQuotes(previous => ({ ...previous, [event.symbol]: event.data }));
    });
    const unsubscribeStatus = subscriber.onStatus(setQuoteConnectionStatus);
    liveSymbols.forEach(symbol => subscriber.subscribeQuote(symbol));
    return () => {
      unsubscribe();
      unsubscribeStatus();
      liveSymbols.forEach(symbol => subscriber.unsubscribeQuote(symbol));
      subscriber.disconnect();
    };
  // `liveSymbolsKey` is the stable representation of the current symbol set.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveSymbolsKey]);

  useEffect(() => {
    if (!showColToggle) return;
    const onClick = (e: MouseEvent) => {
      if (colToggleRef.current && !colToggleRef.current.contains(e.target as Node)) {
        setShowColToggle(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [showColToggle]);

  const fetchIntelligence = useCallback(async () => {
    setIntelligenceLoading(true);
    setIntelligenceError(null);
    try {
      const result = await api.getWatchlistIntelligence(watchlistId, intelligenceSessionScope);
      setIntelligence(result);
    } catch (e: any) {
      setIntelligenceError(e?.message || 'Could not build the watchlist briefing');
    } finally {
      setIntelligenceLoading(false);
    }
  }, [intelligenceSessionScope, watchlistId]);

  // Calendar data is provider-estimated and cached server-side. Fetch it only
  // when the selected watchlist changes — session toggles and 30s rescans do
  // not need to re-request corporate events.
  useEffect(() => {
    let active = true;
    api.getWatchlistCalendar(watchlistId)
      .then(calendar => { if (active) setIntelligenceEvents(calendar.events || []); })
      .catch(() => { if (active) setIntelligenceEvents([]); });
    return () => { active = false; };
  }, [watchlistId]);

  const fetchData = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

    try {
      const scanResp = await api.getWatchlistScan(watchlistId);
      const scanResults = scanResp.results || [];
      setScanTimestamp(scanResp.timestamp || null);

      // Build row data from scan results (no RS yet).
      const baseRows: RowData[] = scanResults.map((r): RowData => ({
        symbol: r.symbol,
        entityType: (r.entity_type as 'stock' | 'etf' | null) ?? 'stock',
        price: r.quote?.price ?? null,
        change: r.change ?? null,
        changePct: r.change_pct ?? null,
        rs: null,
        rsSignals: [],
        trendSignals: r.trend_signals ?? {},
        raw: r,
      }));

      // Phase 3.9.12: single batched RS call instead of N concurrent calls.
      // Cuts N HTTP round-trips + connection-pool pressure on the watchlist page.
      let rsLookup: Record<string, RelativeStrengthData> = {};
      if (baseRows.length > 0) {
        try {
          const batch = await api.getBatchRelativeStrength(baseRows.map(r => r.symbol));
          rsLookup = (batch.results as Record<string, RelativeStrengthData>) || {};
        } catch (e) {
          // Soft-fail: leave rs=null for every row rather than blocking the scan.
          console.warn('Batch RS failed, rendering without RS column', e);
        }
      }

      const finalRows = baseRows.map((row) => {
        const data = rsLookup[row.symbol];
        return data ? { ...row, rsSignals: data.signals } : row;
      });

      setRows(finalRows);
    } catch (e: any) {
      setError(e?.message || 'Failed to load watchlist data');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [watchlistId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // The Scanner request finishes before this runs, so Intelligence always
  // reads that same cache rather than starting another provider-backed scan.
  useEffect(() => {
    if (scanTimestamp == null) return;
    void fetchIntelligence();
  }, [fetchIntelligence, scanTimestamp]);

  const fetchSessionPrices = useCallback(async () => {
    // Neither "everything" (unfiltered) nor "nothing" (no valid `sessions`
    // query param to send — the backend 422s on an empty list) should hit
    // the API.
    if (combinedSessionView || noSessionsSelected) {
      sessionRequestIdRef.current += 1;
      setSessionPrices({});
      setMissingSessionSymbols([]);
      setSessionError(null);
      return;
    }
    const requestId = ++sessionRequestIdRef.current;
    try {
      const response = await api.getWatchlistSessionPrices(
        watchlistId,
        sessionSelectionKey.split(',').filter(Boolean),
      );
      if (requestId !== sessionRequestIdRef.current) return;
      setSessionPrices(Object.fromEntries(
        response.results.map(snapshot => [snapshot.symbol.toUpperCase(), snapshot]),
      ));
      setMissingSessionSymbols(response.missing_symbols ?? []);
      setSessionError(null);
    } catch (error: any) {
      if (requestId !== sessionRequestIdRef.current) return;
      setSessionError(error?.message || 'Session prices unavailable');
    }
  }, [combinedSessionView, noSessionsSelected, sessionSelectionKey, watchlistId]);

  useEffect(() => {
    if (combinedSessionView || noSessionsSelected) setSessionPrices({});
    void fetchSessionPrices();
    if (combinedSessionView || noSessionsSelected) return;
    const interval = window.setInterval(() => void fetchSessionPrices(), 10_000);
    return () => window.clearInterval(interval);
  }, [combinedSessionView, noSessionsSelected, fetchSessionPrices]);

  // Match Symbol Page's fallback behavior: WebSocket is the primary live
  // source, while a slow REST refresh repairs missed events or a temporarily
  // disconnected browser socket (and refreshes scanner-only fields).
  useEffect(() => {
    const interval = setInterval(() => fetchData(true), 30_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Refresh scanner fields when the user returns to the tab; live prices
  // continue through the WebSocket subscription without REST polling.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') fetchData(true);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [fetchData]);

  // --- Per-symbol actions -----------------------------------------------

  const handleToggleSymbol = useCallback(async (symbol: string) => {
    const row = rows.find(r => r.symbol === symbol);
    if (!row) return;
    const nextEnabled = row.raw.is_enabled === false; // toggle
    setTogglingSymbol(symbol);
    // Optimistic update so the UI responds instantly.
    setRows(prev => prev.map(r =>
      r.symbol === symbol
        ? { ...r, raw: { ...r.raw, is_enabled: nextEnabled } }
        : r
    ));
    try {
      await api.updateWatchlistSymbol(watchlistId, symbol, { is_enabled: nextEnabled });
    } catch (err: any) {
      // Roll back on failure.
      setRows(prev => prev.map(r =>
        r.symbol === symbol
          ? { ...r, raw: { ...r.raw, is_enabled: !nextEnabled } }
          : r
      ));
      setError(err?.message || 'Failed to toggle symbol');
    } finally {
      setTogglingSymbol(null);
    }
  }, [rows, watchlistId]);

  const handleDeleteSymbol = useCallback(async (symbol: string) => {
    if (!window.confirm(`Permanently remove ${symbol} from this watchlist?`)) return;
    setPendingSymbol(symbol);
    setPendingAction('delete');
    try {
      await api.removeSymbolFromWatchlist(watchlistId, symbol);
      clearSymbolCatalogCache();
      setRows(prev => prev.filter(r => r.symbol !== symbol));
    } catch (err: any) {
      setError(err?.message || 'Failed to remove symbol');
    } finally {
      setPendingSymbol(null);
      setPendingAction(null);
    }
  }, [watchlistId]);

  // Sorting. Memoized so the virtualized list reuses the same array
  // reference when the underlying rows + sort haven't changed.
  // Resolve the displayed RS signal from the selected benchmark here (not in
  // fetchData) so switching benchmarks is instant and needs no re-fetch.
  const displayRows = useMemo(
    () => rows.map((r): RowData => {
      const symbol = r.symbol.toUpperCase();
      const live = liveQuotes[symbol];
      const withRelativeStrength = {
        ...r,
        rs: r.rsSignals.find(s => s.benchmark === rsBenchmark) ?? r.rsSignals[0] ?? null,
      };
      if (!combinedSessionView) {
        const snapshot = sessionPrices[symbol];
        if (!snapshot) {
          return { ...withRelativeStrength, price: null, change: null, changePct: null };
        }
        const liveSession = classifySessionFromTimestamp(live?.timestamp ?? null);
        const liveDate = tradingDateFromTimestamp(live?.timestamp ?? null);
        const useLive = Boolean(
          live?.price != null
          && liveSession
          && selectedSessions.has(liveSession)
          && liveDate === snapshot.trading_date,
        );
        const price = useLive ? live!.price! : snapshot.price;
        const change = snapshot.baseline_price != null ? price - snapshot.baseline_price : snapshot.change;
        return {
          ...withRelativeStrength,
          sessionSnapshot: snapshot,
          liveQuote: useLive ? live : undefined,
          price,
          change,
          changePct: snapshot.baseline_price
            ? (change! / snapshot.baseline_price) * 100
            : snapshot.change_pct,
          raw: {
            ...r.raw,
            quote: {
              symbol,
              price,
              bid: useLive ? live!.bid : null,
              ask: useLive ? live!.ask : null,
              volume: snapshot.volume,
              timestamp: useLive ? live!.timestamp : snapshot.timestamp,
              provider: useLive ? live!.provider : snapshot.provider,
              data_status: useLive ? 'LIVE' : snapshot.data_status,
              session: useLive ? liveSession : snapshot.session,
            },
          },
        };
      }
      // liveQuotes never expires an entry -- once a symbol gets a WebSocket
      // tick it stays in state until a *newer* tick replaces it, so a quiet
      // stock's last tick (say, from premarket) can sit there for hours
      // after the 30s-polled scan has moved on through regular/after-hours.
      // Without this guard the combined ("all sessions") view would keep
      // computing change% off that stale price forever -- e.g. showing a
      // premarket-vs-yesterday's-close % long after the actual after-hours
      // price implies a different (correct) total-day %. Only trust `live`
      // when it's at least as new as the scan's own quote.
      const scanQuoteTimestamp = r.raw.quote?.timestamp ?? null;
      const liveIsFresher = Boolean(
        live?.timestamp
        && (!scanQuoteTimestamp || Date.parse(live.timestamp) >= Date.parse(scanQuoteTimestamp)),
      );
      // A live quote is useful even when the scanner has not yet produced a
      // valid prior-close baseline for this symbol. Never suppress the live
      // price just because change/change % cannot be recalculated yet.
      if (!live || live.price == null || !liveIsFresher) {
        return withRelativeStrength;
      }
      if (r.price == null || r.change == null) {
        return {
          ...withRelativeStrength,
          price: live.price,
        };
      }
      const priorClose = r.price - r.change;
      const liveChange = live.price - priorClose;
      return {
        ...withRelativeStrength,
        liveQuote: live,
        price: live.price,
        change: liveChange,
        changePct: priorClose !== 0 ? (liveChange / priorClose) * 100 : r.changePct,
      };
    }),
    [rows, rsBenchmark, liveQuotes, combinedSessionView, selectedSessions, sessionPrices],
  );

  const sorted = useMemo(() => {
    const copy = [...displayRows];
    copy.sort((a, b) => {
      let cmp = 0;
      switch (sortCol) {
        case 'symbol': cmp = a.symbol.localeCompare(b.symbol); break;
        case 'price': cmp = (a.price ?? -Infinity) - (b.price ?? -Infinity); break;
        case 'change': cmp = (a.changePct ?? -Infinity) - (b.changePct ?? -Infinity); break;
        case 'rs': cmp = (a.rs?.rs_pct ?? 0) - (b.rs?.rs_pct ?? 0); break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return copy;
  }, [displayRows, sortCol, sortDir]);

  // Stable callback for the virtualized list. Without this, every parent
  // re-render would create a new function identity, forcing react-window
  // to re-render every visible row.
  const itemData = useMemo(
    () => ({
      rows: sorted,
      onSelectSymbol,
      onToggleSymbol: handleToggleSymbol,
      onDeleteSymbol: handleDeleteSymbol,
      togglingSymbol,
      pendingSymbol,
      pendingAction,
      visibleColKeys: effectiveColKeys,
      gridTemplate: virtGridTemplate,
      quoteConnectionStatus,
      marketSession,
    }),
    [sorted, onSelectSymbol, handleToggleSymbol, handleDeleteSymbol, togglingSymbol, pendingSymbol, pendingAction, effectiveColKeys, virtGridTemplate, quoteConnectionStatus, marketSession],
  );

  const toggleSort = (col: typeof sortCol) => {
    if (sortCol === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col);
      setSortDir('desc');
    }
  };

  const thClass = (col: typeof sortCol) =>
    `sortable-th${sortCol === col ? ' sorted' : ''}`;

  const isSortableCol = (key: string): key is typeof sortCol => {
    return ['symbol', 'price', 'change', 'rs'].includes(key);
  };

  if (loading) {
    return (
      <div className="watchlist-table-container">
        <p className="empty-state">Scanning watchlist…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="watchlist-table-container">
        <p className="empty-state">⚠ {error}</p>
        <button className="btn btn-small data-state-retry" onClick={() => void fetchData()}>Retry scan</button>
      </div>
    );
  }

  const useVirtual = sorted.length > VIRT_THRESHOLD;
  const visibleLiveQuotes = Object.values(liveQuotes).filter(quote => {
    if (combinedSessionView) return true;
    const session = classifySessionFromTimestamp(quote.timestamp);
    return session != null && selectedSessions.has(session);
  });
  const latestLiveQuote = visibleLiveQuotes.reduce<LiveQuoteUpdateData | null>(
    (latest, quote) => !latest || (quote.received_at ?? 0) > (latest.received_at ?? 0) ? quote : latest,
    null,
  );
  const latestSessionSnapshot = Object.values(sessionPrices).reduce<WatchlistSessionPrice | null>(
    (latest, snapshot) => !latest || snapshot.timestamp > latest.timestamp ? snapshot : latest,
    null,
  );

  return (
    <div className="watchlist-table-container">
      <div className="watchlist-table-toolbar">
        <span className="table-count">{sorted.length} symbols{useVirtual ? ' (virtualized)' : ''}</span>
        <fieldset className="watchlist-session-filters" aria-label="Watchlist market sessions">
          <legend>Price session</legend>
          {MARKET_SESSIONS.map(session => (
            <label key={session.key}>
              <input
                type="checkbox"
                checked={selectedSessions.has(session.key)}
                onChange={() => setSelectedSessions(previous => {
                  const next = new Set(previous);
                  if (next.has(session.key)) next.delete(session.key);
                  else next.add(session.key);
                  return next;
                })}
              />
              {session.label}
            </label>
          ))}
        </fieldset>
        {noSessionsSelected && (
          <span className="watchlist-session-hint">Select at least one session to show prices.</span>
        )}
        {sessionError && <span className="watchlist-session-error">{sessionError}</span>}
        {!noSessionsSelected && missingSessionSymbols.length > 0 && (
          <span className="watchlist-session-hint" title={missingSessionSymbols.join(', ')}>
            No local data yet for {missingSessionSymbols.length} symbol{missingSessionSymbols.length === 1 ? '' : 's'}.
          </span>
        )}
        <MarketDataFreshnessBadge
          dataStatus={latestLiveQuote ? 'LIVE' : latestSessionSnapshot?.data_status ?? 'STALE'}
          timestamp={latestLiveQuote?.timestamp ?? latestSessionSnapshot?.timestamp}
          showAge
          connectionStatus={quoteConnectionStatus}
          provider={latestLiveQuote?.provider ?? latestSessionSnapshot?.provider}
          marketSession={marketSession}
        />
        <select
          className="rs-benchmark-select"
          value={rsBenchmark}
          onChange={e => setRsBenchmark(e.target.value)}
          title="Relative Strength benchmark"
        >
          <option value="SPY">RS vs SPY</option>
          <option value="QQQ">RS vs QQQ</option>
          <option value="IWM">RS vs IWM</option>
          <option value="DIA">RS vs DIA</option>
        </select>
        <div className="wl-col-toggle-wrap" ref={colToggleRef}>
          <button
            className="wl-col-toggle-btn"
            onClick={() => setShowColToggle(v => !v)}
            title="Toggle columns"
          >
            ⚙ Columns
          </button>
          {showColToggle && (
            <div className="wl-col-dropdown">
              {WATCHLIST_COLUMNS.map(col => (
                <label key={col.key} className="wl-col-option">
                  <input
                    type="checkbox"
                    checked={visibleColKeys.has(col.key)}
                    onChange={() => {
                      setVisibleColKeys(prev => {
                        const next = new Set(prev);
                        if (next.has(col.key)) {
                          next.delete(col.key);
                        } else {
                          next.add(col.key);
                        }
                        return next;
                      });
                    }}
                  />
                  {col.header}
                </label>
              ))}
            </div>
          )}
        </div>
        <button
          className={`btn btn-small ${refreshing ? 'btn-loading' : ''}`}
          onClick={() => fetchData(true)}
          disabled={refreshing}
        >
          {refreshing ? '⟳ Scanning…' : '↻ Rescan'}
        </button>
        {scanTimestamp && (
          <span className="table-timestamp">
            Scanned {formatETTime(scanTimestamp)}
          </span>
        )}
      </div>

      {sorted.length === 0 ? (
        <p className="empty-state">No symbols in this watchlist.</p>
      ) : useVirtual ? (
        <div className="watchlist-virt">
          <div className="watchlist-virt-header" style={{ gridTemplateColumns: virtGridTemplate }}>
            {shownColumns.map((col) => (
              <div
                key={col.key}
                className={`virt-cell th ${col.sortable && isSortableCol(col.key) ? thClass(col.key) : ''}`}
                onClick={col.sortable ? () => toggleSort(col.key as typeof sortCol) : undefined}
                style={{ cursor: col.sortable ? 'pointer' : 'default' }}
              >
                {col.header}
                {col.sortable && <SortIcon column={col.key} sortCol={sortCol} sortDir={sortDir} />}
              </div>
            ))}
          </div>
          <FixedSizeList
            ref={listRef}
            height={VIRT_HEIGHT}
            itemCount={sorted.length}
            itemSize={VIRT_ROW_HEIGHT}
            width="100%"
            itemData={itemData}
            itemKey={(idx, data) => data.rows[idx].symbol}
            className="watchlist-virt-body"
          >
            {VirtualizedRow}
          </FixedSizeList>
        </div>
      ) : (
        <div className="watchlist-table-scroll">
          <table className="watchlist-table">
            <thead>
              <tr>
                {shownColumns.map((col) => (
                  <th
                    key={col.key}
                    className={col.sortable && isSortableCol(col.key) ? thClass(col.key) : ''}
                    onClick={col.sortable ? () => toggleSort(col.key as typeof sortCol) : undefined}
                    style={{
                      // 'fr' widths are only meaningful in the virtualized grid.
                      width: col.width.endsWith('fr') ? undefined : col.width,
                      cursor: col.sortable ? 'pointer' : undefined,
                    }}
                  >
                    {col.header}
                    {col.sortable && <SortIcon column={col.key} sortCol={sortCol} sortDir={sortDir} />}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map(row => (
                <WatchlistRow
                  key={row.symbol}
                  row={row}
                  onSelectSymbol={onSelectSymbol}
                  onToggleSymbol={handleToggleSymbol}
                  onDeleteSymbol={handleDeleteSymbol}
                  togglingSymbol={togglingSymbol}
                  pendingSymbol={pendingSymbol}
                  pendingAction={pendingAction}
                  visibleColKeys={effectiveColKeys}
                  quoteConnectionStatus={quoteConnectionStatus}
                  marketSession={marketSession}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <WatchlistIntelligence
        data={intelligence}
        events={intelligenceEvents}
        loading={intelligenceLoading}
        error={intelligenceError}
        onSelectSymbol={onSelectSymbol}
      />
    </div>
  );
}

// Per-row presentation, memoized so re-renders only happen when this row's
// data or callbacks change — not on every parent state tick (sort, scan, etc.).
// Without this, every sort click or scan refresh re-renders all 10-50 rows.
const WatchlistRow = React.memo(function WatchlistRow({
  row,
  onSelectSymbol,
  onToggleSymbol,
  onDeleteSymbol,
  togglingSymbol,
  pendingSymbol,
  pendingAction,
  visibleColKeys,
  quoteConnectionStatus,
  marketSession,
}: {
  row: RowData;
  onSelectSymbol: (symbol: string) => void;
  onToggleSymbol: (symbol: string) => void;
  onDeleteSymbol: (symbol: string) => void;
  togglingSymbol: string | null;
  pendingSymbol: string | null;
  pendingAction: 'delete' | null;
  visibleColKeys: Set<string>;
  quoteConnectionStatus: RealtimeConnectionStatus;
  marketSession?: MarketSessionType | null;
}) {
  const rowEnabled = isRowEnabled(row.raw);
  const isPending = pendingSymbol === row.symbol;
  const isDeleting = isPending && pendingAction === 'delete';
  const isToggling = togglingSymbol === row.symbol;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Ignore keys bubbling up from the row's buttons (toggle/delete).
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelectSymbol(row.symbol);
    }
  };

  return (
    <tr
      onClick={() => onSelectSymbol(row.symbol)}
      onKeyDown={handleKeyDown}
      className={`watchlist-table-row${rowEnabled ? '' : ' row-disabled'}`}
      tabIndex={0}
    >
      {visibleColKeys.has('symbol') && (
        <td className="td-symbol">
          {row.symbol}
          {!rowEnabled && <span className="row-disabled-badge" title="Disabled">⏸</span>}
        </td>
      )}
      {visibleColKeys.has('type') && (
        <td className="td-type">
          {row.entityType === 'etf' ? (
            <span className="entity-tag entity-tag-etf">ETF</span>
          ) : (
            <span className="entity-tag entity-tag-stock">Stock</span>
          )}
        </td>
      )}
      {visibleColKeys.has('price') && (
        <td className="td-price" title={row.sessionSnapshot ? `${row.sessionSnapshot.session.replace('_', ' ')} · ${row.sessionSnapshot.trading_date}` : undefined}>
          {row.price != null ? `$${fmtPrice(row.price)}` : '—'}
        </td>
      )}
      {visibleColKeys.has('change') && (
        <td className={`td-change ${changeCellClass(row.changePct)}`} title={row.sessionSnapshot?.baseline_label ?? undefined}>
          {row.changePct != null
            ? `${row.changePct > 0 ? '+' : ''}${fmt(row.changePct)}%`
            : '—'}
        </td>
      )}
      {visibleColKeys.has('freshness') && (
        <td className="td-freshness"><RowFreshness row={row} connectionStatus={quoteConnectionStatus} marketSession={marketSession} /></td>
      )}
      {visibleColKeys.has('trend') && (
        <td className="td-trend"><TrendColumn trendSignals={row.trendSignals} /></td>
      )}
      {visibleColKeys.has('rs') && (
        <td className={`td-rs ${rsCellClass(row.rs)}`}>{rsCellLabel(row.rs)}</td>
      )}
      {visibleColKeys.has('actions') && (
        <td className="td-actions" onClick={(e) => e.stopPropagation()}>
          <div className="actions-inner">
            <button
              className={`row-action-btn${rowEnabled ? ' row-action-toggle-active' : ''}`}
              title={rowEnabled ? 'Disable symbol' : 'Enable symbol'}
              disabled={isToggling}
              onClick={() => onToggleSymbol(row.symbol)}
            >
              {isToggling ? '…' : rowEnabled ? '⏸' : '▶'}
            </button>
            <button
              className="row-action-btn row-action-danger"
              title="Remove from watchlist"
              disabled={isDeleting}
              onClick={() => onDeleteSymbol(row.symbol)}
            >
              {isDeleting ? '…' : '×'}
            </button>
          </div>
        </td>
      )}
    </tr>
  );
});

export default WatchlistTable;
