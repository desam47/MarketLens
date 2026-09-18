import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { FixedSizeList, ListChildComponentProps } from 'react-window';
import api, { WatchlistScanResult, RelativeStrengthData, RelativeStrengthSignal } from '../services/api';
import { formatETTime } from './chartMath';
import {
  fmt,
  fmtPrice,
  changeCellClass,
  rsCellClass,
  rsCellLabel,
  isRowEnabled,
} from './watchlistUtils';

const TREND_TFS = [
  { key: 'ONE_MINUTE', short: '1m' },
  { key: 'FIVE_MINUTE', short: '5m' },
  { key: 'FIFTEEN_MINUTE', short: '15m' },
  { key: 'ONE_HOUR', short: '1h' },
  { key: 'FOUR_HOUR', short: '4h' },
  { key: 'ONE_DAY', short: '1d' },
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
  trendSignals: Record<string, any>;
  raw: WatchlistScanResult;
}

// Threshold: above this row count, switch to a virtualized list. Below, a
// regular table renders more cleanly (sticky header, full column widths).
const VIRT_THRESHOLD = 30;
const VIRT_ROW_HEIGHT = 44;
const VIRT_HEIGHT = 480;

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
}>) {
  const { rows, onSelectSymbol, onToggleSymbol, onDeleteSymbol,
    togglingSymbol, pendingSymbol, pendingAction } = data;
  const row = rows[index];

  const isPending = pendingSymbol === row.symbol;
  const isDeleting = isPending && pendingAction === 'delete';
  const isToggling = togglingSymbol === row.symbol;
  const rowEnabled = isRowEnabled(row.raw);

  return (
    <div
      style={style}
      className={`virt-row watchlist-table-row${rowEnabled ? '' : ' row-disabled'}`}
      onClick={() => onSelectSymbol(row.symbol)}
    >
      <div className="virt-cell td-symbol">
        {row.symbol}
        {!rowEnabled && <span className="row-disabled-badge" title="Disabled">⏸</span>}
      </div>
      <div className="virt-cell td-type">
        {row.entityType === 'etf' ? (
          <span className="entity-tag entity-tag-etf">ETF</span>
        ) : (
          <span className="entity-tag entity-tag-stock">Stock</span>
        )}
      </div>
      <div className="virt-cell td-price">
        {row.price != null ? `$${fmtPrice(row.price)}` : '—'}
      </div>
      <div className={`virt-cell td-change ${changeCellClass(row.changePct)}`}>
        {row.changePct != null
          ? `${row.changePct > 0 ? '+' : ''}${fmt(row.changePct)}%`
          : '—'}
      </div>
      <div className="virt-cell td-trend"><TrendColumn trendSignals={row.trendSignals} /></div>
      <div className={`virt-cell td-rs ${rsCellClass(row.rs)}`}>{rsCellLabel(row.rs)}</div>
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
  const [error, setError] = useState<string | null>(null);
  const [sortCol, setSortCol] = useState(sortColumn);
  const [sortDir, setSortDir] = useState(sortDirection);
  const [pendingSymbol, setPendingSymbol] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<'delete' | null>(null);
  const [togglingSymbol, setTogglingSymbol] = useState<string | null>(null);
  const listRef = useRef<FixedSizeList>(null);

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
        if (data) {
          // Pick the SPY benchmark for display.
          const spySignal = data.signals.find(s => s.benchmark === 'SPY') ?? data.signals[0] ?? null;
          return { ...row, rs: spySignal };
        }
        return row;
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

  // Keep prices/change% current without a manual reload — the
  // backend scan is always live (no TTL cache on this endpoint), so a
  // stale table here is purely a frontend polling gap. Mirrors
  // AlertsCard's 30s trigger refresh; `refresh=true` uses the small
  // "Rescan" spinner instead of the full loading skeleton, so periodic
  // updates don't blank the table while it refreshes.
  useEffect(() => {
    const interval = setInterval(() => fetchData(true), 30_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Browsers throttle setInterval heavily in backgrounded/inactive tabs,
  // so a tab left in the background can sit on a stale scan for far
  // longer than 30s until its throttled timer eventually fires again.
  // Refetch immediately on tab-focus-regain to close that gap.
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
  const sorted = useMemo(() => {
    const copy = [...rows];
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
  }, [rows, sortCol, sortDir]);

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
    }),
    [sorted, onSelectSymbol, handleToggleSymbol, handleDeleteSymbol, togglingSymbol, pendingSymbol, pendingAction],
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
      </div>
    );
  }

  const useVirtual = sorted.length > VIRT_THRESHOLD;

  return (
    <div className="watchlist-table-container">
      <div className="watchlist-table-toolbar">
        <span className="table-count">{sorted.length} symbols{useVirtual ? ' (virtualized)' : ''}</span>
        {scanTimestamp && (
          <span className="table-timestamp">
            Scanned {formatETTime(scanTimestamp)}
          </span>
        )}
        <button
          className={`btn btn-small ${refreshing ? 'btn-loading' : ''}`}
          onClick={() => fetchData(true)}
          disabled={refreshing}
        >
          {refreshing ? '⟳ Scanning…' : '↻ Rescan'}
        </button>
      </div>

      {sorted.length === 0 ? (
        <p className="empty-state">No symbols in this watchlist.</p>
      ) : useVirtual ? (
        <div className="watchlist-virt">
          <div className="watchlist-virt-header">
            <div className="virt-cell th" onClick={() => toggleSort('symbol')}>
              Symbol <SortIcon column="symbol" sortCol={sortCol} sortDir={sortDir} />
            </div>
            <div className="virt-cell th">Type</div>
            <div className={`virt-cell th ${thClass('price')}`} onClick={() => toggleSort('price')}>
              Price <SortIcon column="price" sortCol={sortCol} sortDir={sortDir} />
            </div>
            <div className={`virt-cell th ${thClass('change')}`} onClick={() => toggleSort('change')}>
              Change % <SortIcon column="change" sortCol={sortCol} sortDir={sortDir} />
            </div>
            <div className="virt-cell th">Trend</div>
            <div className={`virt-cell th ${thClass('rs')}`} onClick={() => toggleSort('rs')}>
              Rel. Strength <SortIcon column="rs" sortCol={sortCol} sortDir={sortDir} />
            </div>
            <div className="virt-cell th">Actions</div>
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
                <th className={thClass('symbol')} onClick={() => toggleSort('symbol')}>
                  Symbol <SortIcon column="symbol" sortCol={sortCol} sortDir={sortDir} />
                </th>
                <th>Type</th>
                <th className={thClass('price')} onClick={() => toggleSort('price')}>
                  Price <SortIcon column="price" sortCol={sortCol} sortDir={sortDir} />
                </th>
                <th className={thClass('change')} onClick={() => toggleSort('change')}>
                  Change % <SortIcon column="change" sortCol={sortCol} sortDir={sortDir} />
                </th>
                <th>Trend</th>
                <th className={thClass('rs')} onClick={() => toggleSort('rs')}>
                  Rel. Strength <SortIcon column="rs" sortCol={sortCol} sortDir={sortDir} />
                </th>
                <th>Actions</th>
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
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
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
}: {
  row: RowData;
  onSelectSymbol: (symbol: string) => void;
  onToggleSymbol: (symbol: string) => void;
  onDeleteSymbol: (symbol: string) => void;
  togglingSymbol: string | null;
  pendingSymbol: string | null;
  pendingAction: 'delete' | null;
}) {
  const rowEnabled = isRowEnabled(row.raw);
  const isPending = pendingSymbol === row.symbol;
  const isDeleting = isPending && pendingAction === 'delete';
  const isToggling = togglingSymbol === row.symbol;

  return (
    <tr
      onClick={() => onSelectSymbol(row.symbol)}
      className={`watchlist-table-row${rowEnabled ? '' : ' row-disabled'}`}
    >
      <td className="td-symbol">
        {row.symbol}
        {!rowEnabled && <span className="row-disabled-badge" title="Disabled">⏸</span>}
      </td>
      <td className="td-type">
        {row.entityType === 'etf' ? (
          <span className="entity-tag entity-tag-etf">ETF</span>
        ) : (
          <span className="entity-tag entity-tag-stock">Stock</span>
        )}
      </td>
      <td className="td-price">
        {row.price != null ? `$${fmtPrice(row.price)}` : '—'}
      </td>
      <td className={`td-change ${changeCellClass(row.changePct)}`}>
        {row.changePct != null
          ? `${row.changePct > 0 ? '+' : ''}${fmt(row.changePct)}%`
          : '—'}
      </td>
      <td><TrendColumn trendSignals={row.trendSignals} /></td>
      <td className={`td-rs ${rsCellClass(row.rs)}`}>{rsCellLabel(row.rs)}</td>
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
    </tr>
  );
});

export default WatchlistTable;
