import React, { useCallback, useEffect, useState } from 'react';
import api, { HistoricalSignal, RegimeCount, RegimePerformance } from '../services/api';

interface HistoricalSignalCardProps {
  /** Optional default symbol to filter on. */
  defaultSymbol?: string;
}

const TIMEFRAMES = ['1d', '4h', '1h', '30m', '15m', '5m', '1m'];

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function fmtDateTime(d: string | null | undefined): string {
  if (!d) return '—';
  // Trim the timezone offset to keep the table compact.
  return d.slice(0, 19).replace('T', ' ');
}

function outcomeCellClass(v: number | null | undefined): string {
  if (v == null) return 'outcome-pending';
  if (v > 0) return 'outcome-positive';
  if (v < 0) return 'outcome-negative';
  return 'outcome-neutral';
}

function regimeBadgeClass(regime: string | null | undefined): string {
  if (!regime) return 'regime-badge regime-unknown';
  if (regime === 'risk_on') return 'regime-badge regime-risk-on';
  if (regime === 'risk_off') return 'regime-badge regime-risk-off';
  if (regime === 'neutral') return 'regime-badge regime-neutral';
  if (regime === 'transition') return 'regime-badge regime-transition';
  return 'regime-badge regime-unknown';
}

export function HistoricalSignalCard({ defaultSymbol = '' }: HistoricalSignalCardProps) {
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [timeframe, setTimeframe] = useState('1d');
  const [signals, setSignals] = useState<HistoricalSignal[]>([]);
  const [regimePerformance, setRegimePerformance] = useState<RegimePerformance[]>([]);
  const [regimeCounts, setRegimeCounts] = useState<RegimeCount[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<{ msg: string; isError: boolean } | null>(null);
  const [busy, setBusy] = useState<{ backfill: boolean; record: boolean; cleanup: boolean }>({
    backfill: false,
    record: false,
    cleanup: false,
  });

  useEffect(() => {
    if (defaultSymbol) setSymbol(defaultSymbol);
  }, [defaultSymbol]);

  const loadSignals = useCallback(async () => {
    setLoading(true);
    setStatus(null);
    try {
      const rows = await api.listSignals(
        symbol.trim().toUpperCase() || undefined,
        timeframe,
        100,
      );
      setSignals(rows);
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Failed to load signals', isError: true });
      setSignals([]);
    } finally {
      setLoading(false);
    }
  }, [symbol, timeframe]);

  const loadResearch = useCallback(async () => {
    try {
      const [perf, counts] = await Promise.all([
        api.getRegimePerformance(),
        api.getSignalCountByRegime(),
      ]);
      setRegimePerformance(perf);
      setRegimeCounts(counts);
    } catch {
      // Best effort: leave previous values.
    }
  }, []);

  useEffect(() => {
    loadSignals();
  }, [loadSignals]);

  useEffect(() => {
    loadResearch();
  }, [loadResearch]);

  const handleBackfill = async () => {
    setBusy((b) => ({ ...b, backfill: true }));
    setStatus(null);
    try {
      const r = await api.backfillOutcomes(200);
      setStatus({ msg: `Backfilled ${r.updated} signal(s).`, isError: false });
      await Promise.all([loadSignals(), loadResearch()]);
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Backfill failed', isError: true });
    } finally {
      setBusy((b) => ({ ...b, backfill: false }));
    }
  };

  const handleRecord = async () => {
    setBusy((b) => ({ ...b, record: true }));
    setStatus(null);
    try {
      const r = await api.recordSignalsNow();
      setStatus({ msg: `Recorded ${r.recorded} signal(s).`, isError: false });
      await loadSignals();
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Record failed', isError: true });
    } finally {
      setBusy((b) => ({ ...b, record: false }));
    }
  };

  const handleCleanup = async () => {
    if (!window.confirm('Delete signals older than 180 days? This cannot be undone.')) {
      return;
    }
    setBusy((b) => ({ ...b, cleanup: true }));
    setStatus(null);
    try {
      const r = await api.deleteOldSignals(180);
      setStatus({ msg: `Deleted ${r.deleted} old signal(s).`, isError: false });
      await loadSignals();
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Cleanup failed', isError: true });
    } finally {
      setBusy((b) => ({ ...b, cleanup: false }));
    }
  };

  const completedSignals = signals.filter((s) => s.return_5b != null).length;
  const pendingSignals = signals.length - completedSignals;

  return (
    <div className="card historical-signal-card">
      <h2>Historical Signal Recording</h2>
      <p className="label" style={{ marginTop: 0 }}>
        Every meaningful trend snapshot is stored with forward 5/10/20-bar
        returns, MFE, and MAE. Outcomes are computed only after 20+ future
        bars exist, so there's no look-ahead bias.
      </p>

      <div className="signal-filters">
        <label>
          <span>Symbol</span>
          <input
            type="text"
            value={symbol}
            placeholder="(all)"
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            maxLength={8}
          />
        </label>
        <label>
          <span>Timeframe</span>
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map((tf) => (
              <option key={tf} value={tf}>{tf}</option>
            ))}
          </select>
        </label>
        <button className="btn btn-primary" onClick={loadSignals} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
        <button className="btn" onClick={handleBackfill} disabled={busy.backfill}>
          {busy.backfill ? 'Backfilling…' : 'Backfill Outcomes'}
        </button>
        <button className="btn" onClick={handleRecord} disabled={busy.record}>
          {busy.record ? 'Recording…' : 'Record Now'}
        </button>
        <button className="btn btn-danger" onClick={handleCleanup} disabled={busy.cleanup}>
          {busy.cleanup ? 'Cleaning…' : 'Delete > 180d'}
        </button>
      </div>

      {status && (
        <div className={status.isError ? 'error-text' : 'info-text'} style={{ marginBottom: 8 }}>
          {status.msg}
        </div>
      )}

      <div className="summary-grid" style={{ marginBottom: 8 }}>
        <div className="data-item">
          <h3>Loaded</h3>
          <div className="value">{signals.length}</div>
        </div>
        <div className="data-item">
          <h3>With Outcomes</h3>
          <div className="value">{completedSignals}</div>
        </div>
        <div className="data-item">
          <h3>Pending</h3>
          <div className="value">{pendingSignals}</div>
        </div>
      </div>

      {signals.length === 0 ? (
        <p className="empty-state">
          No signals for this filter. Try "Record Now" or wait for the
          ingestion loop to capture one.
        </p>
      ) : (
        <div className="data-grid">
          <table className="signals-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>TF</th>
                <th>Timestamp</th>
                <th>Price</th>
                <th>Trend</th>
                <th>Score</th>
                <th>Regime</th>
                <th>5b</th>
                <th>10b</th>
                <th>20b</th>
                <th>MFE</th>
                <th>MAE</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => (
                <tr key={s.id}>
                  <td><strong>{s.symbol}</strong></td>
                  <td>{s.timeframe}</td>
                  <td>{fmtDateTime(s.timestamp)}</td>
                  <td>{s.price == null ? '—' : s.price.toFixed(2)}</td>
                  <td>{s.trend_state || '—'}</td>
                  <td>{s.trend_score == null ? '—' : s.trend_score.toFixed(1)}</td>
                  <td>
                    <span className={regimeBadgeClass(s.market_regime)}>
                      {s.market_regime || '—'}
                    </span>
                  </td>
                  <td className={outcomeCellClass(s.return_5b)}>{fmtPct(s.return_5b)}</td>
                  <td className={outcomeCellClass(s.return_10b)}>{fmtPct(s.return_10b)}</td>
                  <td className={outcomeCellClass(s.return_20b)}>{fmtPct(s.return_20b)}</td>
                  <td className={outcomeCellClass(s.mfe)}>{fmtPct(s.mfe)}</td>
                  <td className={outcomeCellClass(s.mae)}>{fmtPct(s.mae)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3 style={{ marginTop: 18, color: '#34495e' }}>Performance by Market Regime</h3>
      <p className="label" style={{ marginTop: 0 }}>
        Average forward returns, MFE, and MAE for every signal that has
        outcomes computed. Risk-on / risk-off / neutral / transition come
        from the market-context engine at signal time.
      </p>
      {regimePerformance.length === 0 ? (
        <p className="empty-state">
          No regime-performance data yet. Backfill some outcomes first.
        </p>
      ) : (
        <div className="data-grid">
          <table className="regime-table">
            <thead>
              <tr>
                <th>Regime</th>
                <th>Count</th>
                <th>Avg 5b</th>
                <th>Avg 10b</th>
                <th>Avg 20b</th>
                <th>Avg MFE</th>
                <th>Avg MAE</th>
              </tr>
            </thead>
            <tbody>
              {regimePerformance.map((r) => (
                <tr key={r.regime}>
                  <td>
                    <span className={regimeBadgeClass(r.regime)}>{r.regime}</span>
                  </td>
                  <td>{r.count}</td>
                  <td className={outcomeCellClass(r.avg_return_5b)}>{fmtPct(r.avg_return_5b)}</td>
                  <td className={outcomeCellClass(r.avg_return_10b)}>{fmtPct(r.avg_return_10b)}</td>
                  <td className={outcomeCellClass(r.avg_return_20b)}>{fmtPct(r.avg_return_20b)}</td>
                  <td className={outcomeCellClass(r.avg_mfe)}>{fmtPct(r.avg_mfe)}</td>
                  <td className={outcomeCellClass(r.avg_mae)}>{fmtPct(r.avg_mae)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {regimeCounts.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <span className="label">All-time counts: </span>
          {regimeCounts.map((c, i) => (
            <span key={c.regime} className={regimeBadgeClass(c.regime)} style={{ marginRight: 6 }}>
              {c.regime}={c.count}
              {i < regimeCounts.length - 1 ? '' : ''}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default HistoricalSignalCard;
