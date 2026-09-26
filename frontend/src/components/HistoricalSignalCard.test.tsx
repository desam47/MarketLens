import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { HistoricalSignalCard } from './HistoricalSignalCard';

describe('HistoricalSignalCard', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('offers every recorded timeframe and scopes regime research to the selected one', async () => {
    jest.spyOn(api, 'getWatchlists').mockResolvedValue([]);
    jest.spyOn(api, 'listSignals').mockResolvedValue([]);
    const performance = jest.spyOn(api, 'getRegimePerformance').mockResolvedValue([]);
    const counts = jest.spyOn(api, 'getSignalCountByRegime').mockResolvedValue([]);
    jest.spyOn(api, 'getSignalResearchSummary').mockResolvedValue({
      scope: { mode: 'all_active', watchlist_ids: [], watchlist_names: [], symbols: [] },
      timeframe: '1d', start_date: null, end_date: null, recorded: 40, complete: 30,
      timeframe_coverage: [], regime_coverage: { with_regime: 3, complete: 30 },
      performance: null, performance_note: null,
    });

    render(<HistoricalSignalCard />);

    await waitFor(() => expect(screen.getByLabelText('Regime coverage')).toHaveTextContent('3 of 30 complete outcomes carry a regime'));
    expect(screen.getByLabelText('Regime coverage')).toHaveTextContent('live snapshot');
    const options = Array.from((screen.getByLabelText('Historical signal timeframe') as HTMLSelectElement).options).map((option) => option.value);
    // Weekly is absent: the backend records no weekly signal, so offering it here
    // would be a filter that can only ever return zero rows.
    expect(options).toEqual(['1m', '2m', '3m', '5m', '15m', '30m', '1h', '4h', '1d']);
    expect(options).not.toContain('1wk');
    expect(performance).toHaveBeenLastCalledWith('all_active', undefined, '1d');

    fireEvent.change(screen.getByLabelText('Historical signal timeframe'), { target: { value: '4h' } });
    await waitFor(() => expect(performance).toHaveBeenLastCalledWith('all_active', undefined, '4h'));
    expect(counts).toHaveBeenLastCalledWith('all_active', undefined, '4h');
    expect(screen.getByText(/Regime at Recording \(4 Hour\)/)).toBeInTheDocument();
  });

  it('records only after the trader confirms the previewed scope, and has no manual delete', async () => {
    jest.spyOn(api, 'getWatchlists').mockResolvedValue([]);
    jest.spyOn(api, 'listSignals').mockResolvedValue([]);
    jest.spyOn(api, 'getRegimePerformance').mockResolvedValue([]);
    jest.spyOn(api, 'getSignalCountByRegime').mockResolvedValue([]);
    jest.spyOn(api, 'getSignalResearchSummary').mockRejectedValue(new Error('offline'));
    const record = jest.spyOn(api, 'recordSignalsNow')
      .mockResolvedValueOnce({ status: 'preview', symbols: ['AAPL', 'MSFT'], pairs: 20 })
      .mockResolvedValueOnce({ status: 'recorded', symbols: ['AAPL', 'MSFT'], pairs: 20, recorded: 7 });
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true);

    render(<HistoricalSignalCard />);
    expect(screen.queryByText(/Delete/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Record Now' }));
    await waitFor(() => expect(screen.getByText('Recorded 7 signal(s).')).toBeInTheDocument());
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('2 ingested symbol(s) across 20 symbol/timeframe pair(s)'));
    expect(record.mock.calls).toEqual([[false], [true]]);
  });

  it('writes nothing when the trader declines the preview', async () => {
    jest.spyOn(api, 'getWatchlists').mockResolvedValue([]);
    jest.spyOn(api, 'listSignals').mockResolvedValue([]);
    jest.spyOn(api, 'getRegimePerformance').mockResolvedValue([]);
    jest.spyOn(api, 'getSignalCountByRegime').mockResolvedValue([]);
    jest.spyOn(api, 'getSignalResearchSummary').mockRejectedValue(new Error('offline'));
    const record = jest.spyOn(api, 'recordSignalsNow').mockResolvedValue({ status: 'preview', symbols: ['AAPL'], pairs: 10 });
    jest.spyOn(window, 'confirm').mockReturnValue(false);

    render(<HistoricalSignalCard />);
    fireEvent.click(screen.getByRole('button', { name: 'Record Now' }));
    await waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record).toHaveBeenCalledWith(false);
  });
});
