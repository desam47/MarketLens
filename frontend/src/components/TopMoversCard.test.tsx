import React from 'react';
import { act, render, screen } from '@testing-library/react';
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

  it('updates displayed price and change from the live quote stream', async () => {
    let onEvent: ((event: any) => void) | undefined;
    const subscriber = {
      onEvent: jest.fn((listener: (event: any) => void) => {
        onEvent = listener;
        return jest.fn();
      }),
      onStatus: jest.fn((listener: (status: string) => void) => {
        listener('open');
        return jest.fn();
      }),
      subscribeQuote: jest.fn(),
      unsubscribeQuote: jest.fn(),
      disconnect: jest.fn(),
    };
    jest.spyOn(api, 'createRealtimeSubscriber').mockReturnValue(subscriber as any);
    jest.spyOn(api, 'getTopMoversCombined').mockResolvedValue({
      bullish: [{
        symbol: 'AAPL',
        quote: { symbol: 'AAPL', price: 100, bid: null, ask: null, volume: 1, timestamp: null, provider: 'webull' },
        change: 10,
        change_pct: 11.11,
        indicator_values: {}, scores: {}, total_score: 1, rank: 1,
        signals: [], trend_signals: {}, timestamp: '2026-01-01T12:00:00Z',
      }],
      bearish: [],
    } as any);

    render(<TopMoversCard onSelectSymbol={jest.fn()} autoRefresh={false} />);

    expect(await screen.findByText('+11.11%')).toBeInTheDocument();
    act(() => {
      onEvent?.({
        type: 'quote_update',
        symbol: 'AAPL',
        data: {
          price: 105, volume: 2, bid: 104.9, ask: 105.1, bid_size: 10, ask_size: 12,
          timestamp: '2026-01-01T12:01:00Z', received_at: Date.now(), provider: 'webull', event_type: 'trade',
        },
      });
    });

    expect(screen.getByText('+16.67%')).toBeInTheDocument();
    expect(screen.queryByText('$105')).not.toBeInTheDocument();
    expect(screen.getByText('LIVE')).toBeInTheDocument();
    expect(api.getTopMoversCombined).toHaveBeenCalledTimes(1);
    expect(subscriber.subscribeQuote).toHaveBeenCalledWith('AAPL');
  });
});
