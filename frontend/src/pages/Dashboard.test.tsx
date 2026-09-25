import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { Dashboard } from './Dashboard';

jest.mock('../components/RegimeCard', () => ({ RegimeCard: () => <div>Regime card</div> }));
jest.mock('../components/MarketContextCard', () => ({ MarketContextCard: () => <div>Market context card</div> }));
jest.mock('../components/ConfluenceCard', () => ({
  ConfluenceCard: ({ onPresetChange }: any) => (
    <div>
      {['scalper', 'day_trading', 'swing', 'all'].map(preset => (
        <button key={preset} onClick={() => onPresetChange(preset)}>Use {preset} preset</button>
      ))}
    </div>
  ),
}));
jest.mock('../components/StrategyCard', () => ({ StrategyCard: () => <div>Strategy card</div> }));
jest.mock('../components/TrendCard', () => ({
  TrendCard: ({ trend, confluenceRole }: any) => (
    <div data-testid={`trend-${trend.timeframe}`}>{trend.timeframe}:{confluenceRole}</div>
  ),
}));
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

  it('shows a helpful empty state when the dashboard has no trend data', async () => {
    render(<Dashboard symbol="SPY" onSymbolChange={jest.fn()} />);

    expect(await screen.findByText('No trend data available')).toBeInTheDocument();
  });

  it('keeps the dashboard usable when trend loading fails', async () => {
    jest.spyOn(api, 'getTrends').mockRejectedValue(new Error('Trend provider timed out'));

    render(<Dashboard symbol="SPY" onSymbolChange={jest.fn()} />);

    expect(await screen.findByText(/Failed to load trends: Trend provider timed out/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Market Analysis Dashboard' })).toBeInTheDocument();
  });

  it('keeps Trend by Timeframe aligned with the selected Confluence preset', async () => {
    const allTimeframes = ['1m', '2m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '1wk'];
    const trendRows = allTimeframes.map(timeframe => ({
      symbol: 'SPY', timeframe, direction: 'uptrend', strength: 'moderate', confidence: 0.7,
      timestamp: null, data_status: 'ok', provider: 'webull', session: 'regular', bar_closed: true,
    }));
    const snapshot = (preset: string, timeframes: string[]) => ({
      snapshot: {
        symbol: 'SPY', preset, direction: 'bullish', strength: 0.7, alignment_score: 0.7,
        timeframe_snapshots: Object.fromEntries(timeframes.map(timeframe => [timeframe, {
          direction: 'bullish', score: 50, strength: 0.7, confidence: 0.7, timestamp: null,
          data_quality: 'ok', data_age_seconds: 0, bar_closed: true, is_warmed_up: true,
          valid: true, quality_weight: 0.7,
        }])),
      },
    });
    (api.getTrends as jest.Mock).mockResolvedValue(trendRows);
    const presetInputs: Record<string, string[]> = {
      scalper: ['1m', '2m', '3m', '5m', '15m'],
      day_trading: ['5m', '15m', '30m', '1h', '4h'],
      swing: ['15m', '1h', '4h', '1d', '1wk'],
      all: ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1wk'],
    };
    (api.getMTFSnapshot as jest.Mock).mockImplementation((_symbol: string, preset: string) => Promise.resolve(
      snapshot(preset, presetInputs[preset])
    ));

    render(<Dashboard symbol="SPY" onSymbolChange={jest.fn()} />);

    expect(await screen.findByText('Day Trading Confluence uses 5 timeframes.')).toBeInTheDocument();
    expect(screen.getByTestId('trend-5m')).toHaveTextContent('5m:input');
    expect(screen.queryByTestId('trend-1m')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Structure · 15m–1h' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
    expect(screen.getByTestId('trend-1m')).toHaveTextContent('1m:out_of_scope');
    expect(screen.getByTestId('trend-4h')).toHaveTextContent('4h:input');
    expect(screen.getByRole('heading', { name: 'Bias · 4h–Weekly' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Use swing preset' }));
    await screen.findByText('Swing Trading Confluence uses 5 timeframes.');
    expect(screen.getByTestId('trend-1m')).toHaveTextContent('1m:out_of_scope');
    expect(screen.getByTestId('trend-1d')).toHaveTextContent('1d:input');

    fireEvent.click(screen.getByRole('button', { name: 'Use scalper preset' }));
    await screen.findByText('Scalper Confluence uses 5 timeframes.');
    expect(screen.getByTestId('trend-1m')).toHaveTextContent('1m:input');
    expect(screen.getByTestId('trend-30m')).toHaveTextContent('30m:out_of_scope');

    fireEvent.click(screen.getByRole('button', { name: 'Use all preset' }));
    await screen.findByText('All Timeframes Confluence uses 8 timeframes.');
    expect(screen.getByTestId('trend-2m')).toHaveTextContent('2m:out_of_scope');
    expect(screen.getByTestId('trend-1wk')).toHaveTextContent('1wk:input');

    fireEvent.click(screen.getByRole('button', { name: 'Current preset' }));
    expect(screen.queryByTestId('trend-2m')).not.toBeInTheDocument();
    expect(screen.getByTestId('trend-1wk')).toHaveTextContent('1wk:input');
  });
});
