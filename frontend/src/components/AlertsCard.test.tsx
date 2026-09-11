import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AlertsCard } from './AlertsCard';
import api from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: {
    getAlerts: jest.fn(),
    getActiveAlertTriggers: jest.fn(),
    createAlert: jest.fn(),
    deleteAlert: jest.fn(),
    updateAlert: jest.fn(),
    clearAlertTriggers: jest.fn(),
  },
}));

const mockApi = api as jest.Mocked<typeof api>;

const trigger = (over: Partial<any> = {}) => ({
  id: 1, alert_id: 1, symbol: 'AAPL', observed_value: '150.0',
  message: 'AAPL: price below 200.0 (now 150.0)',
  triggered_at: '2026-09-10T02:01:23', ai_commentary: null,
  ...over,
});

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.getAlerts.mockResolvedValue([]);
  mockApi.getActiveAlertTriggers.mockResolvedValue([]);
});

describe('AlertsCard trigger history', () => {
  it('shows no Clear button when there is no trigger history', async () => {
    render(<AlertsCard />);
    await screen.findByText('No alerts configured.');
    expect(screen.queryByTitle(/clear this history/i)).toBeNull();
  });

  it('renders orphaned triggers (no matching alert) with just the message', async () => {
    // The exact shape found live 2026-09-11: 3 triggers whose parent
    // alert (id=1) had already been deleted.
    mockApi.getActiveAlertTriggers.mockResolvedValue([trigger()]);
    render(<AlertsCard />);
    expect(await screen.findByText('AAPL: price below 200.0 (now 150.0)')).toBeInTheDocument();
    expect(screen.getByText('1 trigger fired in the last 24h.')).toBeInTheDocument();
  });

  it('Clear wipes the history and refreshes the (now empty) list', async () => {
    mockApi.getActiveAlertTriggers
      .mockResolvedValueOnce([trigger(), trigger({ id: 2 }), trigger({ id: 3 })])
      .mockResolvedValueOnce([]); // after clearing
    mockApi.clearAlertTriggers.mockResolvedValue({ deleted: 3 });
    window.confirm = jest.fn(() => true);

    render(<AlertsCard />);
    await screen.findByText('3 triggers fired in the last 24h.');

    fireEvent.click(screen.getByTitle(/clear this history/i));

    await waitFor(() => expect(mockApi.clearAlertTriggers).toHaveBeenCalled());
    expect(await screen.findByText('Cleared 3 triggers.')).toBeInTheDocument();
    expect(mockApi.getActiveAlertTriggers).toHaveBeenCalledTimes(2); // initial + post-clear refresh
  });

  it('does nothing if the confirm dialog is dismissed', async () => {
    mockApi.getActiveAlertTriggers.mockResolvedValue([trigger()]);
    window.confirm = jest.fn(() => false);
    render(<AlertsCard />);
    await screen.findByText('1 trigger fired in the last 24h.');

    fireEvent.click(screen.getByTitle(/clear this history/i));

    expect(mockApi.clearAlertTriggers).not.toHaveBeenCalled();
  });
});
