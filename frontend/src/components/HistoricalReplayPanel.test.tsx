import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { HistoricalReplayPanel } from './HistoricalReplayPanel';
import { SESSION_PREFERENCE_KEY } from '../utils/marketSession';

describe('HistoricalReplayPanel', () => {
  beforeEach(() => {
    jest.spyOn(api, 'getSymbolCatalog').mockResolvedValue(['SPY']);
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('replays bars chronologically and reveals only the signal known at the current bar', async () => {
    jest.spyOn(api, 'getAnalysisBars').mockResolvedValue({
      symbol: 'SPY',
      timeframe: '1d',
      count: 3,
      bars: [
        { timestamp: '2026-09-03T15:00:00-04:00', open: 103, high: 105, low: 102, close: 104, volume: 300 },
        { timestamp: '2026-09-02T15:00:00-04:00', open: 101, high: 103, low: 100, close: 102, volume: 200 },
        { timestamp: '2026-09-01T15:00:00-04:00', open: 99, high: 101, low: 98, close: 100, volume: 100 },
      ],
    });
    jest.spyOn(api, 'listSignals').mockResolvedValue([{
      id: 1,
      symbol: 'SPY',
      timestamp: '2026-09-02T15:00:00-04:00',
      timeframe: '1d',
      price: 102,
      trend_score: 72,
      trend_state: 'bullish',
      strength: 81,
      market_regime: 'risk_on',
      relative_strength: null,
      sector_alignment: null,
      volume_state: null,
      momentum: null,
      structure: null,
      confidence_inputs: null,
      strategy_version: null,
      data_quality: null,
      return_5b: 2.5,
      return_10b: 4.1,
      return_20b: 6.3,
      mfe: 5.8,
      mae: -1.2,
      created_at: null,
    }]);

    render(<HistoricalReplayPanel />);

    await waitFor(() => expect(screen.getByText('1 / 3')).toBeInTheDocument());
    expect(screen.getByText('No recorded signal at this point.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('2 / 3')).toBeInTheDocument();
    expect(screen.getByText('bullish')).toBeInTheDocument();
    expect(screen.getByText(/Score 72\.0/)).toBeInTheDocument();
    expect(screen.getByText(/5-bar \+2\.50%/)).toBeInTheDocument();
    expect(screen.getByText('100.0%')).toBeInTheDocument();
    expect(screen.getByLabelText('Signal: bullish')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Replay start date'), { target: { value: '2026-09-02' } });
    await waitFor(() => expect(screen.getByText('1 / 2')).toBeInTheDocument());
  });

  it('trades only bullish or bearish calls and scopes signals to the replayed session', async () => {
    window.localStorage.setItem(SESSION_PREFERENCE_KEY, 'all');
    const signal = (id: number, timestamp: string, trendState: string | null) => ({
      id, symbol: 'SPY', timestamp, timeframe: '1h', price: 100, trend_score: 10, trend_state: trendState,
      strength: 50, market_regime: null, relative_strength: null, sector_alignment: null, volume_state: null,
      momentum: null, structure: null, confidence_inputs: null, strategy_version: null, data_quality: null,
      return_5b: null, return_10b: null, return_20b: null, mfe: null, mae: null, created_at: null,
    });
    jest.spyOn(api, 'getAnalysisBars').mockResolvedValue({
      symbol: 'SPY',
      timeframe: '1h',
      count: 4,
      bars: [
        { timestamp: '2026-09-02T08:00:00-04:00', open: 100, high: 100.5, low: 99.5, close: 100, volume: 10, session: 'premarket' },
        { timestamp: '2026-09-02T10:00:00-04:00', open: 100, high: 101, low: 99.8, close: 100, volume: 10, session: 'regular' },
        { timestamp: '2026-09-02T11:00:00-04:00', open: 100, high: 103, low: 99.9, close: 102, volume: 10, session: 'regular' },
        { timestamp: '2026-09-02T12:00:00-04:00', open: 102, high: 104, low: 101, close: 103, volume: 10, session: 'regular' },
      ],
    });
    jest.spyOn(api, 'listSignals').mockResolvedValue([
      signal(1, '2026-09-02T08:00:00-04:00', 'bullish'),
      signal(2, '2026-09-02T10:00:00-04:00', 'neutral'),
      signal(3, '2026-09-02T11:00:00-04:00', 'bullish'),
    ] as any);

    render(<HistoricalReplayPanel />);
    await waitFor(() => expect(screen.getByText('1 / 4')).toBeInTheDocument());

    // All sessions: three signals, but the neutral one is never traded.
    expect(screen.getByLabelText('Replay performance summary')).toHaveTextContent('Signals3');
    expect(screen.getByLabelText('Replay performance summary')).toHaveTextContent('Simulated trades1 / 2');
    expect(screen.getByLabelText('Simulation assumptions')).toHaveTextContent('neutral and warm-up signals are not traded');

    // Regular session only: the premarket bar and its signal leave together.
    fireEvent.change(screen.getByLabelText('Replay market session'), { target: { value: 'regular' } });
    await waitFor(() => expect(screen.getByText('1 / 3')).toBeInTheDocument());
    expect(screen.getByLabelText('Replay performance summary')).toHaveTextContent('Signals2');
    expect(screen.getByLabelText('Replay performance summary')).toHaveTextContent('Simulated trades0 / 1');
  });
});
