import React, { useEffect, useState, useCallback } from 'react';
import api, { BacktestRun, BacktestTrade } from '../services/api';
import { formatETDateTime } from './chartMath';

interface BacktestCardProps {
  /** Optional symbol to prefill the form with. */
  defaultSymbol?: string;
}

interface SignalOption {
  value: string;
  label: string;
}

// Same set of replay-supported signals as the backend's DEFAULT_SIGNALS
// in backtesting/engine.py. These are the v1 set that produce
// non-trivial forward returns.
const SIGNAL_OPTIONS: SignalOption[] = [
  { value: 'RSI_OVERSOLD', label: 'RSI Oversold' },
  { value: 'RSI_OVERBOUGHT', label: 'RSI Overbought' },
  { value: 'MACD_BULLISH', label: 'MACD Bullish' },
  { value: 'MACD_BEARISH', label: 'MACD Bearish' },
  { value: 'HIGH_VOLUME', label: 'High Volume' },
];

const DEFAULT_SIGNALS = ['RSI_OVERSOLD', 'RSI_OVERBOUGHT', 'MACD_BULLISH', 'MACD_BEARISH', 'HIGH_VOLUME'];

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function oneYearAgoISO(): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}

function fmtPct(v: number | null): string {
  return v == null ? '—' : v.toFixed(2) + '%';
}

function fmtDate(d: string | null): string {
  return d ? d.slice(0, 10) : '—';
}

function fmtPrice(p: number | null): string {
  return p == null ? '—' : p.toFixed(2);
}

export function BacktestCard({ defaultSymbol = '' }: BacktestCardProps) {
  // Form state
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [startDate, setStartDate] = useState(oneYearAgoISO());
  const [endDate, setEndDate] = useState(todayISO());
  const [selectedSignals, setSelectedSignals] = useState<string[]>(DEFAULT_SIGNALS);

  // Run / display state
  const [currentRun, setCurrentRun] = useState<BacktestRun | null>(null);
  const [currentTrades, setCurrentTrades] = useState<BacktestTrade[] | null>(null);
  const [recent, setRecent] = useState<BacktestRun[]>([]);
  const [status, setStatus] = useState<{ msg: string; isError: boolean } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (defaultSymbol) setSymbol(defaultSymbol);
  }, [defaultSymbol]);

  const refreshRecent = useCallback(async () => {
    try {
      const runs = await api.getBacktestRuns(20);
      setRecent(runs);
    } catch {
      // Best-effort; the card stays usable without the list.
    }
  }, []);

  useEffect(() => {
    refreshRecent();
  }, [refreshRecent]);

  const loadRun = useCallback(async (id: number) => {
    try {
      const [run, trades] = await Promise.all([
        api.getBacktestRun(id),
        api.getBacktestTrades(id).catch(() => [] as BacktestTrade[]),
      ]);
      setCurrentRun(run);
      setCurrentTrades(trades);
      setStatus(null);
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Failed to load run', isError: true });
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanSymbol = symbol.trim().toUpperCase();
    if (!cleanSymbol) {
      setStatus({ msg: 'Enter a symbol.', isError: true });
      return;
    }
    if (!startDate || !endDate) {
      setStatus({ msg: 'Pick a start and end date.', isError: true });
      return;
    }
    if (new Date(endDate) <= new Date(startDate)) {
      setStatus({ msg: 'End date must be after start date.', isError: true });
      return;
    }

    setSubmitting(true);
    setStatus({ msg: 'Running backtest… (this can take a few seconds)', isError: false });
    setCurrentRun(null);
    setCurrentTrades(null);

    try {
      const run = await api.createBacktest({
        symbol: cleanSymbol,
        start_date: startDate,
        end_date: endDate,
        signals: selectedSignals,
      });
      setCurrentRun(run);
      if (run.status === 'completed') {
        try {
          const trades = await api.getBacktestTrades(run.id);
          setCurrentTrades(trades);
        } catch {
          setCurrentTrades([]);
        }
      } else {
        setCurrentTrades([]);
      }
      await refreshRecent();
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Backtest failed', isError: true });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="card backtest-card">
      <h2>Backtest (Signal Replay)</h2>
      <p className="label" style={{ marginTop: 0 }}>
        Replay the scanner's bar-derived signals over historical daily bars
        and see the forward 1d / 5d / 20d return each one would have
        produced. MTF signals are skipped (v1 limitation).
      </p>

      <form className="backtest-form" onSubmit={handleSubmit}>
        <label>
          <span>Symbol</span>
          <input
            type="text"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            maxLength={5}
            disabled={submitting}
          />
        </label>
        <label>
          <span>Start</span>
          <input
            type="date"
            value={startDate}
            onChange={e => setStartDate(e.target.value)}
            disabled={submitting}
          />
        </label>
        <label>
          <span>End</span>
          <input
            type="date"
            value={endDate}
            onChange={e => setEndDate(e.target.value)}
            disabled={submitting}
          />
        </label>
        <label>
          <span>Signals</span>
          <select
            multiple
            size={4}
            value={selectedSignals}
            onChange={e => {
              const opts = Array.from((e.target as HTMLSelectElement).selectedOptions).map(o => o.value);
              setSelectedSignals(opts);
            }}
            disabled={submitting}
          >
            {SIGNAL_OPTIONS.map(s => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </label>
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? 'Running…' : 'Run Backtest'}
        </button>
      </form>

      {status && (
        <div className={status.isError ? 'error-text' : 'info-text'} style={{ marginBottom: 8 }}>
          {status.msg}
        </div>
      )}

      {currentRun && currentRun.status === 'failed' && (
        <div className="error-text">
          Failed: {currentRun.error || 'unknown error'}
        </div>
      )}

      {currentRun && currentRun.status === 'completed' && (
        <>
          <div className="info-text" style={{ marginBottom: 8 }}>
            Run #{currentRun.id} {currentRun.symbol}{' '}
            {fmtDate(currentRun.start_date)} → {fmtDate(currentRun.end_date)}
            {' '}— {currentRun.total_signals ?? 0} signals over {currentRun.total_bars ?? 0} bars.
          </div>
          <div className="backtest-summary-grid">
            <div className="data-item">
              <h3>Win Rate (1d)</h3>
              <div className="value">
                {currentRun.win_rate_1d == null
                  ? '—'
                  : (currentRun.win_rate_1d * 100).toFixed(1) + '%'}
              </div>
            </div>
            <div className="data-item">
              <h3>Avg 1d Return</h3>
              <div className="value">{fmtPct(currentRun.avg_return_1d)}</div>
            </div>
            <div className="data-item">
              <h3>Avg 5d Return</h3>
              <div className="value">{fmtPct(currentRun.avg_return_5d)}</div>
            </div>
            <div className="data-item">
              <h3>Avg 20d Return</h3>
              <div className="value">{fmtPct(currentRun.avg_return_20d)}</div>
            </div>
          </div>
        </>
      )}

      {currentTrades && currentTrades.length > 0 && (
        <div className="data-grid" style={{ marginTop: 12 }}>
          <table className="trades-table">
            <thead>
              <tr>
                <th className="signal">Signal</th>
                <th>Entry</th>
                <th>Entry $</th>
                <th>1d</th>
                <th>5d</th>
                <th>20d</th>
              </tr>
            </thead>
            <tbody>
              {currentTrades.map(t => (
                <tr key={t.id}>
                  <td className="signal">{t.signal}</td>
                  <td>{fmtDate(t.entry_date)}</td>
                  <td>{fmtPrice(t.entry_price)}</td>
                  <td>{fmtPct(t.return_1d)}</td>
                  <td>{fmtPct(t.return_5d)}</td>
                  <td>{fmtPct(t.return_20d)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {currentTrades && currentTrades.length === 0 && currentRun?.status === 'completed' && (
        <p className="empty-state">No signals fired in this range.</p>
      )}

      <h3 style={{ marginTop: 18, color: '#34495e' }}>Recent Runs</h3>
      {recent.length === 0 ? (
        <p className="empty-state">No backtest runs yet.</p>
      ) : (
        <ul className="run-list">
          {recent.map(r => (
            <li key={r.id} onClick={() => loadRun(r.id)}>
              <span>
                #{r.id} <strong>{r.symbol}</strong>{' '}
                {fmtDate(r.start_date)} → {fmtDate(r.end_date)}{' '}
                ({r.total_signals == null ? '…' : `${r.total_signals} sigs`})
              </span>
              <span>{r.created_at ? formatETDateTime(r.created_at) : ''}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default BacktestCard;
