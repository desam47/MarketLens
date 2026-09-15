/**
 * ScannerPage — live scanner results for a watchlist.
 *
 * Subscribes to every enabled symbol in the selected watchlist via the
 * WebSocket stream at /api/scanner-stream/ws. The page re-renders when
 * any subscribed symbol's scan_result arrives, replacing the row with the
 * fresh score/signals.
 *
 * Phase 10 adds a composable filter builder and named rankings panel
 * in a collapsible sidebar.
 *
 * The page is intentionally read-only — the goal is observation. A user
 * who wants to dig into a symbol clicks the row to navigate to the
 * Symbol page.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FixedSizeList, ListChildComponentProps } from 'react-window';
import api, { ScanResult, Watchlist, WatchlistSymbol } from '../services/api';
import { useScannerStream } from '../hooks/useScannerStream';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { parseET } from '../components/chartMath';
import { ScannerTableSkeleton } from '../components/skeletons/ScannerTableSkeleton';
import { ErrorBanner } from '../components/ErrorBanner';
import { fmtPrice } from '../components/watchlistUtils';
import { FilterBuilder } from '../components/FilterBuilder';
import { NamedRankingsPanel } from '../components/NamedRankingsPanel';

/** Format a price with up to 4 decimals, trimming trailing zeros. */
const strPrice = fmtPrice;

interface ScannerPageProps {
  onSelectSymbol: (symbol: string) => void;
}

// ---------------------------------------------------------------------------
// Trend column — maps backend TF key to display label and sort priority
// ---------------------------------------------------------------------------
interface TrendTf {
  key: string;      // key in trend_signals Record (e.g. "ONE_MINUTE")
  short: string;    // badge label  (e.g. "1m")
}
const TREND_TFS: TrendTf[] = [
  { key: 'ONE_MINUTE',   short: '1m'  },
  { key: 'FIVE_MINUTE',  short: '5m'  },
  { key: 'FIFTEEN_MINUTE', short: '15m' },
  { key: 'ONE_HOUR',     short: '1h'  },
  { key: 'FOUR_HOUR',    short: '4h'  },
  { key: 'ONE_DAY',      short: '1d'  },
];

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
    <div className="scanner-vcell cell-trend">
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

// ---------------------------------------------------------------------------
// Score / freshness helpers
// ---------------------------------------------------------------------------
function scoreClass(score: number): string {
  if (score >= 30) return 'score-bullish';
  if (score <= -30) return 'score-bearish';
  return 'score-neutral';
}

function freshnessClass(ts: string | null): string {
  if (!ts) return 'freshness-stale';
  const ageSec = (Date.now() - parseET(ts).getTime()) / 1000;
  if (ageSec < 60) return 'freshness-fresh';
  if (ageSec < 300) return 'freshness-recent';
  return 'freshness-stale';
}

function freshnessLabel(ts: string | null): string {
  if (!ts) return '—';
  const ageSec = Math.max(0, (Date.now() - parseET(ts).getTime()) / 1000);
  if (ageSec < 60) return `${Math.floor(ageSec)}s ago`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ago`;
  return `${Math.floor(ageSec / 3600)}h ago`;
}

/** Format a timestamp as HH:MM:SS in the user's local timezone. */
function formatClockTime(ts: string | null): string {
  if (!ts) return '—';
  const d = parseET(ts);
  if (isNaN(d.getTime())) return '—';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

// Scanner table row, memoized so a fresh quote on one symbol does not
// re-render the other 49-99 rows. The result + errors prop is shallow
// compared by React.memo: when a single symbol's result changes, only
// that row updates. Without this, every WebSocket push re-renders all rows.
const ScannerRow = React.memo(function ScannerRow({
  sym,
  result,
  err,
  onSelectSymbol,
}: {
  sym: string;
  result: ScanResult | null;
  err: string | undefined;
  onSelectSymbol: (s: string) => void;
}) {
  const freshness = freshnessClass(result?.timestamp ?? null);
  return (
    <div
      className={`scanner-vrow ${result ? '' : 'row-pending'}`}
      onClick={() => onSelectSymbol(sym)}
    >
      <div className="scanner-vcell cell-symbol">{sym}</div>
      <div className="scanner-vcell">{result?.quote?.price != null ? strPrice(result.quote.price) : '—'}</div>
      <div className={`scanner-vcell ${result ? scoreClass(result.total_score) : ''}`}>
        {result ? result.total_score.toFixed(1) : '…'}
      </div>
      <TrendColumn trendSignals={result?.trend_signals ?? {}} />
      <div className="scanner-vcell cell-signals">
        {err ? (
          <span className="error-text" title={err}>err</span>
        ) : result && result.signals.length > 0 ? (
          result.signals.slice(0, 4).map((sig) => (
            <span key={sig} className="signal-chip">{sig}</span>
          ))
        ) : result ? (
          <span className="info-text">—</span>
        ) : (
          <span className="info-text">waiting</span>
        )}
      </div>
      <div
        className={`scanner-vcell cell-freshness ${freshness}`}
        title={result?.timestamp ?? undefined}
      >
        <div className="freshness-time">{formatClockTime(result?.timestamp ?? null)}</div>
        <div className="freshness-age">{freshnessLabel(result?.timestamp ?? null)}</div>
      </div>
    </div>
  );
});

// Virtualized row renderer for react-window FixedSizeList.
const ROW_HEIGHT = 56;

type RowItem = { sym: string; result: ScanResult | null };
type VirtualRowData = { rows: RowItem[]; errors: Record<string, string>; onSelectSymbol: (s: string) => void };

function VirtualRow({ index, style, data }: ListChildComponentProps<VirtualRowData>) {
  const { rows, errors, onSelectSymbol } = data;
  const { sym, result } = rows[index];
  return (
    <div style={style}>
      <ScannerRow
        sym={sym}
        result={result ?? null}
        err={errors[sym]}
        onSelectSymbol={onSelectSymbol}
      />
    </div>
  );
}

type SidebarTab = 'filters' | 'rankings';

export function ScannerPage({ onSelectSymbol }: ScannerPageProps) {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [selectedWatchlistId, setSelectedWatchlistId] = useState<number | null>(null);
  const [symbols, setSymbols] = useState<WatchlistSymbol[]>([]);
  const [loadingWatchlists, setLoadingWatchlists] = useState(true);
  const [loadingSymbols, setLoadingSymbols] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<'score' | 'symbol'>('score');

  // Phase 10: filter sidebar state
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>('rankings');
  /** When a filter is applied, this holds the filtered results. */
  const [filterResults, setFilterResults] = useState<ScanResult[] | null>(null);

  // Measure the table wrapper so react-window fills the available height
  // instead of being capped at a fixed 500px (~9 rows).
  const tableWrapperRef = useRef<HTMLDivElement>(null);
  const [tableHeight, setTableHeight] = useState(500);
  useEffect(() => {
    const el = tableWrapperRef.current;
    if (!el) return;
    const update = () => setTableHeight(el.clientHeight);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    window.addEventListener('resize', update);
    return () => { ro.disconnect(); window.removeEventListener('resize', update); };
  }, []);

  // Fetch watchlists on mount.
  useEffect(() => {
    let cancelled = false;
    const fetch = async () => {
      setLoadingWatchlists(true);
      setError(null);
      try {
        const data = await api.getWatchlists();
        if (cancelled) return;
        setWatchlists(data);
        if (data.length > 0) setSelectedWatchlistId(data[0].id);
      } catch (err: any) {
        if (!cancelled) setError(err.message || 'Failed to load watchlists');
      } finally {
        if (!cancelled) setLoadingWatchlists(false);
      }
    };
    fetch();
    return () => { cancelled = true; };
  }, []);

  // Fetch the symbols for the selected watchlist.
  useEffect(() => {
    if (selectedWatchlistId === null) {
      setSymbols([]);
      return;
    }
    let cancelled = false;
    const fetch = async () => {
      setLoadingSymbols(true);
      try {
        const data = await api.getWatchlistSymbols(selectedWatchlistId);
        if (cancelled) return;
        setSymbols(data.filter((s) => s.is_enabled));
      } catch (err: any) {
        if (!cancelled) setError(err.message || 'Failed to load symbols');
      } finally {
        if (!cancelled) setLoadingSymbols(false);
      }
    };
    fetch();
    return () => { cancelled = true; };
  }, [selectedWatchlistId]);

  const subscribedSymbols = useMemo(
    () => symbols.map((s) => s.symbol),
    [symbols]
  );

  // Stable no-op for now; kept as a hook arg so the subscriber's onEvent
  // listener identity is stable across renders.
  const onUpdate = useCallback(() => undefined, []);
  const onScanError = useCallback((sym: string, msg: string) => {
    console.warn(`Scanner error for ${sym}: ${msg}`);
  }, []);

  const { liveResults, connectionStatus, errors, refresh } = useScannerStream({
    symbols: subscribedSymbols,
    onUpdate,
    onError: onScanError,
  });

  // Build the visible rows — filter results override live results when active
  const rows = useMemo(() => {
    if (filterResults !== null) {
      // Filter mode: show filtered results sorted by total_score desc
      const out = filterResults.map(r => ({ sym: r.symbol, result: r }));
      if (sortBy === 'symbol') {
        out.sort((a, b) => a.sym.localeCompare(b.sym));
      } else {
        out.sort((a, b) => (b.result?.total_score ?? -Infinity) - (a.result?.total_score ?? -Infinity));
      }
      return out;
    }

    // Normal mode: show live results
    const out = subscribedSymbols.map((sym) => {
      const result = liveResults[sym];
      return { sym, result };
    });
    if (sortBy === 'symbol') {
      out.sort((a, b) => a.sym.localeCompare(b.sym));
    } else {
      out.sort((a, b) => (b.result?.total_score ?? -Infinity) - (a.result?.total_score ?? -Infinity));
    }
    return out;
  }, [subscribedSymbols, liveResults, sortBy, filterResults]);

  const connectionDot = connectionStatus === 'open' ? 'dot-green'
    : connectionStatus === 'connecting' ? 'dot-amber'
    : 'dot-red';

  const connectionLabel = connectionStatus === 'open' ? 'Live'
    : connectionStatus === 'connecting' ? 'Connecting…'
    : 'Disconnected';

  const stats = useMemo(() => {
    if (filterResults !== null) {
      const bullish = filterResults.filter((r) => r.total_score >= 30).length;
      const bearish = filterResults.filter((r) => r.total_score <= -30).length;
      return { total: filterResults.length, scanned: filterResults.length, bullish, bearish, neutral: filterResults.length - bullish - bearish };
    }
    const all = Object.values(liveResults);
    const bullish = all.filter((r) => r.total_score >= 30).length;
    const bearish = all.filter((r) => r.total_score <= -30).length;
    const neutral = all.length - bullish - bearish;
    return { total: subscribedSymbols.length, scanned: all.length, bullish, bearish, neutral };
  }, [liveResults, subscribedSymbols, filterResults]);

  // Phase 10: handlers
  const handleFilterResults = useCallback((results: ScanResult[]) => {
    setFilterResults(results);
  }, []);

  const handleClearFilter = useCallback(() => {
    setFilterResults(null);
  }, []);

  const toggleSidebar = useCallback(() => {
    setSidebarOpen(v => !v);
  }, []);

  const handleSelectSymbol = useCallback((s: string) => {
    onSelectSymbol(s);
  }, [onSelectSymbol]);

  if (loadingWatchlists) {
    return <LoadingSpinner message="Loading watchlists…" />;
  }

  if (error && watchlists.length === 0) {
    return <ErrorBanner message={error} onDismiss={() => setError(null)} />;
  }

  if (watchlists.length === 0) {
    return (
      <div className="page scanner-page">
        <h1>Live Scanner</h1>
        <div className="empty-state">
          No watchlists yet. Create one on the Watchlist page to start streaming live scans.
        </div>
      </div>
    );
  }

  return (
    <div className="page scanner-page">
      {/* Main content: sidebar + table */}
      <div className="scanner-layout">
        {/* Left sidebar — filter builder + rankings */}
        <aside className={`scanner-sidebar ${sidebarOpen ? 'open' : ''}`}>
          {/* Sidebar toggle */}
          <button
            className="sidebar-toggle-btn"
            onClick={toggleSidebar}
            title={sidebarOpen ? 'Close sidebar' : 'Open filters & rankings'}
          >
            {sidebarOpen ? '◀' : '▶'}
          </button>

          {sidebarOpen && (
            <>
              {/* Tab switcher */}
              <div className="sidebar-tabs">
                <button
                  className={`sidebar-tab ${sidebarTab === 'rankings' ? 'active' : ''}`}
                  onClick={() => setSidebarTab('rankings')}
                >
                  Rankings
                </button>
                <button
                  className={`sidebar-tab ${sidebarTab === 'filters' ? 'active' : ''}`}
                  onClick={() => setSidebarTab('filters')}
                >
                  Filters
                </button>
              </div>

              {/* Tab panels */}
              <div className="sidebar-panel">
                {sidebarTab === 'rankings' && (
                  <NamedRankingsPanel
                    symbols={subscribedSymbols}
                    topN={10}
                    onSelectSymbol={handleSelectSymbol}
                  />
                )}
                {sidebarTab === 'filters' && (
                  <FilterBuilder
                    symbols={subscribedSymbols}
                    onResults={handleFilterResults}
                    onClear={handleClearFilter}
                  />
                )}
              </div>
            </>
          )}
        </aside>

        {/* Main table area */}
        <div className="scanner-main">
          {/* Header */}
          <div className="scanner-header">
            <div>
              <h1>Live Scanner</h1>
              <p className="info-text">
                {filterResults !== null
                  ? `Showing ${filterResults.length} filtered result${filterResults.length !== 1 ? 's' : ''} — snapshot; the live stream keeps updating in the background.`
                  : 'Streaming scan results over WebSocket. Updates push as the ingestion service ingests fresh quotes.'}
              </p>
            </div>
            <div className="scanner-header-actions">
              <span className={`connection-pill ${connectionDot}`} title={`Status: ${connectionLabel}`}>
                <span className="dot" /> {connectionLabel}
              </span>
              <select
                className="watchlist-select"
                value={selectedWatchlistId ?? ''}
                onChange={(e) => setSelectedWatchlistId(Number(e.target.value))}
              >
                {watchlists.map((wl) => (
                  <option key={wl.id} value={wl.id}>{wl.name}</option>
                ))}
              </select>
              <select
                className="sort-select"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as 'score' | 'symbol')}
                title="Sort order"
              >
                <option value="score">Sort: Score</option>
                <option value="symbol">Sort: Symbol</option>
              </select>
              <button className="btn btn-primary" onClick={refresh}>Refresh</button>
            </div>
          </div>

          {/* Stats bar */}
          <div className="scanner-stats">
            <div className="metric">
              <span className="metric-label">Subscribed</span>
              <span className="metric-value">{stats.total}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Scanned</span>
              <span className="metric-value">{stats.scanned}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Bullish</span>
              <span className="metric-value score-bullish">{stats.bullish}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Bearish</span>
              <span className="metric-value score-bearish">{stats.bearish}</span>
            </div>
            <div className="metric">
              <span className="metric-label">Neutral</span>
              <span className="metric-value score-neutral">{stats.neutral}</span>
            </div>
            {filterResults !== null && (
              <div className="metric">
                <span className="metric-label">Filtered</span>
                <span className="metric-value">{filterResults.length}</span>
                <button className="btn btn-ghost btn-sm" onClick={handleClearFilter}>✕ clear</button>
              </div>
            )}
          </div>

          {/* Table */}
          {loadingSymbols ? (
            <ScannerTableSkeleton />
          ) : subscribedSymbols.length === 0 ? (
            <div className="empty-state">
              This watchlist has no enabled symbols. Add some on the Watchlist page.
            </div>
          ) : (
            <div className="scanner-table-wrapper">
              {/* Static header — always visible */}
              <div className="scanner-vheader">
                <div className="scanner-vheader-cell">Symbol</div>
                <div className="scanner-vheader-cell">Price</div>
                <div className="scanner-vheader-cell">Score</div>
                <div className="scanner-vheader-cell">Trend</div>
                <div className="scanner-vheader-cell">Signals</div>
                <div className="scanner-vheader-cell">Captured</div>
              </div>
              {/* Virtualized body — react-window only renders visible rows */}
              <div className="scanner-vlist" ref={tableWrapperRef}>
                <FixedSizeList
                  height={tableHeight}
                  itemCount={rows.length}
                  itemSize={ROW_HEIGHT}
                  width="100%"
                  itemData={{ rows, errors, onSelectSymbol: handleSelectSymbol }}
                >
                  {VirtualRow}
                </FixedSizeList>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
