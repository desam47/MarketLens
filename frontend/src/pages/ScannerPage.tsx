/**
 * ScannerPage — live scanner results for a watchlist.
 *
 * Subscribes to every enabled symbol in the selected watchlist via the
 * WebSocket stream at /api/scanner-stream/ws. The page re-renders when
 * any subscribed symbol's scan_result arrives, replacing the row with the
 * fresh score/signals.
 *
 * The page is intentionally read-only — the goal is observation. A user
 * who wants to dig into a symbol clicks the row to navigate to the
 * Symbol page.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { FixedSizeList, ListChildComponentProps } from 'react-window';
import api, { ScanResult, Watchlist, WatchlistSymbol } from '../services/api';
import { useScannerStream } from '../hooks/useScannerStream';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ScannerTableSkeleton } from '../components/skeletons/ScannerTableSkeleton';
import { ErrorBanner } from '../components/ErrorBanner';
import { fmtPrice } from '../components/watchlistUtils';

/** Format a price with up to 4 decimals, trimming trailing zeros. */
const strPrice = fmtPrice;

interface ScannerPageProps {
  onSelectSymbol: (symbol: string) => void;
}

function scoreClass(score: number): string {
  if (score >= 30) return 'score-bullish';
  if (score <= -30) return 'score-bearish';
  return 'score-neutral';
}

function freshnessClass(ts: string | null): string {
  if (!ts) return 'freshness-stale';
  const ageSec = (Date.now() - new Date(ts).getTime()) / 1000;
  if (ageSec < 60) return 'freshness-fresh';
  if (ageSec < 300) return 'freshness-recent';
  return 'freshness-stale';
}

function freshnessLabel(ts: string | null): string {
  if (!ts) return '—';
  const ageSec = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (ageSec < 60) return `${Math.floor(ageSec)}s ago`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ago`;
  return `${Math.floor(ageSec / 3600)}h ago`;
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
      <div className={`scanner-vcell cell-freshness ${freshness}`}>
        {freshnessLabel(result?.timestamp ?? null)}
      </div>
    </div>
  );
});

// Virtualized row renderer for react-window FixedSizeList.
const ROW_HEIGHT = 48;

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

export function ScannerPage({ onSelectSymbol }: ScannerPageProps) {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [selectedWatchlistId, setSelectedWatchlistId] = useState<number | null>(null);
  const [symbols, setSymbols] = useState<WatchlistSymbol[]>([]);
  const [loadingWatchlists, setLoadingWatchlists] = useState(true);
  const [loadingSymbols, setLoadingSymbols] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<'score' | 'symbol'>('score');

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
    // Surface as a non-blocking error — the row will show stale data and
    // the connection status will reflect the upstream.
    console.warn(`Scanner error for ${sym}: ${msg}`);
  }, []);

  const { liveResults, connectionStatus, errors, refresh } = useScannerStream({
    symbols: subscribedSymbols,
    onUpdate,
    onError: onScanError,
  });

  const rows = useMemo(() => {
    // For each subscribed symbol, build a row from the live result if
    // available; otherwise render a placeholder so the table layout
    // doesn't shift as results trickle in.
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
  }, [subscribedSymbols, liveResults, sortBy]);

  const connectionDot = connectionStatus === 'open' ? 'dot-green'
    : connectionStatus === 'connecting' ? 'dot-amber'
    : 'dot-red';

  const connectionLabel = connectionStatus === 'open' ? 'Live'
    : connectionStatus === 'connecting' ? 'Connecting…'
    : 'Disconnected';

  const stats = useMemo(() => {
    const all = Object.values(liveResults);
    const bullish = all.filter((r) => r.total_score >= 30).length;
    const bearish = all.filter((r) => r.total_score <= -30).length;
    const neutral = all.length - bullish - bearish;
    return { total: subscribedSymbols.length, scanned: all.length, bullish, bearish, neutral };
  }, [liveResults, subscribedSymbols]);

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
      <div className="scanner-header">
        <div>
          <h1>Live Scanner</h1>
          <p className="info-text">
            Streaming scan results over WebSocket. Updates push as the ingestion
            service ingests fresh quotes.
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
      </div>

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
            <div className="scanner-vheader-cell">Signals</div>
            <div className="scanner-vheader-cell">Last Update</div>
          </div>
          {/* Virtualized body — react-window only renders visible rows */}
          <FixedSizeList
            height={Math.min(rows.length * ROW_HEIGHT, 500)}
            itemCount={rows.length}
            itemSize={ROW_HEIGHT}
            width="100%"
            itemData={{ rows, errors, onSelectSymbol }}
          >
            {VirtualRow}
          </FixedSizeList>
        </div>
      )}
    </div>
  );
}
