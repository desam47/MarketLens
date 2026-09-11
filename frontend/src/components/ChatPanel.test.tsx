import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatPanel } from './ChatPanel';
import api from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: {
    createChatSession: jest.fn(),
    getChatMessages: jest.fn(),
    sendChatMessage: jest.fn(),
    streamChatMessage: jest.fn(),
    clearChatHistory: jest.fn(),
    getWatchlists: jest.fn(),
    getWatchlistSymbols: jest.fn(),
    createWatchlist: jest.fn(),
    addSymbolToWatchlist: jest.fn(),
    createAlert: jest.fn(),
  },
}));

const mockApi = api as jest.Mocked<typeof api>;

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.createChatSession.mockResolvedValue({
    id: 1, symbol: null, scope: 'universal', alert_trigger_id: null,
    created_at: '', updated_at: '',
  } as any);
  mockApi.getChatMessages.mockResolvedValue([]);
  mockApi.clearChatHistory.mockResolvedValue({ deleted_sessions: 1, deleted_messages: 2 });
});

describe('ChatPanel (universal)', () => {
  it('opens a universal session with no symbol on mount', async () => {
    render(<ChatPanel />);
    await waitFor(() => expect(mockApi.createChatSession).toHaveBeenCalled());
    expect(mockApi.createChatSession).toHaveBeenCalledWith(undefined, null);
  });

  it('shows the example-prompt chips and prefills the input when one is clicked', async () => {
    render(<ChatPanel />);
    const chip = await screen.findByRole('button', { name: "What's the market doing today?" });
    fireEvent.click(chip);
    expect(screen.getByRole('textbox')).toHaveValue("What's the market doing today?");
  });

  it('renders a provenance row with focus / partial / unavailable pills', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 5, session_id: 1, role: 'assistant', content: 'Here you go.',
        created_at: '', grounded: false,
        focus: ['AAPL'], partial: ['RIVN'], unavailable: ['ZZZZ'],
      } as any,
    ]);
    render(<ChatPanel />);
    expect(await screen.findByText('AAPL ✓')).toBeInTheDocument();
    expect(screen.getByText('RIVN ◐ partial')).toBeInTheDocument();
    expect(screen.getByText('ZZZZ ✗ no data')).toBeInTheDocument();
  });

  describe('quick actions', () => {
    beforeEach(() => {
      mockApi.getChatMessages.mockResolvedValue([
        {
          id: 5, session_id: 1, role: 'assistant', content: 'AAPL looks strong.',
          created_at: '', grounded: true,
          focus: ['AAPL'], partial: [], unavailable: [],
        } as any,
      ]);
    });

    it('renders one quick-action row per resolved ticker, skipping index symbols', async () => {
      mockApi.getChatMessages.mockResolvedValue([
        {
          id: 5, session_id: 1, role: 'assistant', content: 'x',
          created_at: '', grounded: true,
          focus: ['AAPL', '^VIX'], partial: [], unavailable: [],
        } as any,
      ]);
      render(<ChatPanel />);
      expect(await screen.findByText('AAPL', { selector: '.chat-quick-action-symbol' }))
        .toBeInTheDocument();
      expect(screen.queryByText('^VIX', { selector: '.chat-quick-action-symbol' })).toBeNull();
    });

    it('adds the ticker to the single existing watchlist on click', async () => {
      mockApi.getWatchlists.mockResolvedValue([{ id: 3, name: 'Watch1' } as any]);
      mockApi.getWatchlistSymbols.mockResolvedValue([]);
      mockApi.addSymbolToWatchlist.mockResolvedValue({} as any);
      render(<ChatPanel />);

      fireEvent.click(await screen.findByTitle('Add AAPL to your watchlist'));

      await waitFor(() => expect(screen.getByText('✓ Watchlisted')).toBeInTheDocument());
      expect(mockApi.addSymbolToWatchlist).toHaveBeenCalledWith(3, 'AAPL');
      expect(mockApi.createWatchlist).not.toHaveBeenCalled();
    });

    it('does not show the add button for a ticker already on a watchlist', async () => {
      mockApi.getWatchlists.mockResolvedValue([{ id: 3, name: 'Watch1' } as any]);
      mockApi.getWatchlistSymbols.mockResolvedValue([{ symbol: 'AAPL' } as any]);
      render(<ChatPanel />);

      expect(await screen.findByText('✓ Watchlisted')).toBeInTheDocument();
      expect(screen.queryByTitle('Add AAPL to your watchlist')).toBeNull();
      expect(mockApi.addSymbolToWatchlist).not.toHaveBeenCalled();
    });

    it('asks which watchlist when more than one exists', async () => {
      mockApi.getWatchlists.mockResolvedValue([
        { id: 3, name: 'Watch1' } as any, { id: 4, name: 'Swing Setups' } as any,
      ]);
      mockApi.getWatchlistSymbols.mockResolvedValue([]);
      mockApi.addSymbolToWatchlist.mockResolvedValue({} as any);
      render(<ChatPanel />);

      fireEvent.click(await screen.findByTitle('Add AAPL to one of your 2 watchlists'));
      // no call yet — a picker should appear instead of guessing
      expect(mockApi.addSymbolToWatchlist).not.toHaveBeenCalled();
      expect(screen.getByLabelText('Which watchlist to add AAPL to')).toBeInTheDocument();

      fireEvent.change(screen.getByLabelText('Which watchlist to add AAPL to'), {
        target: { value: '4' },
      });
      fireEvent.click(screen.getByText('Add'));

      await waitFor(() => expect(screen.getByText('✓ Watchlisted')).toBeInTheDocument());
      expect(mockApi.addSymbolToWatchlist).toHaveBeenCalledWith(4, 'AAPL');
    });

    it('creates a default watchlist when none exists yet', async () => {
      mockApi.getWatchlists.mockResolvedValue([]);
      mockApi.createWatchlist.mockResolvedValue({ id: 9, name: 'Watchlist' } as any);
      mockApi.addSymbolToWatchlist.mockResolvedValue({} as any);
      render(<ChatPanel />);

      fireEvent.click(await screen.findByTitle('Add AAPL to your watchlist'));

      await waitFor(() => expect(mockApi.addSymbolToWatchlist).toHaveBeenCalledWith(9, 'AAPL'));
      expect(mockApi.createWatchlist).toHaveBeenCalledWith('Watchlist');
    });

    it('opens the alert form and creates an alert on submit', async () => {
      mockApi.createAlert.mockResolvedValue({} as any);
      render(<ChatPanel />);

      fireEvent.click(await screen.findByTitle('Set an alert on AAPL'));
      fireEvent.change(screen.getByLabelText('Alert threshold for AAPL'), {
        target: { value: '220' },
      });
      fireEvent.click(screen.getByText('Set'));

      await waitFor(() => expect(screen.getByText('✓ Alert set')).toBeInTheDocument());
      expect(mockApi.createAlert).toHaveBeenCalledWith({
        name: 'AAPL price above', symbol: 'AAPL',
        condition_type: 'price_above', parameter: '220',
      });
    });

    it('offers signal_equals alongside the 3 threshold conditions, with a signal picker for it', async () => {
      mockApi.createAlert.mockResolvedValue({} as any);
      render(<ChatPanel />);

      fireEvent.click(await screen.findByTitle('Set an alert on AAPL'));
      const select = screen.getByLabelText('Alert condition for AAPL') as HTMLSelectElement;
      const optionValues = Array.from(select.options).map(o => o.value);
      expect(optionValues).toEqual([
        'signal_equals', 'price_above', 'price_below', 'pct_change_above',
      ]);

      fireEvent.change(select, { target: { value: 'signal_equals' } });
      // The threshold field for signal_equals is a picker over every
      // signal the scanner can emit, not free text.
      const paramSelect = screen.getByLabelText('Alert threshold for AAPL') as HTMLSelectElement;
      expect(Array.from(paramSelect.options).map(o => o.value)).toEqual([
        '', 'RSI_OVERSOLD', 'RSI_OVERBOUGHT', 'MACD_BULLISH', 'MACD_BEARISH',
        'MULTI_TIMEFRAME_BULLISH', 'MULTI_TIMEFRAME_BEARISH', 'HIGH_VOLUME',
        'HEAVY_BUY_PRESSURE', 'HEAVY_SELL_PRESSURE', 'BLOCK_ACTIVITY',
      ]);

      fireEvent.change(paramSelect, { target: { value: 'RSI_OVERSOLD' } });
      fireEvent.click(screen.getByText('Set'));

      await waitFor(() => expect(screen.getByText('✓ Alert set')).toBeInTheDocument());
      expect(mockApi.createAlert).toHaveBeenCalledWith({
        name: 'AAPL signal equals', symbol: 'AAPL',
        condition_type: 'signal_equals', parameter: 'RSI_OVERSOLD',
      });
    });

    it('clears the threshold value when switching condition away from signal_equals', async () => {
      render(<ChatPanel />);
      fireEvent.click(await screen.findByTitle('Set an alert on AAPL'));

      const select = screen.getByLabelText('Alert condition for AAPL') as HTMLSelectElement;
      fireEvent.change(select, { target: { value: 'signal_equals' } });
      fireEvent.change(screen.getByLabelText('Alert threshold for AAPL'), {
        target: { value: 'HIGH_VOLUME' },
      });

      fireEvent.change(select, { target: { value: 'price_above' } });
      const numberInput = screen.getByLabelText('Alert threshold for AAPL') as HTMLInputElement;
      expect(numberInput.type).toBe('number');
      expect(numberInput.value).toBe('');
    });
  });

  it('streams the assistant reply incrementally then finalizes it', async () => {
    mockApi.streamChatMessage.mockImplementation(async (_id: number, _content: string, opts: any) => {
      opts.onMeta?.({ focus: ['SPY'], partial: [], unavailable: [] });
      opts.onDelta?.('Risk-');
      opts.onDelta?.('on.');
      return {
        id: 9, session_id: 1, role: 'assistant', content: 'Risk-on.',
        created_at: '', grounded: true, focus: ['SPY'], partial: [], unavailable: [],
      } as any;
    });
    render(<ChatPanel />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'how is the market' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText('Risk-on.')).toBeInTheDocument();
    });
    expect(mockApi.streamChatMessage).toHaveBeenCalledWith(
      1, 'how is the market', expect.objectContaining({ onDelta: expect.any(Function) }),
    );
    // provenance row from the finalized message
    expect(screen.getByText('SPY ✓')).toBeInTheDocument();
    expect(mockApi.sendChatMessage).not.toHaveBeenCalled();
  });

  it('reports the resolved ticker via onSymbolResolved', async () => {
    const onSymbolResolved = jest.fn();
    mockApi.streamChatMessage.mockImplementation(async (_i: number, _c: string, opts: any) => {
      opts.onMeta?.({ focus: ['NVDA'], partial: [], unavailable: [] });
      opts.onDelta?.('NVDA is strong.');
      return {
        id: 9, session_id: 1, role: 'assistant', content: 'NVDA is strong.',
        created_at: '', grounded: true, focus: ['NVDA'], partial: [], unavailable: [],
      } as any;
    });
    render(<ChatPanel onSymbolResolved={onSymbolResolved} />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: "how's nvda" } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => expect(onSymbolResolved).toHaveBeenCalledWith('NVDA'));
    expect(onSymbolResolved).toHaveBeenCalledTimes(1); // once per turn
  });

  it('does not call onSymbolResolved for a market-wide turn', async () => {
    const onSymbolResolved = jest.fn();
    mockApi.streamChatMessage.mockImplementation(async (_i: number, _c: string, opts: any) => {
      opts.onMeta?.({ focus: [], partial: [], unavailable: [] });
      opts.onDelta?.('Risk-on.');
      return {
        id: 9, session_id: 1, role: 'assistant', content: 'Risk-on.',
        created_at: '', grounded: true, focus: [], partial: [], unavailable: [],
      } as any;
    });
    render(<ChatPanel onSymbolResolved={onSymbolResolved} />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'how is the market' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => expect(screen.getByText('Risk-on.')).toBeInTheDocument());
    expect(onSymbolResolved).not.toHaveBeenCalled();
  });

  it('Clear flushes history via the API then reopens an empty session', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      { id: 1, session_id: 1, role: 'user', content: 'old q', created_at: '', grounded: null } as any,
      { id: 2, session_id: 1, role: 'assistant', content: 'old a', created_at: '', grounded: true } as any,
    ]);
    mockApi.createChatSession
      .mockResolvedValueOnce({ id: 1, symbol: null, scope: 'universal', alert_trigger_id: null, created_at: '', updated_at: '' } as any)
      .mockResolvedValueOnce({ id: 2, symbol: null, scope: 'universal', alert_trigger_id: null, created_at: '', updated_at: '' } as any);

    render(<ChatPanel />);
    expect(await screen.findByText('old a')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /clear/i }));

    await waitFor(() => expect(mockApi.clearChatHistory).toHaveBeenCalled());
    expect(mockApi.createChatSession).toHaveBeenCalledWith(undefined, null, true);
    await waitFor(() => expect(screen.queryByText('old a')).not.toBeInTheDocument());
  });

  it('falls back to the blocking endpoint when the stream never starts', async () => {
    mockApi.streamChatMessage.mockRejectedValue(
      Object.assign(new Error('502'), { beforeFirstDelta: true }),
    );
    mockApi.sendChatMessage.mockResolvedValue({
      id: 9, session_id: 1, role: 'assistant', content: 'Fallback reply.',
      created_at: '', grounded: true, focus: [], partial: [], unavailable: [],
    } as any);
    render(<ChatPanel />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'how is the market' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    expect(await screen.findByText('Fallback reply.')).toBeInTheDocument();
    expect(mockApi.sendChatMessage).toHaveBeenCalledWith(1, 'how is the market');
  });
});
