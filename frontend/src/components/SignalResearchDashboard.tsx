import React, { useCallback, useEffect, useMemo, useState } from 'react';
import api, { HistoricalSignal, SignalResearchPage, SignalScopeMode, Watchlist } from '../services/api';
import { TIMEFRAME_LABELS } from '../utils/timeframeUtils';
import {
  directionalOutcome,
  isDirectionalSignal,
  isDirectionalWin,
  isSignalOutcomeComplete,
} from '../utils/signalOutcomes';

const RESEARCH_TIMEFRAMES = ['all', '1m', '5m', '15m', '1h', '1d'] as const;

type MetricRow = {
  label: string;
  count: number;
  winRate: number | null;
  avg5: number | null;
  avg10: number | null;
};

function fmt(value: number | null | undefined, suffix = ''): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(2)}${suffix}`;
}

function metricRows(signals: HistoricalSignal[], getLabel: (signal: HistoricalSignal) => string): MetricRow[] {
  const groups = new Map<string, HistoricalSignal[]>();
  signals.forEach((signal) => {
    const label = getLabel(signal);
    const group = groups.get(label) || [];
    group.push(signal);
    groups.set(label, group);
  });
  return Array.from(groups.entries())
    .map(([label, group]) => {
      const completed = group.filter(isSignalOutcomeComplete);
      const directional = completed.filter(isDirectionalSignal);
      const average = (key: 'return_5b' | 'return_10b') => {
        const values = directional
          .map((signal) => directionalOutcome(signal, key))
          .filter((value): value is number => value != null && Number.isFinite(value));
        return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
      };
      return {
        label,
        count: directional.length,
        winRate: directional.length ? directional.filter(isDirectionalWin).length / directional.length * 100 : null,
        avg5: average('return_5b'),
        avg10: average('return_10b'),
      };
    })
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

function downloadCsv(csv: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'marketlens-signal-research.csv';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function SignalResearchDashboard() {
  const [signals, setSignals] = useState<HistoricalSignal[]>([]);
  const [page, setPage] = useState<SignalResearchPage | null>(null);
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [scope, setScope] = useState<SignalScopeMode>('all_active');
  const [watchlistId, setWatchlistId] = useState<number | null>(null);
  const [timeframe, setTimeframe] = useState<string>('all');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const validDateRange = !fromDate || !toDate || fromDate <= toDate;

  const query = useMemo(() => ({
    timeframe: timeframe === 'all' ? undefined : timeframe,
    startDate: fromDate || undefined,
    endDate: toDate || undefined,
    scope,
    watchlistId: scope === 'watchlist' ? watchlistId ?? undefined : undefined,
    completedOnly: true,
    limit: 250,
  }), [fromDate, scope, timeframe, toDate, watchlistId]);

  const loadSignals = useCallback(async (offset = 0) => {
    if (!validDateRange || (scope === 'watchlist' && watchlistId == null)) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.getSignalResearch({ ...query, offset });
      setPage(result);
      setSignals(result.records);
    } catch (err: any) {
      setSignals([]);
      setPage(null);
      setError(err?.message || 'Unable to load signal research data.');
    } finally {
      setLoading(false);
    }
  }, [query, scope, validDateRange, watchlistId]);

  useEffect(() => {
    let active = true;
    api.getWatchlists()
      .then((rows) => { if (active) setWatchlists(rows.filter((row) => row.is_active)); })
      .catch(() => { if (active) setWatchlists([]); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    void loadSignals(0);
  }, [loadSignals]);

  const completedSignals = useMemo(
    () => signals.filter(isSignalOutcomeComplete),
    [signals],
  );
  const directionalSignals = completedSignals.filter(isDirectionalSignal);
  const overallWinRate = directionalSignals.length
    ? directionalSignals.filter(isDirectionalWin).length / directionalSignals.length * 100
    : null;
  const averageReturn = completedSignals.length
    ? (() => {
      const values = directionalSignals
        .map((signal) => directionalOutcome(signal, 'return_5b'))
        .filter((value): value is number => value != null);
      return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    })()
    : null;
  const regimeRows = useMemo(() => metricRows(signals, (signal) => signal.market_regime || 'unknown'), [signals]);
  const trendRows = useMemo(() => metricRows(signals, (signal) => signal.trend_state || 'unknown'), [signals]);
  const timeframeRows = useMemo(() => metricRows(signals, (signal) => signal.timeframe), [signals]);
  const scopeLabel = page
    ? page.scope.mode === 'all_stored'
      ? 'All stored signals'
      : `${page.scope.watchlist_names.join(', ') || 'No active watchlists'} · ${page.scope.symbols.length} enabled symbols`
    : 'Resolving scope…';

  const exportResearch = async () => {
    if (!validDateRange || (scope === 'watchlist' && watchlistId == null)) return;
    setExporting(true);
    setError(null);
    try {
      downloadCsv(await api.exportSignalResearch(query));
    } catch (err: any) {
      setError(err?.message || 'Unable to export the scoped signal research data.');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="card signal-research-card">
      <div className="research-heading">
        <div>
          <h2>Signal Research Dashboard</h2>
          <p className="label">Direction-adjusted completed 5-bar outcomes. Scope and date coverage are shown below; page metrics never silently stand in for a larger dataset.</p>
        </div>
        <button className="btn" onClick={() => void exportResearch()} disabled={!page?.total || exporting}>{exporting ? 'Exporting…' : 'Export CSV'}</button>
      </div>

      <div className="research-controls">
        <label>
          <span>Scope</span>
          <select
            value={scope === 'watchlist' ? `watchlist:${watchlistId ?? ''}` : scope}
            onChange={(event) => {
              const value = event.target.value;
              if (value.startsWith('watchlist:')) {
                setScope('watchlist');
                setWatchlistId(Number(value.slice('watchlist:'.length)) || null);
              } else {
                setScope(value as SignalScopeMode);
                setWatchlistId(null);
              }
            }}
            aria-label="Research scope"
          >
            <option value="all_active">All active watchlists</option>
            {watchlists.map((watchlist) => <option key={watchlist.id} value={`watchlist:${watchlist.id}`}>{watchlist.name}</option>)}
            <option value="all_stored">All stored signals (offline research)</option>
          </select>
        </label>
        <label>
          <span>Timeframe</span>
          <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)} aria-label="Research timeframe">
            {RESEARCH_TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf === 'all' ? 'All timeframes' : TIMEFRAME_LABELS[tf] || tf}</option>)}
          </select>
        </label>
        <label><span>From</span><input type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} aria-label="Research start date" /></label>
        <label><span>To</span><input type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} aria-label="Research end date" /></label>
        <button className="btn btn-primary" onClick={() => void loadSignals(0)} disabled={loading || !validDateRange}>{loading ? 'Loading…' : 'Refresh'}</button>
        {(fromDate || toDate) && <button className="btn" onClick={() => { setFromDate(''); setToDate(''); }}>Clear Dates</button>}
      </div>

      {error && <div className="error-text research-status">{error}</div>}
      {!error && !validDateRange && <div className="error-text research-status">The start date must be on or before the end date.</div>}
      {!error && validDateRange && page && (
        <p className="label research-status" aria-label="Research coverage">
          Scope: {scopeLabel}. Showing {page.total ? `${page.offset + 1}–${page.offset + signals.length} of ${page.total}` : '0 of 0'} complete matching records; metrics below apply to this page. CSV exports all {page.total.toLocaleString()} matching records.
        </p>
      )}
      {!error && validDateRange && !loading && page && !signals.length && <div className="empty-state research-empty">No complete historical signals match this scope and date range.</div>}
      {!error && validDateRange && signals.length > 0 && (
        <>
          <div className="research-metrics">
            <div><small>Records on this page</small><strong>{signals.length}</strong></div>
            <div><small>Complete outcomes</small><strong>{completedSignals.length}</strong></div>
            <div><small>Directional win rate</small><strong>{overallWinRate == null ? '—' : `${overallWinRate.toFixed(1)}%`}</strong></div>
            <div><small>Avg signal 5-bar return</small><strong>{fmt(averageReturn, '%')}</strong></div>
          </div>
          <p className="label research-status">No cumulative-return chart is shown: overlapping signals across symbols and horizons are not a strategy equity curve. Use a dedicated backtest with execution, allocation, and cost assumptions for that question.</p>

          <div className="research-tables">
            <ResearchTable title="By Market Regime" rows={regimeRows} />
            <ResearchTable title="By Trend State" rows={trendRows} />
            <ResearchTable title="By Timeframe" rows={timeframeRows} />
          </div>
          {page && (page.offset > 0 || page.has_more) && (
            <div className="research-controls">
              <button className="btn" onClick={() => void loadSignals(Math.max(0, page.offset - page.limit))} disabled={loading || page.offset === 0}>Previous page</button>
              <button className="btn" onClick={() => void loadSignals(page.offset + page.limit)} disabled={loading || !page.has_more}>Next page</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ResearchTable({ title, rows }: { title: string; rows: MetricRow[] }) {
  return (
    <div className="research-table-wrap">
      <h3>{title}</h3>
      {rows.length === 0 ? <p className="empty-state">No completed outcomes.</p> : (
        <div className="data-grid">
          <table className="regime-table research-table">
            <thead><tr><th>Group</th><th>Directional calls</th><th>Win rate</th><th>Avg signal 5b</th><th>Avg signal 10b</th></tr></thead>
            <tbody>{rows.map((row) => <tr key={row.label}>
              <td>{row.label}</td><td>{row.count}</td><td>{row.winRate == null ? '—' : `${row.winRate.toFixed(1)}%`}</td>
              <td className={row.avg5 != null && row.avg5 < 0 ? 'outcome-negative' : 'outcome-positive'}>{fmt(row.avg5, '%')}</td>
              <td className={row.avg10 != null && row.avg10 < 0 ? 'outcome-negative' : 'outcome-positive'}>{fmt(row.avg10, '%')}</td>
            </tr>)}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default SignalResearchDashboard;
