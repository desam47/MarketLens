import React, { useCallback, useEffect, useMemo, useState } from 'react';
import api, { HistoricalSignal } from '../services/api';
import { TIMEFRAME_LABELS } from '../utils/timeframeUtils';

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

function isDirectionalWin(signal: HistoricalSignal): boolean {
  if (signal.return_5b == null) return false;
  if (signal.trend_state === 'bullish') return signal.return_5b > 0;
  if (signal.trend_state === 'bearish') return signal.return_5b < 0;
  return false;
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
      const completed = group.filter((signal) => signal.return_5b != null);
      const directional = completed.filter((signal) => signal.trend_state === 'bullish' || signal.trend_state === 'bearish');
      const average = (key: 'return_5b' | 'return_10b') => {
        const values = completed.map((signal) => signal[key]).filter((value): value is number => value != null && Number.isFinite(value));
        return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
      };
      return {
        label,
        count: completed.length,
        winRate: directional.length ? directional.filter(isDirectionalWin).length / directional.length * 100 : null,
        avg5: average('return_5b'),
        avg10: average('return_10b'),
      };
    })
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

function downloadCsv(signals: HistoricalSignal[]): void {
  const header = ['timestamp', 'symbol', 'timeframe', 'trend_state', 'market_regime', 'return_5b', 'return_10b', 'return_20b', 'mfe', 'mae'];
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
    () => filteredSignals.filter((signal) => signal.return_5b != null),
    [filteredSignals],
  );
  const directionalSignals = completedSignals.filter((signal) => signal.trend_state === 'bullish' || signal.trend_state === 'bearish');
  const overallWinRate = directionalSignals.length
    ? directionalSignals.filter(isDirectionalWin).length / directionalSignals.length * 100
    : null;
  const averageReturn = completedSignals.length
    ? completedSignals.reduce((sum, signal) => sum + (signal.return_5b || 0), 0) / completedSignals.length
    : null;
  const regimeRows = useMemo(() => metricRows(filteredSignals, (signal) => signal.market_regime || 'unknown'), [filteredSignals]);
  const trendRows = useMemo(() => metricRows(filteredSignals, (signal) => signal.trend_state || 'unknown'), [filteredSignals]);
  const timeframeRows = useMemo(() => metricRows(filteredSignals, (signal) => signal.timeframe), [filteredSignals]);
  const equityPoints = useMemo(() => {
    const ordered = [...completedSignals].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
    let cumulative = 0;
    return ordered.map((signal) => {
      cumulative += signal.return_5b || 0;
      return cumulative;
    });
  }, [completedSignals]);
  const chart = useMemo(() => {
    if (equityPoints.length < 2) return null;
    const sampled = equityPoints.length > 120
      ? equityPoints.filter((_, index) => index % Math.ceil(equityPoints.length / 120) === 0 || index === equityPoints.length - 1)
      : equityPoints;
    const min = Math.min(0, ...sampled);
    const max = Math.max(0, ...sampled);
    const range = max - min || 1;
    const points = sampled.map((value, index) => `${(index / Math.max(1, sampled.length - 1)) * 100},${100 - ((value - min) / range) * 90 - 5}`).join(' ');
    const zeroY = 100 - ((0 - min) / range) * 90 - 5;
    return { points, zeroY };
  }, [equityPoints]);

  return (
    <div className="card signal-research-card">
      <div className="research-heading">
        <div>
          <h2>Signal Research Dashboard</h2>
          <p className="label">Equal-weighted 5-bar signal outcomes across the active watchlist. Use this view to compare regimes and trend states before trusting a pattern.</p>
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
            <div><small>Completed</small><strong>{completedSignals.length}</strong></div>
            <div><small>Directional win rate</small><strong>{overallWinRate == null ? '—' : `${overallWinRate.toFixed(1)}%`}</strong></div>
            <div><small>Avg 5-bar return</small><strong>{fmt(averageReturn, '%')}</strong></div>
          </div>

          {chart ? (
            <div className="research-chart-wrap">
              <div className="research-chart-title">Cumulative 5-bar signal return</div>
              <svg className="research-chart" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Cumulative 5-bar signal return chart">
                <line x1="0" x2="100" y1={chart.zeroY} y2={chart.zeroY} className="research-zero-line" />
                <polyline points={chart.points} className="research-equity-line" />
              </svg>
              <div className="research-chart-axis"><span>0%</span><span>{fmt(equityPoints[equityPoints.length - 1], '%')}</span></div>
            </div>
          ) : <div className="empty-state research-empty">At least two completed outcomes are needed to draw the equity curve.</div>}

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
            <thead><tr><th>Group</th><th>Count</th><th>Win rate</th><th>Avg 5b</th><th>Avg 10b</th></tr></thead>
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
