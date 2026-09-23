import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { WatchlistIntelligence } from './WatchlistIntelligence';

const intelligence = {
  watchlist_id: 7,
  generated_at: '2026-09-23T09:45:00-04:00',
  data_status: 'ready',
  watchlist_size: 2,
  analyzed_symbols: 2,
  session_scope: 'premarket',
  price_basis: 'latest selected-session 1-minute bar and its regular-session baseline',
  missing_symbols: [],
  top_bullish: [{ symbol: 'AAPL', price: 202, change_pct: 2, timestamp: null, session: 'premarket', score: 3, signals: [], metric: 2, metric_label: 'change %', details: {} }],
  top_bearish: [],
  breakouts: [],
  deteriorating: [],
  volume_spikes: [],
  relative_strength: [],
  mtf_alignment: [],
  sector_rotation: [{ sector: 'Technology', average_change_pct: 1.5, advancing: 2, declining: 0, symbols: ['AAPL', 'NVDA'] }],
  warnings: ['Sector momentum covers the built-in sector map only.'],
};

describe('WatchlistIntelligence', () => {
  it('renders a session-aware, grounded briefing and routes symbols to their page', () => {
    const onSelectSymbol = jest.fn();
    render(
      <WatchlistIntelligence
        data={intelligence}
        events={[{ symbol: 'AAPL', event_type: 'earnings', date: '2026-09-28', source: 'yfinance' }]}
        onSelectSymbol={onSelectSymbol}
      />,
    );

    expect(screen.getByRole('heading', { name: 'Watchlist Intelligence' })).toBeInTheDocument();
    expect(screen.getByText(/Grounded scanner snapshot.*Premarket/)).toBeInTheDocument();
    expect(screen.getByText('Top Bullish')).toBeInTheDocument();
    expect(screen.getByText('+2.00%')).toBeInTheDocument();
    expect(screen.getByText('Technology')).toBeInTheDocument();
    expect(screen.getByText(/Dates are provider estimates/i)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: /AAPL/i })[0]);
    expect(onSelectSymbol).toHaveBeenCalledWith('AAPL');
  });

  it('does not hide a no-session scope behind all-session results', () => {
    render(<WatchlistIntelligence data={{ ...intelligence, session_scope: 'none', top_bullish: [] }} events={[]} onSelectSymbol={jest.fn()} />);

    expect(screen.getByText(/No session selected/)).toBeInTheDocument();
    expect(screen.getByText('No positive price moves in this scope.')).toBeInTheDocument();
  });
});
