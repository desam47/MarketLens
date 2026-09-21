import React from 'react';
import { render, screen } from '@testing-library/react';
import api from '../services/api';
import { TopMoversCard } from './TopMoversCard';

describe('TopMoversCard empty and unavailable states', () => {
  afterEach(() => jest.restoreAllMocks());

  it('explains when no scanner matches are available', async () => {
    jest.spyOn(api, 'getTopMoversCombined').mockResolvedValue({ bullish: [], bearish: [] } as any);

    render(<TopMoversCard onSelectSymbol={jest.fn()} autoRefresh={false} />);

    expect(await screen.findByText('No bullish signals — add symbols to a watchlist')).toBeInTheDocument();
    expect(screen.getByText('No bearish signals — add symbols to a watchlist')).toBeInTheDocument();
  });

  it('surfaces a provider failure without hiding the card', async () => {
    jest.spyOn(api, 'getTopMoversCombined').mockRejectedValue(new Error('Provider unavailable'));

    render(<TopMoversCard autoRefresh={false} />);

    expect(await screen.findByText('Top movers unavailable: Provider unavailable')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Top Movers' })).toBeInTheDocument();
  });
});
