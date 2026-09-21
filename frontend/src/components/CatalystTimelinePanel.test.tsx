import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { CatalystTimelinePanel } from './CatalystTimelinePanel';

test('combines technical, news, fundamentals, and options events chronologically', async () => {
  jest.spyOn(api, 'getNews').mockResolvedValue({
    symbol: 'AAPL', provider: 'test-news', timestamp: '2026-09-20T19:00:00-04:00',
    items: [{
      headline: 'AAPL launches new product', source: 'Wire', timestamp: '2026-09-20T18:30:00-04:00',
      symbol: 'AAPL', relevance: 0.9, url: 'https://example.com/aapl',
    }],
  });
  jest.spyOn(api, 'getFundamentals').mockResolvedValue({
    symbol: 'AAPL', provider: 'test-fundamentals', timestamp: '2026-09-20T17:00:00-04:00',
    data: {
      symbol: 'AAPL', company_name: 'Apple', sector: 'Technology', industry: null,
      market_cap: 1_000_000_000, shares_outstanding: null, revenue: null, net_income: null,
      eps: null, eps_growth: null, pe_ratio: 25, forward_pe: null, peg_ratio: null,
      price_to_book: null, price_to_sales: null, total_debt: null, total_cash: null,
      debt_to_equity: null, current_ratio: null, dividend_yield: null, payout_ratio: null,
      institutional_ownership: null, insider_ownership: 0.01, short_float: null,
      analyst_target: 210, recommendation: 'buy', beta: null, week_52_high: null, week_52_low: null,
    },
  });
  jest.spyOn(api, 'getOptions').mockResolvedValue({
    symbol: 'AAPL', provider: 'test-options', timestamp: '2026-09-20T16:00:00-04:00',
    expirations: ['2026-10-16'], near_term_iv: 0.3, iv_rank: 50,
    chains: [{
      symbol: 'AAPL', expiration: '2026-10-16', calls: [], puts: [], put_call_ratio: 0.7,
      total_call_volume: 100, total_put_volume: 70, avg_iv_call: 0.3, avg_iv_put: 0.32,
      unusual_activity: 'elevated',
    }],
  });

  render(<CatalystTimelinePanel
    symbol="AAPL"
    scanResult={{
      symbol: 'AAPL', timestamp: '2026-09-20T19:30:00-04:00', quote: { provider: 'test', price: 200, bid: null, ask: null, volume: 1000, timestamp: null, symbol: 'AAPL' },
      change: 2, change_pct: 1, indicator_values: {}, scores: {}, total_score: 30, rank: null,
      signals: ['BREAKOUT'], trend_signals: {},
    }}
  />);

  await waitFor(() => expect(screen.getByText('AAPL launches new product')).toBeInTheDocument());
  expect(screen.getByText(/Technical reaction bullish/)).toBeInTheDocument();
  expect(screen.getByText(/Fundamental snapshot/)).toBeInTheDocument();
  expect(screen.getByText(/Options activity · elevated/)).toBeInTheDocument();
});
