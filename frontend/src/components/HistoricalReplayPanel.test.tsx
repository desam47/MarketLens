import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { HistoricalReplayPanel } from './HistoricalReplayPanel';

describe('HistoricalReplayPanel', () => {
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
      return_5b: null,
      return_10b: null,
      return_20b: null,
      mfe: null,
      mae: null,
      created_at: null,
    }]);

    render(<HistoricalReplayPanel />);

    await waitFor(() => expect(screen.getByText('1 / 3')).toBeInTheDocument());
    expect(screen.getByText('No recorded signal at this point.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('2 / 3')).toBeInTheDocument();
    expect(screen.getByText('bullish')).toBeInTheDocument();
    expect(screen.getByText(/Score 72\.0/)).toBeInTheDocument();
  });
});
