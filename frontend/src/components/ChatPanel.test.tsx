import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatPanel } from './ChatPanel';
import api from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: {
    createChatSession: jest.fn(),
    getChatMessages: jest.fn(),
    sendChatMessage: jest.fn(),
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

  it('appends the assistant reply after sending a message', async () => {
    mockApi.sendChatMessage.mockResolvedValue({
      id: 9, session_id: 1, role: 'assistant', content: 'Risk-on.',
      created_at: '', grounded: true, focus: [], partial: [], unavailable: [],
    } as any);
    render(<ChatPanel />);
    await screen.findByPlaceholderText(/Ask about any stock/i);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'how is the market' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));
    expect(await screen.findByText('Risk-on.')).toBeInTheDocument();
    expect(mockApi.sendChatMessage).toHaveBeenCalledWith(1, 'how is the market');
  });
});
