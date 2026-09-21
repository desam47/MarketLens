import React from 'react';
import { render, screen } from '@testing-library/react';
import api from '../services/api';
import { WatchlistPage } from './WatchlistPage';

jest.mock('../components/WatchlistTable', () => ({
  WatchlistTable: () => <div>Watchlist table</div>,
}));

describe('WatchlistPage empty and error states', () => {
  afterEach(() => jest.restoreAllMocks());

  it('guides the user when no watchlist exists yet', async () => {
    jest.spyOn(api, 'getWatchlists').mockResolvedValue([]);

    render(<WatchlistPage onSelectSymbol={jest.fn()} />);

    expect(await screen.findByText('No watchlists yet.')).toBeInTheDocument();
    expect(screen.getByText('Create one above to start organizing your symbols.')).toBeInTheDocument();
    expect(screen.queryByText('Watchlist table')).not.toBeInTheDocument();
  });

  it('shows a watchlist API error instead of an empty state', async () => {
    jest.spyOn(api, 'getWatchlists').mockRejectedValue(new Error('Watchlist service unavailable'));

    render(<WatchlistPage onSelectSymbol={jest.fn()} />);

    expect(await screen.findByText('Watchlist service unavailable')).toBeInTheDocument();
  });
});
