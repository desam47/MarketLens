import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { Dashboard } from './Dashboard';

jest.mock('../components/RegimeCard', () => ({ RegimeCard: () => <div>Regime card</div> }));
jest.mock('../components/MarketContextCard', () => ({ MarketContextCard: () => <div>Market context card</div> }));
jest.mock('../components/ConfluenceCard', () => ({ ConfluenceCard: () => <div>Confluence card</div> }));
jest.mock('../components/StrategyCard', () => ({ StrategyCard: () => <div>Strategy card</div> }));
jest.mock('../components/TrendCard', () => ({ TrendCard: () => <div>Trend card</div> }));
jest.mock('../components/TopMoversCard', () => ({ TopMoversCard: () => <div>Top movers card</div> }));
jest.mock('../components/NLSearchBar', () => ({ NLSearchBar: () => <div>Search card</div> }));
jest.mock('../components/DigestCard', () => ({ DigestCard: () => <div>Digest card</div> }));
jest.mock('../components/TransitionsMiniCard', () => ({ TransitionsMiniCard: () => <div>Transitions card</div> }));
jest.mock('../components/SymbolInput', () => ({ SymbolInput: () => <input aria-label="Symbol" /> }));
jest.mock('../components/FreshnessIndicator', () => ({ FreshnessIndicator: () => <span>Fresh</span> }));
jest.mock('../components/SkeletonBlock', () => ({ SkeletonBlock: () => <span /> }));

describe('Dashboard layouts', () => {
  beforeEach(() => {
    window.localStorage.clear();
    jest.spyOn(api, 'getRegime').mockResolvedValue({} as any);
    jest.spyOn(api, 'getSector').mockResolvedValue({} as any);
    jest.spyOn(api, 'getTrends').mockResolvedValue([]);
    jest.spyOn(api, 'getMTFSnapshot').mockResolvedValue({ snapshot: null } as any);
    jest.spyOn(api, 'getStrategy').mockResolvedValue({} as any);
    jest.spyOn(api, 'getMarketContext').mockResolvedValue({} as any);
    jest.spyOn(api, 'getPriceRange').mockResolvedValue({ price_history: [], latest_close: null, latest_close_timestamp: null, fetched_at: null, last_index: 0, symbol: 'SPY', timeframe: '1d', levels: [], count: 0 } as any);
    jest.spyOn(api, 'getQuote').mockResolvedValue({ price: 100, timestamp: null });
  });

  afterEach(() => jest.restoreAllMocks());

  it('customizes, saves, and restores a layout', async () => {
    render(<Dashboard symbol="SPY" onSymbolChange={jest.fn()} />);
    expect(screen.getByRole('heading', { name: 'Market Analysis Dashboard' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Customize' }));
    expect(screen.getByRole('heading', { name: 'Customize dashboard' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show Market context' }));
    fireEvent.change(screen.getByLabelText('Saved layout name'), { target: { value: 'My focused view' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save layout' }));

    await waitFor(() => expect((screen.getByRole('combobox', { name: 'Dashboard layout' }) as HTMLSelectElement).value).toMatch(/^custom_/));
    expect(screen.queryByText('Market context card')).not.toBeInTheDocument();
    expect(window.localStorage.getItem('marketlens.dashboard.layouts')).toContain('My focused view');

    fireEvent.click(screen.getByRole('button', { name: 'Customize' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show Market context' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save layout' }));
    expect(screen.getByText('Market context card')).toBeInTheDocument();
  });
});
