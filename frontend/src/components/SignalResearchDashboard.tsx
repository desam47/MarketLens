import React, { useCallback, useEffect, useMemo, useState } from 'react';
import api, { HistoricalSignal } from '../services/api';
import { TIMEFRAME_LABELS } from '../utils/timeframeUtils';
import {
  directionalOutcome,
  isDirectionalSignal,
  isDirectionalWin,
  isSignalOutcomeComplete,
} from '../utils/signalOutcomes';

const RESEARCH_TIMEFRAMES = ['all', '1m', '5m', '15m', '1h', '1d'] as const;
const SIGNAL_LIMIT = 1000;

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

function dateKey(timestamp: string): string {
  return timestamp.slice(0, 10);
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

function downloadCsv(signals: HistoricalSignal[]): void {
  const header = ['timestamp', 'symbol', 'timeframe', 'trend_state', 'market_regime', 'raw_return_5b', 'raw_return_10b', 'raw_return_20b', 'raw_mfe', 'raw_mae', 'signal_return_5b', 'signal_return_10b', 'signal_return_20b', 'favorable_excursion', 'adverse_excursion'];
  const rows = signals.map((signal) => [
    signal.timestamp,
    signal.symbol,
    signal.timeframe,
    signal.trend_state || '',
    signal.market_regime || '',
    signal.return_5b ?? '',
    signal.return_10b ?? '',
    signal.return_20b ?? '',
    signal.mfe ?? '',
    signal.mae ?? '',
    directionalOutcome(signal, 'return_5b') ?? '',
    directionalOutcome(signal, 'return_10b') ?? '',
    directionalOutcome(signal, 'return_20b') ?? '',
    directionalOutcome(signal, 'mfe') ?? '',
    directionalOutcome(signal, 'mae') ?? '',
  ]);
  const csv = [header, ...rows]
    .map((row) => row.map((value) => `"${String(value).replace(/"/g, '""')}"`).join(','))
    .join('\n');
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
  const [timeframe, setTimeframe] = useState<string>('all');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSignals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSignals(await api.listSignals(undefined, undefined, SIGNAL_LIMIT, false));
    } catch (err: any) {
      setSignals([]);
      setError(err?.message || 'Unable to load signal research data.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSignals();
  }, [loadSignals]);

  const validDateRange = !fromDate || !toDate || fromDate <= toDate;
  const filteredSignals = useMemo(() => {
    if (!validDateRange) return [];
    return signals.filter((signal) => {
      const day = dateKey(signal.timestamp);
      return (timeframe === 'all' || signal.timeframe === timeframe)
        && (!fromDate || day >= fromDate)
        && (!toDate || day <= toDate);
    });
  }, [signals, timeframe, fromDate, toDate, validDateRange]);
  const completedSignals = useMemo(
    () => filteredSignals.filter(isSignalOutcomeComplete),
    [filteredSignals],
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
  const regimeRows = useMemo(() => metricRows(filteredSignals, (signal) => signal.market_regime || 'unknown'), [filteredSignals]);
  const trendRows = useMemo(() => metricRows(filteredSignals, (signal) => signal.trend_state || 'unknown'), [filteredSignals]);
  const timeframeRows = useMemo(() => metricRows(filteredSignals, (signal) => signal.timeframe), [filteredSignals]);

  return (
    <div className="card signal-research-card">
      <div className="research-heading">
        <div>
          <h2>Signal Research Dashboard</h2>
          <p className="label">Direction-adjusted completed 5-bar outcomes across the active watchlist. Compare one timeframe at a time before trusting a pattern.</p>
        </div>
        <button className="btn" onClick={() => downloadCsv(completedSignals)} disabled={!completedSignals.length}>Export CSV</button>
      </div>

      <div className="research-controls">
        <label>
          <span>Timeframe</span>
          <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)} aria-label="Research timeframe">
            {RESEARCH_TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{tf === 'all' ? 'All timeframes' : TIMEFRAME_LABELS[tf] || tf}</option>)}
          </select>
        </label>
        <label><span>From</span><input type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} aria-label="Research start date" /></label>
        <label><span>To</span><input type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} aria-label="Research end date" /></label>
        <button className="btn btn-primary" onClick={() => void loadSignals()} disabled={loading}>{loading ? 'Loading…' : 'Refresh'}</button>
        {(fromDate || toDate) && <button className="btn" onClick={() => { setFromDate(''); setToDate(''); }}>Clear Dates</button>}
      </div>

      {error && <div className="error-text research-status">{error}</div>}
      {!error && !validDateRange && <div className="error-text research-status">The start date must be on or before the end date.</div>}
      {!error && validDateRange && !loading && !signals.length && <div className="empty-state research-empty">No historical signals are available for the active watchlist.</div>}
      {!error && validDateRange && signals.length > 0 && (
        <>
          <div className="research-metrics">
            <div><small>Signals</small><strong>{filteredSignals.length}</strong></div>
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
