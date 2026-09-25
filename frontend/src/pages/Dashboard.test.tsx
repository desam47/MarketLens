import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { Dashboard } from './Dashboard';

jest.mock('../components/MarketContextCard', () => ({ MarketContextCard: () => <div>Market context card</div> }));
jest.mock('../components/TopMoversCard', () => ({ TopMoversCard: () => <div>Top movers card</div> }));
jest.mock('../components/NLSearchBar', () => ({ NLSearchBar: () => <div>Search card</div> }));
jest.mock('../components/DigestCard', () => ({ DigestCard: () => <div>Digest card</div> }));
jest.mock('../components/SkeletonBlock', () => ({ SkeletonBlock: () => <span /> }));

describe('Dashboard layouts', () => {
  beforeEach(() => {
    window.localStorage.clear();
    jest.spyOn(api, 'getMarketContext').mockResolvedValue({} as any);
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

  it('repairs a saved layout that names sections which have since moved away', () => {
    // Written when the Dashboard still carried the symbol-scoped sections.
    // Those names must be dropped without discarding the layout itself.
    window.localStorage.setItem(
      'marketlens.dashboard.layouts',
      JSON.stringify([
        {
          id: 'custom_legacy',
          name: 'Legacy view',
          visible: ['regime', 'trends', 'digest', 'movers'],
          order: ['regime', 'trends', 'digest', 'movers'],
        },
      ]),
    );
    window.localStorage.setItem('marketlens.dashboard.layout', 'custom_legacy');

    render(<Dashboard symbol="SPY" onSymbolChange={jest.fn()} />);

    const select = screen.getByRole('combobox', { name: 'Dashboard layout' }) as HTMLSelectElement;
    expect(select.value).toBe('custom_legacy');
    expect(screen.getByRole('option', { name: 'Legacy view' })).toBeInTheDocument();
    // Its surviving visible sections still render...
    expect(screen.getByText('Digest card')).toBeInTheDocument();
    expect(screen.getByText('Top movers card')).toBeInTheDocument();
    // ...and sections it never named remain available in the editor.
    fireEvent.click(screen.getByRole('button', { name: 'Customize' }));
    expect(screen.getByRole('checkbox', { name: 'Show Market context' })).toBeInTheDocument();
  });
});
