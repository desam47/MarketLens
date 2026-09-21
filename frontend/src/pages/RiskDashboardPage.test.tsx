import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { RiskDashboardPage } from './RiskDashboardPage';

describe('RiskDashboardPage', () => {
  beforeEach(() => {
    window.localStorage.clear();
    jest.spyOn(api, 'getQuote').mockResolvedValue({ price: 110, timestamp: '2026-01-02T15:00:00Z' });
    jest.spyOn(api, 'getAnalysisBars').mockResolvedValue({
      symbol: 'AAPL', timeframe: '1d', count: 4,
      bars: [
        { timestamp: '2026-01-01T00:00:00Z', open: 100, high: 101, low: 99, close: 100, volume: 1000 },
        { timestamp: '2026-01-02T00:00:00Z', open: 100, high: 106, low: 99, close: 105, volume: 1000 },
        { timestamp: '2026-01-03T00:00:00Z', open: 105, high: 111, low: 104, close: 110, volume: 1000 },
        { timestamp: '2026-01-04T00:00:00Z', open: 110, high: 111, low: 109, close: 110, volume: 1000 },
      ],
    });
    jest.spyOn(api, 'getFundamentals').mockResolvedValue({
      symbol: 'AAPL', provider: 'test', timestamp: '2026-01-02T15:00:00Z',
      data: { symbol: 'AAPL', company_name: 'Apple', sector: 'Technology', industry: null, market_cap: null, shares_outstanding: null, revenue: null, net_income: null, eps: null, eps_growth: null, pe_ratio: null, forward_pe: null, peg_ratio: null, price_to_book: null, price_to_sales: null, total_debt: null, total_cash: null, debt_to_equity: null, current_ratio: null, dividend_yield: null, payout_ratio: null, institutional_ownership: null, insider_ownership: null, short_float: null, analyst_target: null, recommendation: null, beta: null, week_52_high: null, week_52_low: null },
    });
    jest.spyOn(api, 'getSymbolCalendar').mockResolvedValue({ symbol: 'AAPL', events: [], provider: 'test', timestamp: '2026-01-02T15:00:00Z' });
  });

  afterEach(() => jest.restoreAllMocks());

  it('starts with a useful empty state', () => {
    render(<RiskDashboardPage />);
    expect(screen.getByRole('heading', { name: 'Risk Dashboard' })).toBeInTheDocument();
    expect(screen.getByText('No positions yet')).toBeInTheDocument();
  });

  it('adds a position and calculates exposure, stop risk, and sector', async () => {
    render(<RiskDashboardPage />);
    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'AAPL' } });
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Entry price'), { target: { value: '100' } });
    fireEvent.change(screen.getByLabelText(/Stop price/), { target: { value: '95' } });
    fireEvent.click(screen.getByRole('button', { name: '+ Add position' }));

    await waitFor(() => expect(screen.getByText('AAPL')).toBeInTheDocument());
    expect(screen.getAllByText('$1,100').length).toBeGreaterThan(0);
    expect(screen.getAllByText('$50').length).toBeGreaterThan(0);
    expect(screen.getByText('Technology')).toBeInTheDocument();
    expect(window.localStorage.getItem('marketlens.risk.positions')).toContain('AAPL');
  });

  it('flags a position with earnings inside the selected risk window', async () => {
    const earningsDate = new Date();
    earningsDate.setDate(earningsDate.getDate() + 2);
    const date = earningsDate.toISOString().slice(0, 10);
    jest.spyOn(api, 'getSymbolCalendar').mockResolvedValue({
      symbol: 'AAPL', provider: 'test', timestamp: '2026-01-02T15:00:00Z',
      events: [{ symbol: 'AAPL', event_type: 'earnings', date, source: 'test' }],
    });

    render(<RiskDashboardPage />);
    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'AAPL' } });
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Entry price'), { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: '+ Add position' }));

    await waitFor(() => expect(document.querySelector('.risk-earnings-warning')).toHaveTextContent('AAPL reports in 2 days'));
    expect(screen.getByText('In 2d')).toBeInTheDocument();
  });
});
