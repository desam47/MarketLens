import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api, { Bar, HistoricalSignal } from '../services/api';
import { formatETDateTime } from './chartMath';
import { TIMEFRAME_LABELS } from '../utils/timeframeUtils';

const REPLAY_TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d'] as const;
const REPLAY_LIMIT = 180;

interface HistoricalReplayPanelProps {
  defaultSymbol?: string;
}

function fmt(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits);
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

export function HistoricalReplayPanel({ defaultSymbol = 'SPY' }: HistoricalReplayPanelProps) {
  const [symbol, setSymbol] = useState(defaultSymbol || 'SPY');
  const [timeframe, setTimeframe] = useState<string>('1d');
  const [bars, setBars] = useState<Bar[]>([]);
  const [signals, setSignals] = useState<HistoricalSignal[]>([]);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(700);
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

  useEffect(() => {
    if (!playing || bars.length < 2) return undefined;
    const timer = window.setInterval(() => {
      setCursor((current) => {
        if (current >= bars.length - 1) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, speed);
    return () => window.clearInterval(timer);
  }, [playing, bars.length, speed]);

  const currentBar = bars[cursor] || null;
  const currentSignal = useMemo(
    () => currentBar ? latestSignalAt(signals, currentBar.timestamp) : null,
    [currentBar, signals],
  );
  const previousBar = cursor > 0 ? bars[cursor - 1] : null;
  const change = currentBar && previousBar ? currentBar.close - previousBar.close : null;
  const visibleBars = bars.slice(Math.max(0, cursor - 11), cursor + 1);

  const moveCursor = (next: number) => {
    setPlaying(false);
    setCursor(Math.max(0, Math.min(next, Math.max(0, bars.length - 1))));
  };

  return (
    <div className="card historical-replay-card">
      <div className="replay-heading">
        <div>
          <h2>Historical Replay</h2>
          <p className="label">
            Step through candles from oldest to newest. The signal state is limited to data
            available at the selected point, so replay never looks ahead.
          </p>
        </div>
        {bars.length > 0 && <span className="replay-counter">{cursor + 1} / {bars.length}</span>}
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
        <label>
          <span>Speed</span>
          <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))} aria-label="Replay speed">
            <option value={1200}>Slow</option>
            <option value={700}>Normal</option>
            <option value={300}>Fast</option>
          </select>
        </label>
      </div>

      {error && <div className="error-text replay-status">{error}</div>}
      {!loading && !error && bars.length === 0 && (
        <div className="empty-state replay-empty">Load a symbol to start the replay.</div>
      )}

      {currentBar && (
        <>
          <div className="replay-toolbar">
            <button className="btn" onClick={() => moveCursor(cursor - 1)} disabled={cursor === 0}>Previous</button>
            <button
              className="btn btn-primary"
              onClick={() => {
                if (cursor >= bars.length - 1) setCursor(0);
                setPlaying((value) => !value);
              }}
              disabled={bars.length < 2}
            >
              {playing ? 'Pause' : 'Play'}
            </button>
            <button className="btn" onClick={() => moveCursor(cursor + 1)} disabled={cursor >= bars.length - 1}>Next</button>
            <input
              className="replay-progress"
              type="range"
              min={0}
              max={Math.max(0, bars.length - 1)}
              value={cursor}
              onChange={(event) => moveCursor(Number(event.target.value))}
              aria-label="Replay position"
            />
          </div>

          <div className="replay-current-grid">
            <div className="replay-current-bar">
              <div className="replay-date">{formatETDateTime(currentBar.timestamp)} ET</div>
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
                </div>
              ) : (
                <span className="replay-muted">No recorded signal at this point.</span>
              )}
            </div>
          </div>

          <div className="replay-bars" aria-label="Recent replay candles">
            {visibleBars.map((bar, index) => {
              const isUp = bar.close >= bar.open;
              const isCurrent = bar.timestamp === currentBar.timestamp;
              return (
                <div key={`${bar.timestamp}-${index}`} className={`replay-bar ${isCurrent ? 'current' : ''}`} title={`${formatETDateTime(bar.timestamp)} · ${fmt(bar.close)}`}>
                  <span className={`replay-bar-body ${isUp ? 'up' : 'down'}`} style={{ height: `${Math.max(10, Math.min(84, Math.abs(bar.close - bar.open) / Math.max(bar.high - bar.low, 0.01) * 84))}%` }} />
                  <span className="replay-bar-wick" />
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
