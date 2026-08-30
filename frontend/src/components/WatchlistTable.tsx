import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { FixedSizeList, ListChildComponentProps } from 'react-window';
import api, { WatchlistScanResult, WatchlistSymbol, RelativeStrengthData, RelativeStrengthSignal } from '../services/api';

interface WatchlistTableProps {
  watchlistId: number;
  onSelectSymbol: (symbol: string) => void;
  sortColumn?: 'symbol' | 'price' | 'score' | 'confidence' | 'rs';
  sortDirection?: 'asc' | 'desc';
}

interface RowData {
  symbol: string;
  price: number | null;
  change: number | null;
  changePct: number | null;
  score: number;
  confidence: number;
  trendDir: 'bullish' | 'bearish' | 'neutral';
  rs: RelativeStrengthSignal | null;
  raw: WatchlistScanResult;
}

// Threshold: above this row count, switch to a virtualized list. Below, a
// regular table renders more cleanly (sticky header, full column widths).
const VIRT_THRESHOLD = 30;
const VIRT_ROW_HEIGHT = 44;
const VIRT_HEIGHT = 480;

// Derive trend direction from signals list.
function deriveDirection(signals: string[]): 'bullish' | 'bearish' | 'neutral' {
  if (signals.length === 0) return 'neutral';
  const bullishSignals = [
    'daily_bullish', 'mtf_bullish', 'breakout', 'strong_trend',
    'trend_strengthens', 'full_alignment', 'bullish_divergence',
    'trend_crosses_above_70',
  ];
  const bearishSignals = [
    'daily_bearish', 'mtf_bearish', 'breakdown', 'weak_trend',
    'trend_weakens', 'timeframe_conflict', 'bearish_divergence',
    'trend_crosses_below_70',
  ];
  const bullCount = signals.filter(s => bullishSignals.includes(s)).length;
  const bearCount = signals.filter(s => bearishSignals.includes(s)).length;
  if (bullCount > bearCount) return 'bullish';
  if (bearCount > bullCount) return 'bearish';
  return 'neutral';
}

// Estimate confidence from score magnitude (0-100).
function estimateConfidence(score: number): number {
  const abs = Math.abs(score);
  if (abs >= 70) return 90;
  if (abs >= 50) return 75;
  if (abs >= 30) return 60;
  if (abs >= 15) return 45;
  return 30;
}

const TREND_ICONS: Record<string, string> = {
  bullish: '🐂',
  bearish: '🐻',
  neutral: '➡',
};

const TREND_LABELS: Record<string, string> = {
  bullish: 'Uptrend',
  bearish: 'Downtrend',
  neutral: 'Neutral',
};

const RS_CLASS_LABELS: Record<string, string> = {
  strong_outperformer: 'Strong Outperformer',
  outperformer: 'Outperformer',
  inline: 'Inline',
  underperformer: 'Underperformer',
  strong_underperformer: 'Strong Underperformer',
  unknown: '—',
};

function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return '—';
  return n.toFixed(decimals);
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
  onToggleSymbol: (symbol: string, currentEnabled: boolean) => void;
  onDeleteSymbol: (symbol: string) => void;
  pendingSymbol: string | null;
  pendingAction: 'toggle' | 'delete' | null;
}>) {
  const { rows, onSelectSymbol, onToggleSymbol, onDeleteSymbol,
    pendingSymbol, pendingAction } = data;
  const row = rows[index];

  const isPending = pendingSymbol === row.symbol;
  const isDeleting = isPending && pendingAction === 'delete';
  const isToggling = isPending && pendingAction === 'toggle';
  const rowEnabled = row.raw.is_enabled !== false;

  const priceColor = row.changePct == null
    ? ''
    : row.changePct > 0
      ? 'price-up'
      : row.changePct < 0
        ? 'price-down'
        : '';
  const rsCls = !row.rs
    ? ''
    : (() => {
        const c = row.rs.classification ?? 'unknown';
        if (c === 'strong_outperformer' || c === 'outperformer') return 'rs-bullish';
        if (c === 'strong_underperformer' || c === 'underperformer') return 'rs-bearish';
        return '';
      })();
  const rsLbl = !row.rs
    ? '—'
    : `${fmt(row.rs.rs_pct)}% ${RS_CLASS_LABELS[row.rs.classification] ?? ''}`;

  return (
    <div
      style={style}
      className="virt-row watchlist-table-row"
      onClick={() => onSelectSymbol(row.symbol)}
    >
      <div className="virt-cell td-symbol">{row.symbol}</div>
      <div className={`virt-cell td-price ${priceColor}`}>
        {row.price != null ? `$${fmt(row.price)}` : '—'}
        {row.changePct != null && (
          <span className="price-chg">
            {row.changePct > 0 ? '+' : ''}{fmt(row.changePct)}%
          </span>
        )}
      </div>
      <div className="virt-cell">
        <span className={`trend-badge trend-${row.trendDir}`}>
          {TREND_ICONS[row.trendDir]} {TREND_LABELS[row.trendDir]}
        </span>
      </div>
      <div className={`virt-cell td-score ${row.score > 0 ? 'score-pos' : row.score < 0 ? 'score-neg' : ''}`}>
        {row.score > 0 ? '+' : ''}{fmt(row.score)}
      </div>
      <div className="virt-cell td-confidence">
        <div className="conf-bar">
          <div className="conf-fill" style={{ width: `${row.confidence}%` }} />
        </div>
        <span className="conf-label">{fmt(row.confidence, 0)}%</span>
      </div>
      <div className={`virt-cell td-rs ${rsCls}`}>{rsLbl}</div>
      <div
        className="virt-cell td-actions"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          className="row-action-btn"
          title={rowEnabled ? 'Disable symbol' : 'Enable symbol'}
          disabled={isToggling}
          onClick={() => onToggleSymbol(row.symbol, rowEnabled)}
        >
          {rowEnabled ? '⏸' : '▶'}
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
  );
});

export function WatchlistTable({
  watchlistId,
  onSelectSymbol,
  sortColumn = 'score',
  sortDirection = 'desc',
}: WatchlistTableProps) {
  const [rows, setRows] = useState<RowData[]>([]);
  const [scanTimestamp, setScanTimestamp] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortCol, setSortCol] = useState(sortColumn);
  const [sortDir, setSortDir] = useState(sortDirection);
  const [pendingSymbol, setPendingSymbol] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<'toggle' | 'delete' | null>(null);
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
        price: r.quote?.price ?? null,
        change: r.quote ? (r.quote as any).change ?? null : null,
        changePct: r.quote ? (r.quote as any).changePercent ?? null : null,
        score: r.total_score,
        confidence: estimateConfidence(r.total_score),
        trendDir: deriveDirection(r.signals),
        rs: null,
        raw: r,
      }));

      // Fetch RS for each symbol in parallel (batched, non-blocking).
      const rsResults = await Promise.allSettled(
        baseRows.map(row =>
          api.getRelativeStrength(row.symbol).catch(() => null) as Promise<RelativeStrengthData | null>
        ),
      );

      const finalRows = baseRows.map((row, i) => {
        const result = rsResults[i];
        if (result?.status === 'fulfilled' && result.value) {
          const data = result.value as RelativeStrengthData;
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

  // --- Per-symbol actions -----------------------------------------------

  const handleToggleSymbol = useCallback(async (symbol: string, currentEnabled: boolean) => {
    setPendingSymbol(symbol);
    setPendingAction('toggle');
    try {
      if (currentEnabled) {
        await api.disableSymbol(watchlistId, symbol);
      } else {
        await api.enableSymbol(watchlistId, symbol);
      }
      await fetchData(true);
    } catch (err: any) {
      setError(err?.message || 'Failed to update symbol');
    } finally {
      setPendingSymbol(null);
      setPendingAction(null);
    }
  }, [watchlistId, fetchData]);

  const handleDeleteSymbol = useCallback(async (symbol: string) => {
    if (!window.confirm(`Remove ${symbol} from this watchlist?`)) return;
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
        case 'score': cmp = a.score - b.score; break;
        case 'confidence': cmp = a.confidence - b.confidence; break;
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
      pendingSymbol,
      pendingAction,
    }),
    [sorted, onSelectSymbol, handleToggleSymbol, handleDeleteSymbol, pendingSymbol, pendingAction],
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
            Scanned {new Date(scanTimestamp).toLocaleTimeString()}
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
            <div className={`virt-cell th ${thClass('price')}`} onClick={() => toggleSort('price')}>
              Price <SortIcon column="price" sortCol={sortCol} sortDir={sortDir} />
            </div>
            <div className="virt-cell th">Trend</div>
            <div className={`virt-cell th ${thClass('score')}`} onClick={() => toggleSort('score')}>
              Score <SortIcon column="score" sortCol={sortCol} sortDir={sortDir} />
            </div>
            <div className={`virt-cell th ${thClass('confidence')}`} onClick={() => toggleSort('confidence')}>
              Conf <SortIcon column="confidence" sortCol={sortCol} sortDir={sortDir} />
            </div>
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
                <th className={thClass('price')} onClick={() => toggleSort('price')}>
                  Price <SortIcon column="price" sortCol={sortCol} sortDir={sortDir} />
                </th>
                <th>Trend</th>
                <th className={thClass('score')} onClick={() => toggleSort('score')}>
                  Score <SortIcon column="score" sortCol={sortCol} sortDir={sortDir} />
                </th>
                <th className={thClass('confidence')} onClick={() => toggleSort('confidence')}>
                  Conf <SortIcon column="confidence" sortCol={sortCol} sortDir={sortDir} />
                </th>
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
  pendingSymbol,
  pendingAction,
}: {
  row: RowData;
  onSelectSymbol: (symbol: string) => void;
  onToggleSymbol: (symbol: string, currentEnabled: boolean) => void;
  onDeleteSymbol: (symbol: string) => void;
  pendingSymbol: string | null;
  pendingAction: 'toggle' | 'delete' | null;
}) {
  const priceColor = row.changePct == null
    ? ''
    : row.changePct > 0
      ? 'price-up'
      : row.changePct < 0
        ? 'price-down'
        : '';
  const rsCls = !row.rs
    ? ''
    : (() => {
        const c = row.rs.classification ?? 'unknown';
        if (c === 'strong_outperformer' || c === 'outperformer') return 'rs-bullish';
        if (c === 'strong_underperformer' || c === 'underperformer') return 'rs-bearish';
        return '';
      })();
  const rsLbl = !row.rs
    ? '—'
    : `${fmt(row.rs.rs_pct)}% ${RS_CLASS_LABELS[row.rs.classification] ?? ''}`;

  const rowEnabled = row.raw.is_enabled !== false;
  const isPending = pendingSymbol === row.symbol;
  const isToggling = isPending && pendingAction === 'toggle';
  const isDeleting = isPending && pendingAction === 'delete';

  return (
    <tr
      onClick={() => onSelectSymbol(row.symbol)}
      className={`watchlist-table-row${rowEnabled ? '' : ' row-disabled'}`}
    >
      <td className="td-symbol">
        {row.symbol}
        {!rowEnabled && <span className="row-disabled-badge" title="Disabled">⏸</span>}
      </td>
      <td className={`td-price ${priceColor}`}>
        {row.price != null ? `$${fmt(row.price)}` : '—'}
        {row.changePct != null && (
          <span className="price-chg">
            {row.changePct > 0 ? '+' : ''}{fmt(row.changePct)}%
          </span>
        )}
      </td>
      <td>
        <span className={`trend-badge trend-${row.trendDir}`}>
          {TREND_ICONS[row.trendDir]} {TREND_LABELS[row.trendDir]}
        </span>
      </td>
      <td className={`td-score ${row.score > 0 ? 'score-pos' : row.score < 0 ? 'score-neg' : ''}`}>
        {row.score > 0 ? '+' : ''}{fmt(row.score)}
      </td>
      <td className="td-confidence">
        <div className="conf-bar">
          <div className="conf-fill" style={{ width: `${row.confidence}%` }} />
        </div>
        <span className="conf-label">{fmt(row.confidence, 0)}%</span>
      </td>
      <td className={`td-rs ${rsCls}`}>{rsLbl}</td>
      <td className="td-actions" onClick={(e) => e.stopPropagation()}>
        <button
          className="row-action-btn"
          title={rowEnabled ? 'Disable symbol' : 'Enable symbol'}
          disabled={isToggling}
          onClick={() => onToggleSymbol(row.symbol, rowEnabled)}
        >
          {rowEnabled ? '⏸' : '▶'}
        </button>
        <button
          className="row-action-btn row-action-danger"
          title="Remove from watchlist"
          disabled={isDeleting}
          onClick={() => onDeleteSymbol(row.symbol)}
        >
          {isDeleting ? '…' : '×'}
        </button>
      </td>
    </tr>
  );
});

export default WatchlistTable;
