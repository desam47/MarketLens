import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api, { Bar, HistoricalSignal, TickSignalReplayResponse } from '../services/api';
import { formatETDateTime } from './chartMath';
import { TIMEFRAME_LABELS } from '../utils/timeframeUtils';
import { readSessionPreference, sessionMatchesPreference, SESSION_PREFERENCE_KEY, type SessionPreference } from '../utils/marketSession';

const REPLAY_TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d'] as const;
const REPLAY_LIMIT = 180;
const REPLAY_SESSIONS = [
  { value: 'all', label: 'All sessions' },
  { value: 'premarket', label: 'Premarket (04:00–09:30 ET)' },
  { value: 'regular', label: 'Regular (09:30–16:00 ET)' },
  { value: 'after_hours', label: 'After-hours (16:00–20:00 ET)' },
] as const;

interface HistoricalReplayPanelProps {
  defaultSymbol?: string;
}

interface SimulatedTrade {
  signal: HistoricalSignal;
  entry: number;
  exit: number | null;
  result: 'target' | 'stop' | 'open';
  returnPct: number | null;
  barsHeld: number;
}

function fmt(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits);
}

function fmtPct(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function dateKey(timestamp: string): string {
  return timestamp.slice(0, 10);
}

function latestSignalAt(signals: HistoricalSignal[], timestamp: string): HistoricalSignal | null {
  const point = Date.parse(timestamp);
  let latest: HistoricalSignal | null = null;
  let latestTime = -Infinity;
  for (const signal of signals) {
    const signalTime = Date.parse(signal.timestamp);
    if (Number.isFinite(signalTime) && signalTime <= point && signalTime >= latestTime) {
      latest = signal;
      latestTime = signalTime;
    }
  }
  return latest;
}

function ReplayMicrostructureChart({ candles }: { candles: TickSignalReplayResponse['candles'] }) {
  const points = candles.slice(-120);
  if (!points.length) return null;
  const width = 800;
  const height = 180;
  const x = (index: number) => (index / Math.max(1, points.length - 1)) * width;
  const y = (value: number | null, min: number, max: number) => {
    const normalized = (value == null ? 0 : Math.max(min, Math.min(max, value)) - min) / (max - min);
    return height - normalized * (height - 24) - 12;
  };
  const pressurePath = points.map((point, index) => `${index ? 'L' : 'M'}${x(index)},${y(point.tape_pressure, -1, 1)}`).join(' ');
  const imbalancePath = points.map((point, index) => `${index ? 'L' : 'M'}${x(index)},${y(point.bbo_imbalance, -1, 1)}`).join(' ');
  const maxVelocity = Math.max(1, ...points.map(point => point.trade_velocity));
  return (
    <div className="replay-microstructure-chart" aria-label="Replay microstructure timeline">
      <div className="replay-chart-legend"><span className="replay-legend-pressure">● Tape pressure</span><span className="replay-legend-imbalance">● BBO imbalance</span><span>▂ Trade velocity</span></div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" preserveAspectRatio="none">
        <line x1="0" y1={y(0, -1, 1)} x2={width} y2={y(0, -1, 1)} className="replay-chart-zero" />
        {points.map((point, index) => <rect key={`bar-${index}`} x={x(index) - 1.5} y={height - (point.trade_velocity / maxVelocity) * (height - 24) - 12} width="3" height={(point.trade_velocity / maxVelocity) * (height - 24)} className="replay-velocity-bar" />)}
        <path d={pressurePath} className="replay-pressure-line" />
        <path d={imbalancePath} className="replay-imbalance-line" />
        {points.map((point, index) => point.large_prints > 0 && <circle key={`print-${index}`} cx={x(index)} cy={y(point.tape_pressure, -1, 1)} r={Math.min(6, 2 + point.large_prints)} className="replay-large-print" />)}
      </svg>
    </div>
  );
}

export function HistoricalReplayPanel({ defaultSymbol = 'SPY' }: HistoricalReplayPanelProps) {
  const [symbol, setSymbol] = useState(defaultSymbol || 'SPY');
  const [timeframe, setTimeframe] = useState<string>('1d');
  const [bars, setBars] = useState<Bar[]>([]);
  const [signals, setSignals] = useState<HistoricalSignal[]>([]);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(700);
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [sessionFilter, setSessionFilter] = useState<SessionPreference>(() => readSessionPreference());
  const [stopPct, setStopPct] = useState(1);
  const [targetPct, setTargetPct] = useState(2);
  const [tickReplay, setTickReplay] = useState<TickSignalReplayResponse | null>(null);
  const [tickReplayLoading, setTickReplayLoading] = useState(false);
  const [tickReplayError, setTickReplayError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef(0);
  const symbolRef = useRef(symbol);
  const timeframeRef = useRef(timeframe);
  symbolRef.current = symbol;
  timeframeRef.current = timeframe;

  const loadReplay = useCallback(async () => {
    const requestedSymbol = symbolRef.current.trim().toUpperCase();
    if (!requestedSymbol) {
      setError('Enter a symbol to replay.');
      return;
    }
    const requestId = ++requestRef.current;
    setLoading(true);
    setPlaying(false);
    setError(null);
    try {
      const barResult = await api.getAnalysisBars(requestedSymbol, timeframeRef.current, REPLAY_LIMIT);
      let signalResult: HistoricalSignal[] = [];
      try {
        signalResult = await api.listSignals(requestedSymbol, timeframeRef.current, REPLAY_LIMIT, false);
      } catch {
        // Price replay remains useful when signal recording is unavailable.
      }
      if (requestId !== requestRef.current) return;
      // The analysis endpoint returns newest-first; replay must be chronological.
      setBars([...barResult.bars].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)));
      setSignals(signalResult);
      setCursor(0);
    } catch (err: any) {
      if (requestId !== requestRef.current) return;
      setBars([]);
      setSignals([]);
      setError(err?.message || 'Unable to load replay data.');
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadReplay();
  }, [loadReplay]); // Load the default symbol once; symbol changes are applied with Load.

  const reconstructTicks = useCallback(async () => {
    const requestedSymbol = symbol.trim().toUpperCase();
    if (!requestedSymbol) return;
    setTickReplayLoading(true);
    setTickReplayError(null);
    try {
      setTickReplay(await api.getTickSignalReplay(requestedSymbol));
    } catch (err: any) {
      setTickReplay(null);
      setTickReplayError(err?.message || 'No retained ticks are available for reconstruction.');
    } finally {
      setTickReplayLoading(false);
    }
  }, [symbol]);

  const validDateRange = !fromDate || !toDate || fromDate <= toDate;
  const replayBars = useMemo(() => {
    if (!validDateRange) return [];
    return bars.filter((bar) => {
      const day = dateKey(bar.timestamp);
      return (!fromDate || day >= fromDate) && (!toDate || day <= toDate);
    }).filter((bar) => sessionMatchesPreference(bar, sessionFilter));
  }, [bars, fromDate, toDate, validDateRange, sessionFilter]);
  const replaySignals = useMemo(() => {
    if (!replayBars.length) return [];
    const firstDay = dateKey(replayBars[0].timestamp);
    const lastDay = dateKey(replayBars[replayBars.length - 1].timestamp);
    return signals.filter((signal) => dateKey(signal.timestamp) >= firstDay && dateKey(signal.timestamp) <= lastDay);
  }, [replayBars, signals]);
  const completedOutcomes = useMemo(
    () => replaySignals.filter((signal) => signal.return_5b != null),
    [replaySignals],
  );
  const replayStats = useMemo(() => {
    const average = (values: Array<number | null>) => {
      const usable = values.filter((value): value is number => value != null && Number.isFinite(value));
      return usable.length ? usable.reduce((total, value) => total + value, 0) / usable.length : null;
    };
    const wins = completedOutcomes.filter((signal) => (signal.return_5b || 0) > 0).length;
    return {
      signals: replaySignals.length,
      completed: completedOutcomes.length,
      winRate: completedOutcomes.length ? (wins / completedOutcomes.length) * 100 : null,
      averageReturn: average(completedOutcomes.map((signal) => signal.return_5b)),
      averageMfe: average(completedOutcomes.map((signal) => signal.mfe)),
      averageMae: average(completedOutcomes.map((signal) => signal.mae)),
    };
  }, [completedOutcomes, replaySignals.length]);
  const simulatedTrades = useMemo<SimulatedTrade[]>(() => replaySignals.map((signal) => {
    const index = replayBars.findIndex((bar) => Date.parse(bar.timestamp) >= Date.parse(signal.timestamp));
    if (index < 0) return { signal, entry: 0, exit: null, result: 'open', returnPct: null, barsHeld: 0 };
    const entry = replayBars[index].close;
    const bearish = /bear|down|sell/i.test(signal.trend_state || '');
    const stop = bearish ? entry * (1 + stopPct / 100) : entry * (1 - stopPct / 100);
    const target = bearish ? entry * (1 - targetPct / 100) : entry * (1 + targetPct / 100);
    for (let i = index + 1; i < replayBars.length; i += 1) {
      const bar = replayBars[i];
      const hitStop = bearish ? bar.high >= stop : bar.low <= stop;
      const hitTarget = bearish ? bar.low <= target : bar.high >= target;
      if (hitStop || hitTarget) {
        const result = hitStop ? 'stop' : 'target';
        const exit = result === 'stop' ? stop : target;
        return { signal, entry, exit, result, returnPct: (bearish ? (entry - exit) : (exit - entry)) / entry * 100, barsHeld: i - index };
      }
    }
    return { signal, entry, exit: null, result: 'open', returnPct: null, barsHeld: replayBars.length - index - 1 };
  }), [replayBars, replaySignals, stopPct, targetPct]);
  const simulatedCompleted = simulatedTrades.filter((trade) => trade.returnPct != null);
  const simulatedNet = simulatedCompleted.reduce((sum, trade) => sum + (trade.returnPct || 0), 0);

  useEffect(() => {
    setCursor((current) => Math.min(current, Math.max(0, replayBars.length - 1)));
    if (!validDateRange || replayBars.length < 2) setPlaying(false);
  }, [replayBars.length, validDateRange]);

  useEffect(() => {
    setCursor(0);
    setPlaying(false);
  }, [fromDate, toDate, sessionFilter]);

  useEffect(() => {
    if (!playing || replayBars.length < 2) return undefined;
    const timer = window.setInterval(() => {
      setCursor((current) => {
        if (current >= replayBars.length - 1) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, speed);
    return () => window.clearInterval(timer);
  }, [playing, replayBars.length, speed]);

  const currentBar = replayBars[cursor] || null;
  const currentSignal = useMemo(
    () => currentBar ? latestSignalAt(signals, currentBar.timestamp) : null,
    [currentBar, signals],
  );
  const previousBar = cursor > 0 ? replayBars[cursor - 1] : null;
  const change = currentBar && previousBar ? currentBar.close - previousBar.close : null;
  const visibleBars = replayBars.slice(Math.max(0, cursor - 11), cursor + 1);

  const moveCursor = (next: number) => {
    setPlaying(false);
    setCursor(Math.max(0, Math.min(next, Math.max(0, replayBars.length - 1))));
  };

  return (
    <div className="card historical-replay-card" id="historical-replay">
      <div className="replay-heading">
        <div>
          <h2>Historical Replay</h2>
          <p className="label">
            Step through candles from oldest to newest. The signal state is limited to data
            available at the selected point, so replay never looks ahead.
          </p>
        </div>
        {replayBars.length > 0 && <span className="replay-counter">{cursor + 1} / {replayBars.length}</span>}
      </div>

      <div className="replay-controls">
        <label>
          <span>Symbol</span>
          <input
            value={symbol}
            maxLength={8}
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            onKeyDown={(event) => { if (event.key === 'Enter') void loadReplay(); }}
            aria-label="Replay symbol"
          />
        </label>
        <label>
          <span>Timeframe</span>
          <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)} aria-label="Replay timeframe">
            {REPLAY_TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{TIMEFRAME_LABELS[tf] || tf}</option>)}
          </select>
        </label>
        <button className="btn btn-primary" onClick={() => void loadReplay()} disabled={loading}>
          {loading ? 'Loading…' : 'Load Replay'}
        </button>
        <button className="btn" onClick={() => void reconstructTicks()} disabled={tickReplayLoading}>
          {tickReplayLoading ? 'Reconstructing…' : 'Reconstruct Retained Ticks'}
        </button>
        <label>
          <span>Speed</span>
          <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))} aria-label="Replay speed">
            <option value={1200}>Slow</option>
            <option value={700}>Normal</option>
            <option value={300}>Fast</option>
          </select>
        </label>
        <label>
          <span>From</span>
          <input type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} aria-label="Replay start date" />
        </label>
        <label>
          <span>To</span>
          <input type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} aria-label="Replay end date" />
        </label>
        <label>
          <span>Market session</span>
          <select value={sessionFilter} onChange={(event) => { const value = event.target.value as SessionPreference; setSessionFilter(value); window.localStorage.setItem(SESSION_PREFERENCE_KEY, value); }} aria-label="Replay market session">
            {REPLAY_SESSIONS.map((session) => <option key={session.value} value={session.value}>{session.label}</option>)}
          </select>
        </label>
        <label><span>Stop %</span><input type="number" min="0.1" step="0.1" value={stopPct} onChange={(event) => setStopPct(Math.max(0.1, Number(event.target.value) || 1))} aria-label="Simulated stop percentage" /></label>
        <label><span>Target %</span><input type="number" min="0.1" step="0.1" value={targetPct} onChange={(event) => setTargetPct(Math.max(0.1, Number(event.target.value) || 2))} aria-label="Simulated target percentage" /></label>
        {(fromDate || toDate) && (
          <button className="btn" onClick={() => { setFromDate(''); setToDate(''); }}>
            Clear Dates
          </button>
        )}
      </div>

      {error && <div className="error-text replay-status">{error}</div>}
      {tickReplayError && <div className="error-text replay-status">{tickReplayError}</div>}
      {tickReplay && (
        <div className="replay-tick-summary" aria-label="Tick signal reconstruction">
          <strong>Tick-level reconstruction</strong>
          <span>{tickReplay.retained_events.toLocaleString()} retained events → {tickReplay.reconstructed_candles} one-minute candles</span>
          <span>{tickReplay.candles.filter((c) => c.signal_score != null).length} causal signal states reconstructed</span>
          {tickReplay.candles.length > 0 && (
            <span>Latest: {tickReplay.candles[tickReplay.candles.length - 1].signal_state || 'warm-up'} · Score {fmt(tickReplay.candles[tickReplay.candles.length - 1].signal_score, 1)} · Pressure {fmtPct(tickReplay.candles[tickReplay.candles.length - 1].tape_pressure * 100)}</span>
          )}
          {tickReplay.candles.length > 0 && (() => { const latest = tickReplay.candles[tickReplay.candles.length - 1]; return <span>Latest microstructure: Buy {latest.buy_volume.toLocaleString()} · Sell {latest.sell_volume.toLocaleString()} · Velocity {latest.trade_velocity}/min · Large prints {latest.large_prints} · BBO imbalance {latest.bbo_imbalance == null ? '—' : fmtPct(latest.bbo_imbalance * 100)}</span>; })()}
          <ReplayMicrostructureChart candles={tickReplay.candles} />
        </div>
      )}
      {!loading && !error && !validDateRange && (
        <div className="error-text replay-status">The start date must be on or before the end date.</div>
      )}
      {!loading && !error && validDateRange && bars.length === 0 && (
        <div className="empty-state replay-empty">Load a symbol to start the replay.</div>
      )}
      {!loading && !error && validDateRange && bars.length > 0 && replayBars.length === 0 && (
        <div className="empty-state replay-empty">No candles fall inside the selected date range.</div>
      )}

      {currentBar && (
        <>
          <div className="replay-metrics" aria-label="Replay performance summary">
            <div><small>Signals</small><strong>{replayStats.signals}</strong></div>
            <div><small>Completed</small><strong>{replayStats.completed}</strong></div>
            <div><small>5-bar win rate</small><strong>{replayStats.winRate == null ? '—' : `${replayStats.winRate.toFixed(1)}%`}</strong></div>
            <div><small>Avg 5-bar return</small><strong>{fmtPct(replayStats.averageReturn)}</strong></div>
            <div><small>Avg MFE</small><strong>{fmtPct(replayStats.averageMfe)}</strong></div>
            <div><small>Avg MAE</small><strong>{fmtPct(replayStats.averageMae)}</strong></div>
            <div><small>Simulated trades</small><strong>{simulatedCompleted.length} / {simulatedTrades.length}</strong></div>
            <div><small>Simulated net</small><strong className={simulatedNet >= 0 ? 'positive' : 'negative'}>{fmtPct(simulatedNet)}</strong></div>
          </div>

          <div className="replay-toolbar">
            <button className="btn" onClick={() => moveCursor(cursor - 1)} disabled={cursor === 0}>Previous</button>
            <button
              className="btn btn-primary"
              onClick={() => {
                if (cursor >= replayBars.length - 1) setCursor(0);
                setPlaying((value) => !value);
              }}
              disabled={replayBars.length < 2}
            >
              {playing ? 'Pause' : 'Play'}
            </button>
            <button className="btn" onClick={() => moveCursor(cursor + 1)} disabled={cursor >= replayBars.length - 1}>Next</button>
            <input
              className="replay-progress"
              type="range"
              min={0}
              max={Math.max(0, replayBars.length - 1)}
              value={cursor}
              onChange={(event) => moveCursor(Number(event.target.value))}
              aria-label="Replay position"
            />
          </div>

          <div className="replay-current-grid">
            <div className="replay-current-bar">
              <div className="replay-date">
                {formatETDateTime(currentBar.timestamp)} ET
                {currentBar.session && <span className={`session-badge session-${currentBar.session}`}>{currentBar.session.replace('_', ' ')}</span>}
              </div>
              <div className="replay-ohlc-grid">
                <span><small>Open</small>{fmt(currentBar.open)}</span>
                <span><small>High</small>{fmt(currentBar.high)}</span>
                <span><small>Low</small>{fmt(currentBar.low)}</span>
                <span><small>Close</small>{fmt(currentBar.close)}</span>
                <span><small>Change</small><em className={change != null && change < 0 ? 'negative' : 'positive'}>{change == null ? '—' : `${change >= 0 ? '+' : ''}${fmt(change)}`}</em></span>
                <span><small>Volume</small>{fmt(currentBar.volume, 0)}</span>
              </div>
            </div>
            <div className="replay-signal-state">
              <div className="replay-section-title">Signal at this point</div>
              {currentSignal ? (
                <div className="replay-signal-values">
                  <strong>{currentSignal.trend_state || 'Unknown'}</strong>
                  <span>Score {fmt(currentSignal.trend_score, 1)} · Strength {fmt(currentSignal.strength, 1)}</span>
                  <span>Regime: {currentSignal.market_regime || '—'}</span>
                  <span>Recorded {formatETDateTime(currentSignal.timestamp)} ET</span>
                  <div className="replay-outcome">
                    <small>Historical outcome after this signal</small>
                    <span>5-bar {fmtPct(currentSignal.return_5b)} · 10-bar {fmtPct(currentSignal.return_10b)} · 20-bar {fmtPct(currentSignal.return_20b)}</span>
                    <span>MFE {fmtPct(currentSignal.mfe)} · MAE {fmtPct(currentSignal.mae)}</span>
                  </div>
                </div>
              ) : (
                <span className="replay-muted">No recorded signal at this point.</span>
              )}
            </div>
          </div>

          {simulatedTrades.length > 0 && (
            <div className="replay-section-title" style={{ marginTop: '0.9rem' }}>Simulated trade outcomes</div>
          )}
          {simulatedTrades.slice(-8).map((trade) => (
            <div className="replay-sim-trade" key={`${trade.signal.id}-${trade.signal.timestamp}`}>
              <span>{formatETDateTime(trade.signal.timestamp)} · {trade.signal.trend_state || 'signal'}</span>
              <span>Entry {fmt(trade.entry)} · {trade.result === 'open' ? `${trade.barsHeld} bars open` : `${trade.result} after ${trade.barsHeld} bars`}</span>
              <strong className={trade.returnPct == null ? '' : trade.returnPct >= 0 ? 'positive' : 'negative'}>{fmtPct(trade.returnPct)}</strong>
            </div>
          ))}

          <div className="replay-bars" aria-label="Recent replay candles">
            {visibleBars.map((bar, index) => {
              const isUp = bar.close >= bar.open;
              const isCurrent = bar.timestamp === currentBar.timestamp;
              const marker = replaySignals.find((signal) => dateKey(signal.timestamp) === dateKey(bar.timestamp));
              return (
                <div key={`${bar.timestamp}-${index}`} className={`replay-bar ${isCurrent ? 'current' : ''}`} title={`${formatETDateTime(bar.timestamp)} · ${fmt(bar.close)}${marker ? ` · ${marker.trend_state || 'signal'}` : ''}`}>
                  <span className={`replay-bar-body ${isUp ? 'up' : 'down'}`} style={{ height: `${Math.max(10, Math.min(84, Math.abs(bar.close - bar.open) / Math.max(bar.high - bar.low, 0.01) * 84))}%` }} />
                  <span className="replay-bar-wick" />
                  {marker && <span className={`replay-signal-marker ${marker.return_5b == null ? 'neutral' : marker.return_5b >= 0 ? 'positive' : 'negative'}`} aria-label={`Signal: ${marker.trend_state || 'unknown'}`} />}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

export default HistoricalReplayPanel;
