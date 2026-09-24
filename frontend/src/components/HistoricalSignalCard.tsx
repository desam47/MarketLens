import React, { useCallback, useEffect, useState } from 'react';
import api, { HistoricalSignal, RegimeCount, RegimePerformance, SignalScopeMode, Watchlist } from '../services/api';
import { fmtPrice } from './watchlistUtils';
import { DEFAULT_TIMEFRAME, TIMEFRAME_LABELS } from '../utils/timeframeUtils';
import { directionalOutcome, isSignalOutcomeComplete } from '../utils/signalOutcomes';

const strPrice = fmtPrice;

// Historical Signals page only shows the 3 primary timeframes — the
// sub-hour / 4h / weekly buckets are recorded by the trend engine but
// not analysed in this view. Keeps the dropdown focused on what users
// typically want to inspect.
const SIGNAL_TIMEFRAMES = ['1m', '1h', '1d'] as const;

interface HistoricalSignalCardProps {
  /** Optional default symbol to filter on. */
  defaultSymbol?: string;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function fmtDateTime(d: string | null | undefined): string {
  if (!d) return '—';
  // The API serialises with format_edt_iso() which bakes the ET offset
  // into the time portion: "2026-09-03T14:05:00-04:00" means the time IS
  // 14:05 Eastern. We parse just the date+time part and append " ET".
  const match = d.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})/);
  if (match) {
    return match[1].replace('T', ' ') + ' ET';
  }
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
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [scope, setScope] = useState<SignalScopeMode>('all_active');
  const [watchlistId, setWatchlistId] = useState<number | undefined>();
  const [timeframe, setTimeframe] = useState<string>(DEFAULT_TIMEFRAME);
  const [completedOnly, setCompletedOnly] = useState(true);
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
        completedOnly,
        scope,
        scope === 'watchlist' ? watchlistId : undefined,
      );
      setSignals(rows);
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Failed to load signals', isError: true });
      setSignals([]);
    } finally {
      setLoading(false);
    }
  }, [symbol, timeframe, completedOnly, scope, watchlistId]);

  const loadResearch = useCallback(async () => {
    try {
      const [perf, counts] = await Promise.all([
        api.getRegimePerformance(scope, scope === 'watchlist' ? watchlistId : undefined),
        api.getSignalCountByRegime(scope, scope === 'watchlist' ? watchlistId : undefined),
      ]);
      setRegimePerformance(perf);
      setRegimeCounts(counts);
    } catch {
      // Best effort: leave previous values.
    }
  }, [scope, watchlistId]);

  useEffect(() => {
    let active = true;
    api.getWatchlists()
      .then((rows) => { if (active) setWatchlists(rows.filter((row) => row.is_active)); })
      .catch(() => { if (active) setWatchlists([]); });
    return () => { active = false; };
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

  const completedSignals = signals.filter(isSignalOutcomeComplete).length;
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
          <span>Scope</span>
          <select
            value={scope === 'watchlist' ? `watchlist:${watchlistId ?? ''}` : scope}
            onChange={(event) => {
              const value = event.target.value;
              if (value.startsWith('watchlist:')) {
                setScope('watchlist');
                setWatchlistId(Number(value.slice('watchlist:'.length)) || undefined);
              } else {
                setScope(value as SignalScopeMode);
                setWatchlistId(undefined);
              }
            }}
            aria-label="Historical signal scope"
          >
            <option value="all_active">All active watchlists</option>
            {watchlists.map((watchlist) => <option key={watchlist.id} value={`watchlist:${watchlist.id}`}>{watchlist.name}</option>)}
          </select>
        </label>
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
            {SIGNAL_TIMEFRAMES.map((tf) => (
              <option key={tf} value={tf}>{TIMEFRAME_LABELS[tf] || tf}</option>
            ))}
          </select>
        </label>
        <label className="checkbox-label" style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={completedOnly}
            onChange={(e) => setCompletedOnly(e.target.checked)}
          />
          <span>With outcomes only</span>
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
                <th>Signal 5b</th>
                <th>Signal 10b</th>
                <th>Signal 20b</th>
                <th>Fav. ex.</th>
                <th>Adv. ex.</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => (
                <tr key={s.id}>
                  <td><strong>{s.symbol}</strong></td>
                  <td>{s.timeframe}</td>
                  <td>{fmtDateTime(s.timestamp)}</td>
                  <td>{s.price == null ? '—' : strPrice(s.price)}</td>
                  <td>{s.trend_state || '—'}</td>
                  <td>{s.trend_score == null ? '—' : s.trend_score.toFixed(1)}</td>
                  <td>
                    <span className={regimeBadgeClass(s.market_regime)}>
                      {s.market_regime || '—'}
                    </span>
                  </td>
                  <td className={outcomeCellClass(directionalOutcome(s, 'return_5b'))}>{fmtPct(directionalOutcome(s, 'return_5b'))}</td>
                  <td className={outcomeCellClass(directionalOutcome(s, 'return_10b'))}>{fmtPct(directionalOutcome(s, 'return_10b'))}</td>
                  <td className={outcomeCellClass(directionalOutcome(s, 'return_20b'))}>{fmtPct(directionalOutcome(s, 'return_20b'))}</td>
                  <td className={outcomeCellClass(directionalOutcome(s, 'mfe'))}>{fmtPct(directionalOutcome(s, 'mfe'))}</td>
                  <td className={outcomeCellClass(directionalOutcome(s, 'mae'))}>{fmtPct(directionalOutcome(s, 'mae'))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3 style={{ marginTop: 18, color: '#34495e' }}>Directional Performance by Market Regime</h3>
      <p className="label" style={{ marginTop: 0 }}>
        Direction-adjusted returns, favorable excursion, and adverse excursion
        for completed bullish/bearish signals. Underlying price movement remains
        available in exports; neutral signals have no directional outcome.
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
                <th>Avg signal 5b</th>
                <th>Avg signal 10b</th>
                <th>Avg signal 20b</th>
                <th>Avg fav. ex.</th>
                <th>Avg adv. ex.</th>
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
