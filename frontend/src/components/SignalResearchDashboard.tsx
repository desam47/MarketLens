import React, { useCallback, useEffect, useMemo, useState } from 'react';
import api, { SignalResearchMetrics, SignalResearchSummary, SignalScopeMode, Watchlist } from '../services/api';
import { DEFAULT_TIMEFRAME, TIMEFRAMES, TIMEFRAME_LABELS } from '../utils/timeframeUtils';

const ALL_TIMEFRAMES = 'all';

function fmt(value: number | null | undefined, suffix = ''): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(2)}${suffix}`;
}

function fmtRate(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${(value * 100).toFixed(1)}%`;
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
  const [summary, setSummary] = useState<SignalResearchSummary | null>(null);
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [scope, setScope] = useState<SignalScopeMode>('all_active');
  const [watchlistId, setWatchlistId] = useState<number | null>(null);
  // One timeframe by default: five bars of 1m and five bars of 1d are different horizons.
  const [timeframe, setTimeframe] = useState<string>(DEFAULT_TIMEFRAME);
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const validDateRange = !fromDate || !toDate || fromDate <= toDate;

  const query = useMemo(() => ({
    timeframe: timeframe === ALL_TIMEFRAMES ? undefined : timeframe,
    startDate: fromDate || undefined,
    endDate: toDate || undefined,
    scope,
    watchlistId: scope === 'watchlist' ? watchlistId ?? undefined : undefined,
    completedOnly: true,
  }), [fromDate, scope, timeframe, toDate, watchlistId]);

  const loadSummary = useCallback(async () => {
    if (!validDateRange || (scope === 'watchlist' && watchlistId == null)) return;
    setLoading(true);
    setError(null);
    try {
      setSummary(await api.getSignalResearchSummary(query));
    } catch (err: any) {
      setSummary(null);
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
    void loadSummary();
  }, [loadSummary]);

  const performance = summary?.performance ?? null;
  const scopeLabel = summary
    ? summary.scope.mode === 'all_stored'
      ? 'All stored signals'
      : `${summary.scope.watchlist_names.join(', ') || 'No active watchlists'} · ${summary.scope.symbols.length} enabled symbols`
    : 'Resolving scope…';
  const regimeCoverage = summary?.regime_coverage;
  const regimeShare = regimeCoverage && regimeCoverage.complete
    ? (regimeCoverage.with_regime / regimeCoverage.complete) * 100
    : null;

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
          <p className="label">Direction-adjusted outcomes of completed bullish and bearish calls, over every record that matches the filters. Performance is shown one timeframe at a time.</p>
        </div>
        <button className="btn" onClick={() => void exportResearch()} disabled={!summary?.complete || exporting}>{exporting ? 'Exporting…' : 'Export CSV'}</button>
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
            {TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{TIMEFRAME_LABELS[tf] || tf}</option>)}
            <option value={ALL_TIMEFRAMES}>All timeframes (coverage only)</option>
          </select>
        </label>
        <label><span>From</span><input type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} aria-label="Research start date" /></label>
        <label><span>To</span><input type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} aria-label="Research end date" /></label>
        <button className="btn btn-primary" onClick={() => void loadSummary()} disabled={loading || !validDateRange}>{loading ? 'Loading…' : 'Refresh'}</button>
        {(fromDate || toDate) && <button className="btn" onClick={() => { setFromDate(''); setToDate(''); }}>Clear Dates</button>}
      </div>

      {error && <div className="error-text research-status">{error}</div>}
      {!error && !validDateRange && <div className="error-text research-status">The start date must be on or before the end date.</div>}
      {!error && validDateRange && summary && (
        <p className="label research-status" aria-label="Research coverage">
          Scope: {scopeLabel}. {summary.complete.toLocaleString()} complete outcomes of {summary.recorded.toLocaleString()} recorded signals; metrics cover all of them. CSV exports the same {summary.complete.toLocaleString()} records.
        </p>
      )}
      {!error && validDateRange && !loading && summary && !summary.complete && (
        <div className="empty-state research-empty">No complete historical signals match this scope and date range.</div>
      )}
      {!error && validDateRange && summary && summary.complete > 0 && (
        <>
          {performance ? (
            <>
              <div className="research-metrics">
                <div><small>Complete outcomes</small><strong>{performance.complete.toLocaleString()}</strong></div>
                <div><small>Directional calls</small><strong>{performance.directional.toLocaleString()}</strong></div>
                <div><small>Directional win rate</small><strong>{fmtRate(performance.win_rate)}</strong></div>
                <div><small>Avg signal 5-bar return</small><strong>{fmt(performance.avg_signal_return_5b, '%')}</strong></div>
              </div>
              <p className="label research-status">No cumulative-return chart is shown: overlapping signals across symbols are not a strategy equity curve. Use a dedicated backtest with execution, allocation, and cost assumptions for that question.</p>
              <p className="label research-status" aria-label="Regime coverage">
                Regime is a live snapshot: it is recorded only for signals captured as their bar closed, because the regime engine knows only the present. {regimeCoverage?.with_regime.toLocaleString()} of {regimeCoverage?.complete.toLocaleString()} complete outcomes ({fmt(regimeShare, '%')}) carry one; the rest are listed as “not recorded”.
              </p>
            </>
          ) : (
            <p className="label research-status" aria-label="Performance note">{summary.performance_note}</p>
          )}

          <div className="research-tables">
            {performance && <ResearchTable title="By Regime at Recording" rows={performance.by_regime} />}
            {performance && <ResearchTable title="By Trend State" rows={performance.by_trend} />}
            <CoverageTable summary={summary} />
          </div>
        </>
      )}
    </div>
  );
}

function ResearchTable({ title, rows }: { title: string; rows: SignalResearchMetrics[] }) {
  return (
    <div className="research-table-wrap">
      <h3>{title}</h3>
      {rows.length === 0 ? <p className="empty-state">No completed outcomes.</p> : (
        <div className="data-grid">
          <table className="regime-table research-table">
            <thead><tr><th>Group</th><th>Directional calls</th><th>Win rate</th><th>Avg signal 5b</th><th>Avg signal 10b</th></tr></thead>
            <tbody>{rows.map((row) => <tr key={row.label}>
              <td>{row.label}</td><td>{row.directional}</td><td>{fmtRate(row.win_rate)}</td>
              <td className={row.avg_signal_return_5b != null && row.avg_signal_return_5b < 0 ? 'outcome-negative' : 'outcome-positive'}>{fmt(row.avg_signal_return_5b, '%')}</td>
              <td className={row.avg_signal_return_10b != null && row.avg_signal_return_10b < 0 ? 'outcome-negative' : 'outcome-positive'}>{fmt(row.avg_signal_return_10b, '%')}</td>
            </tr>)}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CoverageTable({ summary }: { summary: SignalResearchSummary }) {
  return (
    <div className="research-table-wrap">
      <h3>Coverage by Timeframe</h3>
      <div className="data-grid">
        <table className="regime-table research-table" aria-label="Coverage by timeframe">
          <thead><tr><th>Timeframe</th><th>Recorded</th><th>Complete</th></tr></thead>
          <tbody>{summary.timeframe_coverage.map((row) => <tr key={row.timeframe}>
            <td>{TIMEFRAME_LABELS[row.timeframe] || row.timeframe}</td><td>{row.recorded.toLocaleString()}</td><td>{row.complete.toLocaleString()}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

export default SignalResearchDashboard;
