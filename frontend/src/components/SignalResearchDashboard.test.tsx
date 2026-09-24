import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api, { SignalResearchSummary } from '../services/api';
import { SignalResearchDashboard } from './SignalResearchDashboard';

const scope = { mode: 'all_active' as const, watchlist_ids: [1], watchlist_names: ['Default'], symbols: ['SPY', 'QQQ'] };
const coverage = [
  { timeframe: '1m', recorded: 900, complete: 850 },
  { timeframe: '1d', recorded: 12, complete: 10 },
];

const dailySummary: SignalResearchSummary = {
  scope, timeframe: '1d', start_date: null, end_date: null,
  recorded: 12, complete: 10,
  timeframe_coverage: [coverage[1]],
  regime_coverage: { with_regime: 2, complete: 10 },
  performance: {
    label: 'all', complete: 10, directional: 8, win_rate: 0.625,
    avg_signal_return_5b: 1.5, avg_signal_return_10b: 2.25,
    by_regime: [
      { label: 'not recorded', complete: 8, directional: 6, win_rate: 0.5, avg_signal_return_5b: 1, avg_signal_return_10b: 2 },
      { label: 'risk_on', complete: 2, directional: 2, win_rate: 1, avg_signal_return_5b: 3, avg_signal_return_10b: 3 },
    ],
    by_trend: [
      { label: 'bullish', complete: 5, directional: 5, win_rate: 0.6, avg_signal_return_5b: 2, avg_signal_return_10b: 2 },
      { label: 'neutral', complete: 2, directional: 0, win_rate: null, avg_signal_return_5b: null, avg_signal_return_10b: null },
    ],
  },
  performance_note: null,
};

describe('SignalResearchDashboard', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('shows metrics for the whole filtered population of one timeframe', async () => {
    jest.spyOn(api, 'getWatchlists').mockResolvedValue([]);
    jest.spyOn(api, 'getSignalResearchSummary').mockResolvedValue(dailySummary);

    render(<SignalResearchDashboard />);

    await waitFor(() => expect(screen.getByText('Avg signal 5-bar return')).toBeInTheDocument());
    expect(api.getSignalResearchSummary).toHaveBeenCalledWith(expect.objectContaining({ scope: 'all_active', timeframe: '1d' }));
    expect(screen.getByLabelText('Research coverage')).toHaveTextContent('Default · 2 enabled symbols');
    expect(screen.getByLabelText('Research coverage')).toHaveTextContent('10 complete outcomes of 12 recorded signals; metrics cover all of them');
    expect(screen.getByText('62.5%')).toBeInTheDocument();
    expect(screen.getAllByText('1.50%').length).toBeGreaterThan(0);
    expect(screen.getByText('not recorded')).toBeInTheDocument();
    expect(screen.getByLabelText('Regime coverage')).toHaveTextContent('2 of 10 complete outcomes (20.00%)');
    expect(screen.getByText(/not a strategy equity curve/)).toBeInTheDocument();
    expect(screen.queryByText(/Next page/)).not.toBeInTheDocument();
  });

  it('offers every recorded timeframe and shows coverage, not returns, across all of them', async () => {
    jest.spyOn(api, 'getWatchlists').mockResolvedValue([]);
    const summarySpy = jest.spyOn(api, 'getSignalResearchSummary').mockResolvedValue(dailySummary);

    render(<SignalResearchDashboard />);
    await waitFor(() => expect(screen.getByText('Avg signal 5-bar return')).toBeInTheDocument());

    const options = Array.from((screen.getByLabelText('Research timeframe') as HTMLSelectElement).options).map((option) => option.value);
    expect(options).toEqual(['1m', '2m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '1wk', 'all']);

    summarySpy.mockResolvedValue({
      ...dailySummary, timeframe: null, recorded: 912, complete: 860, timeframe_coverage: coverage,
      performance: null, performance_note: 'Choose one timeframe to see performance.',
    });
    fireEvent.change(screen.getByLabelText('Research timeframe'), { target: { value: 'all' } });

    await waitFor(() => expect(screen.getByLabelText('Performance note')).toHaveTextContent('Choose one timeframe'));
    expect(summarySpy).toHaveBeenLastCalledWith(expect.objectContaining({ timeframe: undefined }));
    expect(screen.queryByText('Directional win rate')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Coverage by timeframe')).toHaveTextContent('850');
  });
});
