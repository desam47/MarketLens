/**
 * WatchlistTable — column toggle, RS benchmark selector, keyboard handling.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WatchlistTable } from './WatchlistTable';
import { rsCellLabel } from './watchlistUtils';
import api, { RelativeStrengthSignal } from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: {
    getWatchlistScan: jest.fn(),
    getBatchRelativeStrength: jest.fn(),
    updateWatchlistSymbol: jest.fn(),
    removeSymbolFromWatchlist: jest.fn(),
  },
}));

const mockApi = api as unknown as {
  getWatchlistScan: jest.Mock;
  getBatchRelativeStrength: jest.Mock;
};

const rsSignal = (symbol: string, benchmark: string, rs_pct: number): RelativeStrengthSignal => ({
  symbol,
  benchmark,
  rs_pct,
  classification: 'outperformer',
  symbol_return_pct: rs_pct,
  benchmark_return_pct: 0,
  lookback_days: 20,
  timestamp: null,
});

function scanResult(symbol: string) {
  return {
    symbol,
    quote: { price: 100 },
    change: 1,
    change_pct: 1,
    indicator_values: {},
    scores: {},
    total_score: 0,
    rank: null,
    signals: [],
    trend_signals: {},
    timestamp: '2026-01-01T12:00:00Z',
    is_enabled: true,
    entity_type: 'stock',
  };
}

function mockData(symbols: string[]) {
  mockApi.getWatchlistScan.mockResolvedValue({
    timestamp: '2026-01-01T12:00:00Z',
    count: symbols.length,
    results: symbols.map(scanResult),
  });
  mockApi.getBatchRelativeStrength.mockResolvedValue({
    results: Object.fromEntries(
      symbols.map(s => [s, {
        symbol: s,
        signals: [rsSignal(s, 'SPY', 1.5), rsSignal(s, 'QQQ', 7.25)],
        count: 2,
      }]),
    ),
  });
}

async function renderTable(symbols: string[], onSelectSymbol = jest.fn()) {
  mockData(symbols);
  const utils = render(<WatchlistTable watchlistId={1} onSelectSymbol={onSelectSymbol} />);
  await screen.findByText(`${symbols.length} symbols`, { exact: false });
  return { ...utils, onSelectSymbol };
}

// The virtualized list is a CSS grid of <div>s with no ARIA roles, so these
// helpers reach into the DOM directly.
/* eslint-disable testing-library/no-node-access */
const getVirtRow = (symbol: string) => screen.getByText(symbol).closest('.virt-row') as HTMLElement;
const getVirtHeader = () => document.querySelector('.watchlist-virt-header') as HTMLElement;
const cellCount = (el: HTMLElement) => el.children.length;
/* eslint-enable testing-library/no-node-access */

const manySymbols = () => Array.from({ length: 31 }, (_, i) => `SYM${String(i).padStart(2, '0')}`);

beforeEach(() => {
  jest.clearAllMocks();
});

describe('RS benchmark selector', () => {
  it('switches the RS column without re-fetching', async () => {
    await renderTable(['AAPL']);
    expect(screen.getByText(rsCellLabel(rsSignal('AAPL', 'SPY', 1.5)))).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByTitle('Relative Strength benchmark'), 'QQQ');

    expect(screen.getByText(rsCellLabel(rsSignal('AAPL', 'QQQ', 7.25)))).toBeInTheDocument();
    expect(mockApi.getWatchlistScan).toHaveBeenCalledTimes(1);
    expect(mockApi.getBatchRelativeStrength).toHaveBeenCalledTimes(1);
  });
});

describe('row keyboard handling', () => {
  it('selects the symbol on Enter/Space on the row itself', async () => {
    const { onSelectSymbol } = await renderTable(['AAPL']);
    const row = screen.getByRole('row', { name: /AAPL/ });

    fireEvent.keyDown(row, { key: 'Enter' });
    fireEvent.keyDown(row, { key: ' ' });

    expect(onSelectSymbol).toHaveBeenCalledTimes(2);
    expect(onSelectSymbol).toHaveBeenCalledWith('AAPL');
  });

  it('ignores keys bubbling up from the row action buttons', async () => {
    const { onSelectSymbol } = await renderTable(['AAPL']);
    const row = screen.getByRole('row', { name: /AAPL/ });

    const deleteBtn = within(row).getByTitle('Remove from watchlist');
    fireEvent.keyDown(deleteBtn, { key: 'Enter' });
    fireEvent.keyDown(within(row).getByTitle('Disable symbol'), { key: ' ' });

    expect(onSelectSymbol).not.toHaveBeenCalled();
  });

  it('ignores keys bubbling up from the action buttons in the virtualized list', async () => {
    const { onSelectSymbol } = await renderTable(manySymbols());
    const row = getVirtRow('SYM00');

    fireEvent.keyDown(within(row).getByTitle('Remove from watchlist'), { key: 'Enter' });
    expect(onSelectSymbol).not.toHaveBeenCalled();

    fireEvent.keyDown(row, { key: 'Enter' });
    expect(onSelectSymbol).toHaveBeenCalledWith('SYM00');
  });
});

describe('column toggle', () => {
  const hideColumn = async (label: string) => {
    await userEvent.click(screen.getByTitle('Toggle columns'));
    await userEvent.click(screen.getByLabelText(label));
  };

  it('keeps widths on the right column when an earlier column is hidden (regular table)', async () => {
    await renderTable(['AAPL']);
    await hideColumn('Type');

    expect(screen.queryByRole('columnheader', { name: /Type/ })).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /Price/ })).toHaveStyle({ width: '110px' });
    expect(screen.getByRole('columnheader', { name: /Trend/ })).toHaveStyle({ width: '320px' });
  });

  it('renders one cell per visible column in the virtualized list', async () => {
    await renderTable(manySymbols());
    await hideColumn('Type');

    const header = getVirtHeader();
    const row = getVirtRow('SYM00');
    expect(cellCount(header)).toBe(6);
    expect(cellCount(row)).toBe(6);
    // Header and rows must share the same (6-track) grid template.
    expect(header.style.gridTemplateColumns).toBe(row.style.gridTemplateColumns);
    expect(row.style.gridTemplateColumns.split(' ').length).toBeGreaterThanOrEqual(6);
    expect(row.style.gridTemplateColumns).not.toContain('64px');
  });
});

describe('narrow viewport', () => {
  const original = window.matchMedia;
  afterEach(() => {
    window.matchMedia = original;
  });

  it('drops the Trend column and keeps the rest', async () => {
    window.matchMedia = jest.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
    })) as unknown as typeof window.matchMedia;

    await renderTable(['AAPL']);

    expect(screen.queryByRole('columnheader', { name: /Trend/ })).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /Rel\. Strength/ })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /Actions/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole('columnheader')).toHaveLength(6));
  });
});

describe('empty and error states', () => {
  it('explains when the selected watchlist has no symbols', async () => {
    await renderTable([]);

    expect(screen.getByText('No symbols in this watchlist.')).toBeInTheDocument();
  });

  it('shows a failed or timed-out scanner request instead of an empty table', async () => {
    mockApi.getWatchlistScan.mockRejectedValue(new Error('Watchlist scan timed out'));

    render(<WatchlistTable watchlistId={1} onSelectSymbol={jest.fn()} />);

    expect(await screen.findByText(/Watchlist scan timed out/)).toBeInTheDocument();
  });
});
