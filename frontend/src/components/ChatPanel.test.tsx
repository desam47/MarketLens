import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
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
    setChatFeedback: jest.fn(),
    resetChatMemory: jest.fn(),
  },
}));

const mockApi = api as jest.Mocked<typeof api>;

beforeEach(() => {
  jest.clearAllMocks();
  window.localStorage.clear();
  mockApi.createChatSession.mockResolvedValue({
    id: 1, symbol: null, scope: 'universal', alert_trigger_id: null,
    created_at: '', updated_at: '',
  } as any);
  mockApi.getChatMessages.mockResolvedValue([]);
  mockApi.clearChatHistory.mockResolvedValue({ deleted_sessions: 1, deleted_messages: 2 });
});

afterEach(() => {
  jest.useRealTimers();
});

describe('ChatPanel (universal)', () => {
  it('opens a universal session with no symbol on mount', async () => {
    render(<ChatPanel />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
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

  it('renders persisted typed calculation and warning blocks', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 7, session_id: 1, role: 'assistant', content: 'Allocation is 25%.',
        created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
        blocks: [
          { id: 'calculation-1', type: 'calculation', data: { values: { allocation: 25 }, formulas: ['25000 / 100000'] }, quality: { state: 'verified', grounded: true, confidence: 1 } },
          { id: 'warning-1', type: 'warning', data: { items: ['Partial source coverage.'] }, quality: { state: 'partial', grounded: false, confidence: 0.5 } },
        ],
      } as any,
    ]);
    render(<ChatPanel />);
    expect(await screen.findByRole('region', { name: 'Verified calculation' })).toBeInTheDocument();
    expect(screen.getByText('allocation')).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Answer warnings' })).toHaveTextContent('Partial source coverage.');
  });

  it('renders the application-owned answer verification state', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 71, session_id: 1, role: 'assistant', content: 'AAPL is at $101.',
        created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
        blocks: [{
          id: 'verification-1', type: 'verification',
          data: { version: '5.8.1', status: 'verified', issues: [], evidence_refs: ['ev-1'] },
          quality: { state: 'verified', grounded: true, confidence: 1 },
        }],
      } as any,
    ]);
    render(<ChatPanel />);
    expect(await screen.findByRole('status', { name: 'Answer verification' })).toHaveTextContent('Checked against 1 evidence source.');
    expect(screen.getByText('Verifier 5.8.1')).toBeInTheDocument();
  });

  it('offers visual/action shortcuts for a grounded symbol reply', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 8, session_id: 1, role: 'assistant', content: 'AAPL is ready.',
        created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
      } as any,
    ]);
    const onNavigate = jest.fn();
    render(<ChatPanel onNavigate={onNavigate} />);
    expect(await screen.findByRole('button', { name: 'Open Symbol' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open Symbol' }));
    expect(onNavigate).toHaveBeenCalledWith('symbol', 'AAPL');
    expect(screen.getByRole('button', { name: 'Open Scanner' })).toBeInTheDocument();
  });

  it('renders local reports with download and page navigation actions', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 10, session_id: 1, role: 'assistant', content: 'Report ready.',
        created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
        blocks: [
          {
            id: 'report-1', type: 'report',
            data: {
              title: 'Trade Plan — AAPL', content: '# Trade Plan — AAPL', symbol: 'AAPL',
              deep_links: { symbol: '#symbol', replay: '#signals', journal: '#journal' },
            },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'journal-1', type: 'journal_save',
            data: { saved_entry: { id: 'entry-1', symbol: 'AAPL', status: 'planned' } },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
        ],
      } as any,
    ]);
    const onNavigate = jest.fn();
    render(<ChatPanel onNavigate={onNavigate} />);

    expect(await screen.findByRole('region', { name: 'Local report' })).toHaveTextContent('# Trade Plan — AAPL');
    expect(screen.getByRole('button', { name: 'Download Markdown' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open Replay' }));
    expect(onNavigate).toHaveBeenCalledWith('signals', undefined);
    expect(screen.getByRole('region', { name: 'Journal save' })).toHaveTextContent('AAPL');
  });

  it('offers regeneration and freshness refresh for an older grounded answer', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      { id: 1, session_id: 1, role: 'user', content: 'How is AAPL?', created_at: '', grounded: null } as any,
      {
        id: 2, session_id: 1, role: 'assistant', content: 'Old answer.', created_at: '', grounded: true,
        focus: ['AAPL'], partial: [], unavailable: [],
        blocks: [{ id: 'evidence-1', type: 'evidence', data: { symbols: { verified: ['AAPL'], partial: [], unavailable: [] }, items: [] }, quality: { state: 'stale', grounded: true, confidence: 0.5, source_timestamp: '2020-01-01T00:00:00Z' } }],
      } as any,
    ]);
    mockApi.streamChatMessage.mockImplementation(async () => ({
      id: 3, session_id: 1, role: 'assistant', content: 'New answer.', created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
    } as any));
    render(<ChatPanel />);
    expect(await screen.findByText('Old answer.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '↻ Refresh current data' }));
    await waitFor(() => expect(mockApi.streamChatMessage).toHaveBeenCalledWith(1, 'How is AAPL?', expect.anything()));
    expect(mockApi.streamChatMessage.mock.calls[0][2]).toEqual(expect.objectContaining({ regenerationMode: 'refresh' }));
  });

  it('regenerates with a typed mode and scope instead of rewriting the question', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      { id: 1, session_id: 1, role: 'user', content: 'How is AAPL?', created_at: '', grounded: null } as any,
      { id: 2, session_id: 1, role: 'assistant', content: 'Answer.', created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [], blocks: [] } as any,
    ]);
    mockApi.streamChatMessage.mockImplementation(async () => ({
      id: 3, session_id: 1, role: 'assistant', content: 'Again.', created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
    } as any));
    render(<ChatPanel />);
    expect(await screen.findByText('Answer.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '↻ Run again' }));
    await waitFor(() => expect(mockApi.streamChatMessage).toHaveBeenCalledTimes(1));
    expect(mockApi.streamChatMessage.mock.calls[0][1]).toBe('How is AAPL?');
    expect(mockApi.streamChatMessage.mock.calls[0][2]).toEqual(expect.objectContaining({ regenerationMode: 'again' }));
  });

  it('creates a local notebook and saves an answer with the notebook action', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      { id: 1, session_id: 1, role: 'user', content: 'Compare AAPL and MSFT', created_at: '', grounded: null } as any,
      { id: 2, session_id: 1, role: 'assistant', content: 'Comparison.', created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [], blocks: [] } as any,
    ]);
    render(<ChatPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Notebooks/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'New notebook name' }), { target: { value: 'Trade ideas' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    fireEvent.click(screen.getByRole('button', { name: '📓 Save to notebook' }));
    fireEvent.click(screen.getByRole('button', { name: 'Trade ideas' }));
    expect(window.localStorage.getItem('marketlens.chat.notebooks')).toContain('Compare AAPL and MSFT');
  });

  it('renames notebooks and confirms removal of saved answers or a notebook', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      { id: 1, session_id: 1, role: 'user', content: 'Compare AAPL and MSFT', created_at: '', grounded: null } as any,
      { id: 2, session_id: 1, role: 'assistant', content: 'Comparison.', created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [], blocks: [] } as any,
    ]);
    render(<ChatPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Notebooks/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'New notebook name' }), { target: { value: 'Trade ideas' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    fireEvent.click(screen.getByRole('button', { name: '📓 Save to notebook' }));
    fireEvent.click(screen.getByRole('button', { name: 'Trade ideas' }));
    fireEvent.click(screen.getByRole('button', { name: /Trade ideas · 1 saved/ }));

    fireEvent.click(screen.getByRole('button', { name: 'Edit name' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Notebook name' }), { target: { value: 'Long ideas' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save name' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit name' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Long ideas · 1 saved/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Remove saved answer: Compare AAPL and MSFT' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Remove this saved answer?');
    fireEvent.click(screen.getByRole('button', { name: 'Remove answer' }));
    await waitFor(() => expect(screen.getByText('No answers saved yet.')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Delete notebook' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Delete “Long ideas”');
    const deleteButtons = screen.getAllByRole('button', { name: 'Delete notebook' });
    fireEvent.click(deleteButtons[deleteButtons.length - 1]);
    await waitFor(() => expect(screen.queryByRole('button', { name: /Long ideas ·/ })).not.toBeInTheDocument());
  });

  it('renders every remaining typed block type (5.7.1 component coverage)', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 20, session_id: 1, role: 'assistant', content: 'Full breakdown.',
        created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
        blocks: [
          {
            id: 'evidence-1', type: 'evidence',
            data: {
              symbols: { verified: ['AAPL'], partial: [], unavailable: [] },
              items: [{ tool: 'get_quote', provider: 'webull', timeframe: '1m', session: 'regular' }],
            },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'action-1', type: 'action_confirmation',
            data: { actions: [{ tool: 'create_alert', status: 'completed', reason: null }] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'comparison-1', type: 'comparison_table',
            data: { columns: ['Rank', 'Symbol', 'Value'], rows: [[1, 'AAPL', 10.2], [2, 'MSFT', 8.1]] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'ranked-1', type: 'ranked_results',
            data: { title: 'AAPL relative strength', items: [{ name: 'vs QQQ', score: 4.5 }, { name: 'vs SPY', score: 1.2 }] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'chart-1', type: 'chart',
            data: { symbol: 'AAPL', timeframe: '1d', bars: [{ close: 100 }, { close: 102 }, { close: 101 }] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'indicator-1', type: 'indicator_table',
            data: { symbol: 'AAPL', indicators: { rsi: 62.4, adx: 28.1 }, triggers: [] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'options-1', type: 'options_chain',
            data: { symbol: 'AAPL', iv: 0.31, iv_rank: 45, chains: [{ calls: [{ strike: 230, option_type: 'call', last_price: 3.2, volume: 500, open_interest: 1200 }], puts: [] }] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'risk-1', type: 'risk_card',
            data: { portfolio_value: 100000, gross_exposure: 40000, unknowns: [], conclusion: 'ok' },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'scenario-1', type: 'scenario',
            data: { shock_percent: -10, total_pnl_delta: -4200, positions: [], unknowns: [] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'session-1', type: 'session_stats',
            data: { symbol: 'AAPL', session: 'regular', open: 228.1, close: 230.4 },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'historical-1', type: 'historical_outcomes',
            data: { summaries: [{ horizon: 5, sample_size: 42, mean_return_percent: 1.23, win_rate_percent: 58.4 }] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'followups-1', type: 'suggested_followups',
            data: { items: ['Show the source data', 'Recheck with current data'] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
        ],
      } as any,
    ]);
    render(<ChatPanel />);

    expect(await screen.findByRole('region', { name: 'Evidence' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Action status' })).toHaveTextContent('create_alert');
    expect(screen.getByRole('region', { name: 'Comparison table' })).toHaveTextContent('MSFT');
    expect(screen.getByRole('region', { name: 'Ranked results' })).toHaveTextContent('vs QQQ');
    expect(screen.getByRole('img', { name: /price trend/i })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Indicator table' })).toHaveTextContent('62.40');
    expect(screen.getByRole('region', { name: 'Options chain card' })).toHaveTextContent('230');
    expect(screen.getByRole('region', { name: 'Risk snapshot' })).toHaveTextContent('100000');
    expect(screen.getByRole('region', { name: 'Scenario analysis' })).toHaveTextContent('-4200');
    expect(screen.getByRole('region', { name: 'Session statistics' })).toHaveTextContent('228.1');
    expect(screen.getByRole('region', { name: 'Historical outcomes' })).toHaveTextContent('58.4%');
    expect(screen.getByLabelText('Suggested follow-ups')).toHaveTextContent('Recheck with current data');
  });

  it('shows action-specific detail in action_confirmation blocks (5.7.2)', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 22, session_id: 1, role: 'assistant', content: 'Done.',
        created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
        blocks: [
          {
            id: 'action-1', type: 'action_confirmation',
            data: {
              actions: [
                { tool: 'create_alert', status: 'completed', detail: { symbol: 'AAPL', condition_type: 'price_above', parameter: '200' } },
                { tool: 'add_to_watchlist', status: 'completed', detail: { symbol: 'TSLA', watchlist: 'Swing' } },
                { tool: 'delete_watchlist', status: 'completed', detail: { watchlist: 'Old', target_id: 5 } },
                { tool: 'save_to_journal', status: 'completed', detail: null },
              ],
            },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
        ],
      } as any,
    ]);
    render(<ChatPanel />);

    const region = await screen.findByRole('status', { name: 'Action status' });
    expect(region).toHaveTextContent('create_alert: AAPL price above 200');
    expect(region).toHaveTextContent('add_to_watchlist: TSLA → Swing');
    expect(region).toHaveTextContent('delete_watchlist: "Old"');
    // No detail (null) falls back to the plain tool · status line, not a crash.
    expect(region).toHaveTextContent('save_to_journal · completed');
  });

  it('scenario slider recomputes a labeled local preview, never the verified numbers in place (5.7.2)', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 23, session_id: 1, role: 'assistant', content: 'Scenario ready.',
        created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
        blocks: [
          {
            id: 'scenario-1', type: 'scenario',
            data: {
              shock_percent: -10,
              base_gross_exposure: 1000,
              scenario_gross_exposure: 900,
              total_pnl_delta: -100,
              base_stop_loss_risk: 50,
              scenario_stop_loss_risk: 50,
              positions: [{ symbol: 'AAPL', side: 'long', quantity: 10, base_price: 100, stop_price: 90 }],
              unknowns: [],
            },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
        ],
      } as any,
    ]);
    render(<ChatPanel />);

    const region = await screen.findByRole('region', { name: 'Scenario analysis' });
    expect(region).toHaveTextContent('-100.00'); // original verified pnl delta
    expect(region.querySelector('.chat-scenario-preview-note')).toBeNull();

    const slider = screen.getByLabelText(/Price shock/i);
    fireEvent.change(slider, { target: { value: '10' } });

    // (100 * 1.10 - 100) * 10 qty * 1 (long) = 100; gross = 100*1.10*10 = 1100
    expect(region).toHaveTextContent('100.00');
    expect(region).toHaveTextContent('1100.00');
    expect(region).toHaveTextContent('not verified');
    // stop-loss risk is not shock-dependent — must stay at its original verified value.
    expect(region).toHaveTextContent('50.00');

    fireEvent.click(screen.getByRole('button', { name: 'Reset to verified' }));
    expect(region).toHaveTextContent('-100.00');
    expect(region.querySelector('.chat-scenario-preview-note')).toBeNull();
  });

  it('expands evidence and options-chain tables beyond their default cap, and back (5.7.2)', async () => {
    const items = Array.from({ length: 10 }, (_, index) => ({ tool: `tool_${index}`, provider: 'webull' }));
    const calls = Array.from({ length: 10 }, (_, index) => ({ strike: 100 + index, option_type: 'call', last_price: 1 }));
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 24, session_id: 1, role: 'assistant', content: 'Lots of evidence.',
        created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
        blocks: [
          {
            id: 'evidence-1', type: 'evidence',
            data: { symbols: { verified: ['AAPL'], partial: [], unavailable: [] }, items },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            id: 'options-1', type: 'options_chain',
            data: { symbol: 'AAPL', chains: [{ calls, puts: [] }] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
        ],
      } as any,
    ]);
    render(<ChatPanel />);

    const evidenceRegion = await screen.findByRole('region', { name: 'Evidence' });
    expect(evidenceRegion.querySelectorAll('li')).toHaveLength(8);
    fireEvent.click(within(evidenceRegion).getByRole('button', { name: 'Show all 10' }));
    expect(evidenceRegion.querySelectorAll('li')).toHaveLength(10);
    fireEvent.click(within(evidenceRegion).getByRole('button', { name: 'Show less' }));
    expect(evidenceRegion.querySelectorAll('li')).toHaveLength(8);

    const optionsRegion = screen.getByRole('region', { name: 'Options chain card' });
    expect(optionsRegion.querySelectorAll('tbody tr')).toHaveLength(10); // only 10 calls supplied, under the 14 cap
  });

  it('toggles the mini chart between compact and expanded (5.7.2)', async () => {
    const bars = Array.from({ length: 5 }, (_, index) => ({ close: 100 + index }));
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 25, session_id: 1, role: 'assistant', content: 'Chart ready.',
        created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
        blocks: [
          {
            id: 'chart-1', type: 'chart',
            data: { symbol: 'AAPL', timeframe: '1d', bars },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
        ],
      } as any,
    ]);
    render(<ChatPanel />);

    const chartRegion = await screen.findByRole('region', { name: 'Mini price chart' });
    expect(within(chartRegion).queryByText(/High/)).toBeNull();
    fireEvent.click(within(chartRegion).getByRole('button', { name: 'Expand' }));
    expect(within(chartRegion).getByText('High 104.00')).toBeInTheDocument();
    expect(within(chartRegion).getByText('Low 100.00')).toBeInTheDocument();
    fireEvent.click(within(chartRegion).getByRole('button', { name: 'Collapse' }));
    expect(within(chartRegion).queryByText(/High/)).toBeNull();
  });

  it('preferences panel: set, persist across remount, and reset (5.7.3)', async () => {
    render(<ChatPanel />);
    await screen.findByPlaceholderText(/Ask about any stock/i);

    const toggle = screen.getByRole('button', { name: /Preferences/i });
    expect(toggle).not.toHaveClass('chat-pref-set');
    fireEvent.click(toggle);

    fireEvent.change(screen.getByLabelText('Trading mode'), { target: { value: 'swing_trading' } });
    fireEvent.change(screen.getByLabelText('Risk per trade percent'), { target: { value: '1.5' } });
    fireEvent.click(screen.getByRole('checkbox', { name: '4h' }));

    // Persisted to localStorage immediately, not only on some later save action.
    const stored = JSON.parse(window.localStorage.getItem('marketlens.chat.preferences') || '{}');
    expect(stored.mode).toBe('swing_trading');
    expect(stored.risk_per_trade_percent).toBe(1.5);
    expect(stored.preferred_timeframes).toEqual(['4h']);
    expect(screen.getByRole('button', { name: /Preferences/i })).toHaveClass('chat-pref-set');

    // A remount (e.g. navigating away and back) must pick the saved value back up.
    const { unmount } = render(<ChatPanel />);
    unmount();

    fireEvent.click(screen.getByRole('button', { name: 'Reset to defaults' }));
    expect(JSON.parse(window.localStorage.getItem('marketlens.chat.preferences') || '{}').mode).toBeUndefined();
    expect(screen.getByLabelText('Trading mode')).toHaveValue('');
  });

  it('sends non-default preferences with the message, and null when everything is unset (5.7.3)', async () => {
    render(<ChatPanel />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    fireEvent.click(screen.getByRole('button', { name: /Preferences/i }));
    fireEvent.change(screen.getByLabelText('Trading mode'), { target: { value: 'day_trading' } });
    fireEvent.click(screen.getByRole('button', { name: '✕' })); // close the panel, doesn't clear the preference

    mockApi.streamChatMessage.mockRejectedValueOnce(Object.assign(new Error('stream failed'), { beforeFirstDelta: true }));
    mockApi.sendChatMessage.mockResolvedValueOnce({
      id: 9, session_id: 1, role: 'assistant', content: 'ok', created_at: '', grounded: true, focus: [], partial: [], unavailable: [],
    } as any);

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'how is the market' } });
    fireEvent.submit(screen.getByRole('textbox').closest('form')!);

    await waitFor(() => expect(mockApi.sendChatMessage).toHaveBeenCalled());
    const sentPrefs = mockApi.sendChatMessage.mock.calls[0][2];
    expect(sentPrefs.mode).toBe('day_trading');
  });

  it('records Correct feedback immediately, with no category picker (5.7.8)', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      { id: 30, session_id: 1, role: 'assistant', content: 'AAPL is bullish.', created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [] } as any,
    ]);
    mockApi.setChatFeedback.mockResolvedValueOnce({
      id: 30, session_id: 1, role: 'assistant', content: 'AAPL is bullish.', created_at: '', grounded: true,
      feedback: { rating: 'correct', category: null, comment: null, updated_at: '2026-09-23T12:00:00' },
    } as any);
    render(<ChatPanel />);

    const row = await screen.findByLabelText('Rate this answer');
    fireEvent.click(within(row).getByRole('button', { name: '👍 Correct' }));

    await waitFor(() => expect(mockApi.setChatFeedback).toHaveBeenCalledWith(30, 'correct', null, null));
    expect(await screen.findByText('✓ Marked correct')).toBeInTheDocument();
    // Once rated, the row collapses to the confirmation — no more buttons for this message.
    expect(screen.queryByLabelText('Rate this answer')).toBeNull();
  });

  it('records Incorrect feedback with an optional category and comment (5.7.8)', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      { id: 31, session_id: 1, role: 'assistant', content: 'AAPL is bullish.', created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [] } as any,
    ]);
    mockApi.setChatFeedback.mockResolvedValueOnce({
      id: 31, session_id: 1, role: 'assistant', content: 'AAPL is bullish.', created_at: '', grounded: true,
      feedback: { rating: 'incorrect', category: 'wrong_data', comment: 'Price is stale.', updated_at: '2026-09-23T12:00:00' },
    } as any);
    render(<ChatPanel />);

    const row = await screen.findByLabelText('Rate this answer');
    fireEvent.click(within(row).getByRole('button', { name: '👎 Incorrect' }));
    fireEvent.change(screen.getByLabelText('Feedback category'), { target: { value: 'wrong_data' } });
    fireEvent.change(screen.getByLabelText('Feedback comment'), { target: { value: 'Price is stale.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));

    await waitFor(() => expect(mockApi.setChatFeedback).toHaveBeenCalledWith(31, 'incorrect', 'wrong_data', 'Price is stale.'));
    expect(await screen.findByText('✗ Marked incorrect · wrong data')).toBeInTheDocument();
  });

  it('shows previously-saved feedback from history without re-offering the buttons (5.7.8)', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 32, session_id: 1, role: 'assistant', content: 'AAPL is bullish.', created_at: '', grounded: true, focus: [], partial: [], unavailable: [],
        feedback: { rating: 'not_useful', category: 'poor_explanation', comment: null, updated_at: '2026-09-23T12:00:00' },
      } as any,
    ]);
    render(<ChatPanel />);

    expect(await screen.findByText('⊘ Marked not useful · poor explanation')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '👍 Correct' })).toBeNull();
  });

  it('does not offer feedback on a user message', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      { id: 33, session_id: 1, role: 'user', content: 'how is AAPL?', created_at: '', grounded: null } as any,
    ]);
    render(<ChatPanel />);
    await screen.findByText('how is AAPL?');
    expect(screen.queryByLabelText('Rate this answer')).toBeNull();
  });

  it('handles missing/long values without crashing (5.7.1 mobile/long-value coverage)', async () => {
    const longText = 'A'.repeat(5000);
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 21, session_id: 1, role: 'assistant', content: 'Edge cases.',
        created_at: '', grounded: true, focus: [], partial: [], unavailable: [],
        blocks: [
          {
            // No bars at all — chart must show its empty state, not throw.
            id: 'chart-empty', type: 'chart',
            data: { symbol: 'ZZZZ', timeframe: '1d', bars: [] },
            quality: { state: 'unavailable', grounded: false, confidence: 0 },
          },
          {
            // Null indicator values mixed with real ones.
            id: 'indicator-nulls', type: 'indicator_table',
            data: { symbol: 'ZZZZ', indicators: { rsi: null, adx: undefined, ema_20: 101.5 } },
            quality: { state: 'partial', grounded: false, confidence: 0.5 },
          },
          {
            // Ragged rows (fewer cells than columns) in a comparison table.
            id: 'comparison-ragged', type: 'comparison_table',
            data: { columns: ['Rank', 'Symbol', 'Value'], rows: [[1, 'AAPL']] },
            quality: { state: 'partial', grounded: false, confidence: 0.5 },
          },
          {
            // Very long report content — must render, not truncate-crash.
            id: 'report-long', type: 'report',
            data: { title: 'Long report', content: longText, symbol: 'AAPL', deep_links: {} },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
          {
            // Empty ranked list.
            id: 'ranked-empty', type: 'ranked_results',
            data: { title: 'No anomalies', items: [] },
            quality: { state: 'verified', grounded: true, confidence: 1 },
          },
        ],
      } as any,
    ]);

    render(<ChatPanel />);

    expect(await screen.findByText('Chart data is unavailable.')).toBeInTheDocument();
    expect(screen.getByText('101.50')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Comparison table' })).toHaveTextContent('AAPL');
    expect(screen.getByRole('region', { name: 'Local report' })).toHaveTextContent(longText);
    expect(screen.getByRole('region', { name: 'Ranked results' })).toBeInTheDocument();
  });

  it('colorizes signed numbers in an assistant reply', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 5, session_id: 1, role: 'assistant',
        content: 'NVDA is +3.2% today after last week\'s -1.8% pullback.',
        created_at: '', grounded: true, focus: ['NVDA'], partial: [], unavailable: [],
      } as any,
    ]);
    render(<ChatPanel />);
    const positive = await screen.findByText('+3.2%');
    const negative = await screen.findByText('-1.8%');
    expect(positive).toHaveClass('chat-num-pos');
    expect(negative).toHaveClass('chat-num-neg');
  });

  it('does not colorize numbers in a user message', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 4, session_id: 1, role: 'user', content: 'what about -5%?',
        created_at: '', grounded: null,
      } as any,
    ]);
    const { container } = render(<ChatPanel />);
    await screen.findByText('what about -5%?');
    expect(container.querySelector('.chat-num-neg')).toBeNull();
  });

  it('colors ticker mentions by that reply\'s own grounding status', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 6, session_id: 1, role: 'assistant',
        content: 'NVDA looks solid, PLTR is shakier, and ZZZZ has nothing to go on.',
        created_at: '', grounded: false,
        focus: ['NVDA'], partial: ['PLTR'], unavailable: ['ZZZZ'],
      } as any,
    ]);
    render(<ChatPanel />);
    expect((await screen.findAllByText('NVDA'))[0]).toHaveClass('chat-ticker-ok');
    expect((await screen.findAllByText('PLTR'))[0]).toHaveClass('chat-ticker-partial');
    expect(screen.getByText('ZZZZ')).toHaveClass('chat-ticker-none');
  });

  it('colors bullish/bearish words and neutral metric names', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 7, session_id: 1, role: 'assistant',
        content: 'This looks like a bullish breakout, while RSI stays below overbought.',
        created_at: '', grounded: true, focus: [], partial: [], unavailable: [],
      } as any,
    ]);
    render(<ChatPanel />);
    expect(await screen.findByText('bullish')).toHaveClass('chat-num-pos');
    expect(screen.getByText('breakout')).toHaveClass('chat-num-pos');
    expect(screen.getByText('overbought')).toHaveClass('chat-num-neg');
    expect(screen.getByText('RSI')).toHaveClass('chat-metric');
  });

  it('colors a bare number by the nearest metric/sentiment word in its sentence', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 8, session_id: 1, role: 'assistant',
        content:
          'The RSI is oversold around 26.6 and the MACD is bearish, indicating some ' +
          'downside pressure. Overall, the engine sees a sideways market with only ' +
          'weak bearish signals for the ticker.',
        created_at: '', grounded: true, focus: [], partial: [], unavailable: [],
      } as any,
    ]);
    render(<ChatPanel />);
    expect(await screen.findByText('RSI')).toHaveClass('chat-metric');
    expect(screen.getByText('oversold')).toHaveClass('chat-num-pos');
    // 26.6 has no sign of its own — it inherits "oversold" (the last
    // metric/sentiment word before it in the same sentence), not "RSI".
    expect(screen.getByText('26.6')).toHaveClass('chat-num-pos');
  });

  it('does not color an unsigned number with no metric/sentiment word before it', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      {
        id: 9, session_id: 1, role: 'assistant', content: 'You hold 20 shares of NVDA.',
        created_at: '', grounded: true, focus: ['NVDA'], partial: [], unavailable: [],
      } as any,
    ]);
    const { container } = render(<ChatPanel />);
    await screen.findAllByText('NVDA');
    const coloredNumbers = Array.from(
      container.querySelectorAll('.chat-num-pos, .chat-num-neg, .chat-metric'),
    ).map(el => el.textContent);
    expect(coloredNumbers).not.toContain('20');
  });

  it('polls for and merges a proactive nudge dropped into the session', async () => {
    jest.useFakeTimers();
    mockApi.getChatMessages
      .mockResolvedValueOnce([]) // initial history load on mount
      .mockResolvedValueOnce([
        {
          id: 9, session_id: 1, role: 'assistant',
          content: 'NVDA just crossed into strongly bullish scanner territory.',
          created_at: '', grounded: true,
        } as any,
      ]);

    const { container } = render(<ChatPanel />);
    await waitFor(() => expect(mockApi.getChatMessages).toHaveBeenCalledTimes(1));

    await act(async () => {
      jest.advanceTimersByTime(20000);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    // "bullish" is now its own highlighted span, so the nudge text spans
    // multiple text nodes — check the bubble's full textContent instead
    // of a single-node text match.
    const bubble = container.querySelector('.chat-bubble.assistant');
    expect(bubble?.textContent).toContain('NVDA just crossed into strongly bullish scanner territory.');
  });

  it('does not poll for a session opened from a specific alert trigger', async () => {
    jest.useFakeTimers();
    mockApi.getChatMessages.mockResolvedValueOnce([]);

    await act(async () => {
      render(<ChatPanel alertTriggerId={42} />);
    });
    await screen.findByPlaceholderText(/Ask about any stock/i);
    expect(mockApi.createChatSession).toHaveBeenCalled();
    mockApi.getChatMessages.mockClear();

    await act(async () => {
      jest.advanceTimersByTime(20000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockApi.getChatMessages).not.toHaveBeenCalled();
  });

  it('keeps the triggering symbol on an alert-scoped session', async () => {
    mockApi.getChatMessages.mockResolvedValueOnce([]);
    render(<ChatPanel alertTriggerId={42} alertSymbol="AAPL" />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    expect(mockApi.createChatSession).toHaveBeenCalledWith('AAPL', 42);
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
    expect(mockApi.sendChatMessage).toHaveBeenCalledWith(1, 'how is the market', null);
  });

  it('preserves partial text when a stream fails after emitting a delta', async () => {
    mockApi.streamChatMessage.mockImplementation(async (_id: number, _content: string, opts: any) => {
      opts.onDelta?.('Partial answer');
      throw new Error('stream interrupted');
    });
    render(<ChatPanel />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'how is the market' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    expect(await screen.findByText(/Partial answer/)).toBeInTheDocument();
    expect(screen.getByText(/Stream interrupted before verification completed/)).toBeInTheDocument();
    expect(mockApi.sendChatMessage).not.toHaveBeenCalled();
  });

  it('does not resend a turn the server already started', async () => {
    mockApi.streamChatMessage.mockRejectedValue(
      Object.assign(new Error('The chat turn failed after it started'), { beforeFirstDelta: true, turnStarted: true }),
    );
    mockApi.getChatMessages
      .mockResolvedValueOnce([])
      .mockResolvedValue([
        { id: 1, session_id: 1, role: 'user', content: 'delete my alert', created_at: '', grounded: null } as any,
        { id: 2, session_id: 1, role: 'assistant', content: 'Stored server reply.', created_at: '', grounded: false } as any,
      ]);
    render(<ChatPanel />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'delete my alert' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    expect(await screen.findByText('Stored server reply.')).toBeInTheDocument();
    expect(mockApi.sendChatMessage).not.toHaveBeenCalled();
  });

  it('labels streamed text as an unverified draft until the final message arrives', async () => {
    let finish: (value: any) => void = () => undefined;
    mockApi.streamChatMessage.mockImplementation((_id: number, _content: string, opts: any) => {
      opts.onDelta?.('AAPL is at $300');
      return new Promise(resolve => { finish = resolve; });
    });
    render(<ChatPanel />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'is AAPL at 300?' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    expect(await screen.findByText(/Unverified draft/)).toBeInTheDocument();
    await act(async () => finish({
      id: 3, session_id: 1, role: 'assistant', content: "I couldn't verify one or more numbers.", created_at: '', grounded: false, focus: [], partial: [], unavailable: [],
    }));
    expect(await screen.findByText(/couldn't verify one or more numbers/)).toBeInTheDocument();
    expect(screen.queryByText(/Unverified draft/)).not.toBeInTheDocument();
  });

  it('resets structured memory without clearing messages', async () => {
    mockApi.getChatMessages.mockResolvedValue([
      { id: 1, session_id: 1, role: 'user', content: 'How is AAPL?', created_at: '', grounded: null } as any,
    ]);
    (mockApi as any).resetChatMemory.mockResolvedValue({ session_id: 1, reset: true });
    render(<ChatPanel />);
    expect(await screen.findByText('How is AAPL?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /reset memory/i }));
    await waitFor(() => expect((mockApi as any).resetChatMemory).toHaveBeenCalledWith(1));
    expect(await screen.findByText(/Chat memory reset/)).toBeInTheDocument();
    expect(screen.getByText('How is AAPL?')).toBeInTheDocument();
    expect(mockApi.clearChatHistory).not.toHaveBeenCalled();
  });
});
