import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { SignalResearchDashboard } from './SignalResearchDashboard';

describe('SignalResearchDashboard', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('summarizes completed outcomes and groups them by regime, trend, and timeframe', async () => {
    jest.spyOn(api, 'listSignals').mockResolvedValue([
      {
        id: 1, symbol: 'SPY', timestamp: '2026-09-01T15:00:00-04:00', timeframe: '1d', price: 100,
        trend_score: 70, trend_state: 'bullish', strength: 80, market_regime: 'risk_on',
        relative_strength: null, sector_alignment: null, volume_state: null, momentum: null, structure: null,
        confidence_inputs: null, strategy_version: null, data_quality: null,
        return_5b: 2, return_10b: 3, return_20b: 4, mfe: 3, mae: -1, created_at: null,
      },
      {
        id: 2, symbol: 'QQQ', timestamp: '2026-09-02T15:00:00-04:00', timeframe: '1d', price: 200,
        trend_score: 40, trend_state: 'bearish', strength: 50, market_regime: 'risk_off',
        relative_strength: null, sector_alignment: null, volume_state: null, momentum: null, structure: null,
        confidence_inputs: null, strategy_version: null, data_quality: null,
        return_5b: -1, return_10b: -2, return_20b: -3, mfe: 1, mae: -2, created_at: null,
      },
    ]);

    render(<SignalResearchDashboard />);

    await waitFor(() => expect(screen.getByText('Avg signal 5-bar return')).toBeInTheDocument());
    expect(screen.getByText('risk_on')).toBeInTheDocument();
    expect(screen.getByText('risk_off')).toBeInTheDocument();
    expect(screen.getAllByText('100.0%').length).toBeGreaterThan(0);
    expect(screen.getAllByText('1.50%').length).toBeGreaterThan(0);
    expect(screen.queryByText('Cumulative 5-bar signal return')).not.toBeInTheDocument();
    expect(screen.getByText(/not a strategy equity curve/)).toBeInTheDocument();
    expect(api.listSignals).toHaveBeenCalledWith(undefined, undefined, 1000, false);
  });
});
