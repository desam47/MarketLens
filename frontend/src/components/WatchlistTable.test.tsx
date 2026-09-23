/**
 * WatchlistTable — column toggle, RS benchmark selector, keyboard handling.
 */
import React from 'react';
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WatchlistTable } from './WatchlistTable';
import { rsCellLabel } from './watchlistUtils';
import api, { RelativeStrengthSignal } from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: {
    getWatchlistScan: jest.fn(),
    getWatchlistIntelligence: jest.fn(),
    getWatchlistSessionPrices: jest.fn(),
    getWatchlistCalendar: jest.fn(),
    getBatchRelativeStrength: jest.fn(),
    updateWatchlistSymbol: jest.fn(),
    removeSymbolFromWatchlist: jest.fn(),
    getMarketSession: jest.fn().mockResolvedValue(null),
    createRealtimeSubscriber: jest.fn(),
  },
}));

const mockApi = api as unknown as {
  getWatchlistScan: jest.Mock;
  getWatchlistIntelligence: jest.Mock;
  getWatchlistSessionPrices: jest.Mock;
  getWatchlistCalendar: jest.Mock;
  getBatchRelativeStrength: jest.Mock;
  createRealtimeSubscriber: jest.Mock;
};

function fakeSubscriber() {
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
  return { subscriber, emit: (event: any) => onEvent?.(event) };
}

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
  mockApi.getWatchlistIntelligence.mockResolvedValue({
    watchlist_id: 1,
    generated_at: '2026-01-01T12:00:00-05:00',
    data_status: 'ready',
    watchlist_size: symbols.length,
    analyzed_symbols: symbols.length,
    session_scope: 'all',
    price_basis: 'latest scanner quote and its scanner baseline',
    missing_symbols: [],
    top_bullish: [], top_bearish: [], breakouts: [], deteriorating: [], volume_spikes: [],
    relative_strength: [], mtf_alignment: [], sector_rotation: [], warnings: [],
  });
  mockApi.getWatchlistCalendar.mockResolvedValue({ events: [] });
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
  mockApi.getWatchlistCalendar.mockResolvedValue({ events: [] });
  mockApi.getWatchlistIntelligence.mockResolvedValue({
    watchlist_id: 1,
    generated_at: '2026-01-01T12:00:00-05:00',
    data_status: 'warming',
    watchlist_size: 0,
    analyzed_symbols: 0,
    session_scope: 'all',
    price_basis: 'latest scanner quote and its scanner baseline',
    missing_symbols: [],
    top_bullish: [], top_bearish: [], breakouts: [], deteriorating: [], volume_spikes: [],
    relative_strength: [], mtf_alignment: [], sector_rotation: [], warnings: [],
  });
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

describe('price session filters', () => {
  it('uses local session prices without re-running the scanner', async () => {
    mockApi.getWatchlistSessionPrices.mockResolvedValue({
      sessions: ['premarket', 'regular'],
      count: 1,
      results: [{
        symbol: 'AAPL',
        price: 110,
        change: 10,
        change_pct: 10,
        baseline_price: 100,
        baseline_label: 'previous regular close',
        timestamp: '2026-01-02T12:00:00-05:00',
        trading_date: '2026-01-02',
        session: 'regular',
        volume: 5000,
        high: 112,
        low: 99,
        provider: 'webull',
        data_status: 'historical',
      }],
    });
    await renderTable(['AAPL']);

    await userEvent.click(screen.getByLabelText('After-hours'));

    await waitFor(() => expect(mockApi.getWatchlistSessionPrices).toHaveBeenCalledWith(
      1,
      ['premarket', 'regular'],
    ));
    expect(await screen.findByText('$110')).toBeInTheDocument();
    expect(screen.getByText('+10%')).toBeInTheDocument();
    expect(mockApi.getWatchlistScan).toHaveBeenCalledTimes(1);
  });

  it('shows no prices and skips the API call when every session is unchecked', async () => {
    await renderTable(['AAPL']);
    mockApi.getWatchlistSessionPrices.mockClear();

    await userEvent.click(screen.getByLabelText('Premarket'));
    await userEvent.click(screen.getByLabelText('Regular'));
    mockApi.getWatchlistSessionPrices.mockClear();
    await userEvent.click(screen.getByLabelText('After-hours'));

    expect(await screen.findByText('Select at least one session to show prices.')).toBeInTheDocument();
    expect(mockApi.getWatchlistSessionPrices).not.toHaveBeenCalled();
    expect(screen.queryByText('$100')).not.toBeInTheDocument();
  });

  it('reports symbols with no local session data instead of silently dropping them', async () => {
    mockApi.getWatchlistSessionPrices.mockResolvedValue({
      sessions: ['regular'],
      count: 0,
      results: [],
      missing_symbols: ['AAPL'],
    });
    await renderTable(['AAPL']);

    await userEvent.click(screen.getByLabelText('Premarket'));
    await userEvent.click(screen.getByLabelText('After-hours'));

    expect(await screen.findByText('No local data yet for 1 symbol.')).toBeInTheDocument();
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
    expect(screen.getByRole('columnheader', { name: /Trend/ })).toHaveStyle({ width: '240px' });
  });

  it('renders one cell per visible column in the virtualized list', async () => {
    await renderTable(manySymbols());
    await hideColumn('Type');

    const header = getVirtHeader();
    const row = getVirtRow('SYM00');
    expect(cellCount(header)).toBe(7);
    expect(cellCount(row)).toBe(7);
    // Header and rows must share the same (7-track) grid template.
    expect(header.style.gridTemplateColumns).toBe(row.style.gridTemplateColumns);
    expect(row.style.gridTemplateColumns.split(' ').length).toBeGreaterThanOrEqual(7);
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
    await waitFor(() => expect(screen.getAllByRole('columnheader')).toHaveLength(7));
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

describe('combined view (all sessions selected) live quote staleness', () => {
  function scanResultWithQuoteTimestamp(price: number, change: number, timestamp: string) {
    return {
      symbol: 'AAPL',
      quote: { price, timestamp },
      change,
      change_pct: (change / (price - change)) * 100,
      indicator_values: {}, scores: {}, total_score: 0, rank: null,
      signals: [], trend_signals: {}, timestamp,
      is_enabled: true, entity_type: 'stock',
    };
  }

  it('ignores a live quote older than the last scan and keeps the scanned change %', async () => {
    // priorClose = 102 - 2 = 100, so the scan's own change % is 2%.
    mockApi.getWatchlistScan.mockResolvedValue({
      timestamp: '2026-01-01T20:00:00Z',
      count: 1,
      results: [scanResultWithQuoteTimestamp(102, 2, '2026-01-01T20:00:00Z')],
    });
    mockApi.getBatchRelativeStrength.mockResolvedValue({ results: {} });
    const { subscriber, emit } = fakeSubscriber();
    mockApi.createRealtimeSubscriber.mockReturnValue(subscriber);

    render(<WatchlistTable watchlistId={1} onSelectSymbol={jest.fn()} />);
    expect(await screen.findByText('$102')).toBeInTheDocument();
    expect(screen.getByText('+2%')).toBeInTheDocument();

    // A tick timestamped BEFORE the scan's own quote -- e.g. a premarket
    // trade that's been sitting in the live-quote map for hours with no
    // newer tick since. Must not override the fresher scanned price.
    act(() => {
      emit({
        type: 'quote_update',
        symbol: 'AAPL',
        data: {
          price: 99, volume: 1, bid: null, ask: null, bid_size: null, ask_size: null,
          timestamp: '2026-01-01T09:00:00Z', received_at: Date.now(), provider: 'webull', event_type: 'trade',
        },
      });
    });

    expect(screen.getByText('$102')).toBeInTheDocument();
    expect(screen.getByText('+2%')).toBeInTheDocument();
  });

  it('applies a live quote newer than the last scan', async () => {
    mockApi.getWatchlistScan.mockResolvedValue({
      timestamp: '2026-01-01T20:00:00Z',
      count: 1,
      results: [scanResultWithQuoteTimestamp(102, 2, '2026-01-01T20:00:00Z')],
    });
    mockApi.getBatchRelativeStrength.mockResolvedValue({ results: {} });
    const { subscriber, emit } = fakeSubscriber();
    mockApi.createRealtimeSubscriber.mockReturnValue(subscriber);

    render(<WatchlistTable watchlistId={1} onSelectSymbol={jest.fn()} />);
    expect(await screen.findByText('$102')).toBeInTheDocument();

    // A genuinely newer tick (after-hours, later than the scan) must still
    // update the price and be reflected in change % (vs the same priorClose).
    act(() => {
      emit({
        type: 'quote_update',
        symbol: 'AAPL',
        data: {
          price: 103, volume: 1, bid: null, ask: null, bid_size: null, ask_size: null,
          timestamp: '2026-01-01T21:00:00Z', received_at: Date.now(), provider: 'webull', event_type: 'trade',
        },
      });
    });

    expect(await screen.findByText('$103')).toBeInTheDocument();
    expect(screen.getByText('+3%')).toBeInTheDocument();
  });
});
