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

const alert = (over: Partial<any> = {}) => ({
  id: 1, name: 'AAPL RSI Oversold', symbol: 'AAPL',
  condition_type: 'signal_equals', parameter: 'RSI_OVERSOLD',
  is_enabled: true, created_at: '2026-09-01T00:00:00', updated_at: '2026-09-01T00:00:00',
  ...over,
});

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

describe('AlertsCard editing an alert', () => {
  it('populates the form and locks the symbol when Edit is clicked', async () => {
    mockApi.getAlerts.mockResolvedValue([alert()]);
    render(<AlertsCard />);
    await screen.findByText('AAPL RSI Oversold');

    fireEvent.click(screen.getByTitle('Edit alert'));

    expect(screen.getByDisplayValue('AAPL RSI Oversold')).toBeInTheDocument();
    expect(screen.getByDisplayValue('RSI_OVERSOLD')).toBeInTheDocument();
    const symbolInput = screen.getByDisplayValue('AAPL') as HTMLInputElement;
    expect(symbolInput).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('submits an update without a symbol in the payload', async () => {
    mockApi.getAlerts.mockResolvedValue([alert()]);
    mockApi.updateAlert.mockResolvedValue(alert({ name: 'Renamed' }) as any);
    render(<AlertsCard />);
    await screen.findByText('AAPL RSI Oversold');

    fireEvent.click(screen.getByTitle('Edit alert'));
    fireEvent.change(screen.getByDisplayValue('AAPL RSI Oversold'), {
      target: { value: 'Renamed' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

    await waitFor(() => expect(mockApi.updateAlert).toHaveBeenCalledWith(1, {
      name: 'Renamed', condition_type: 'signal_equals', parameter: 'RSI_OVERSOLD',
    }));
    expect(await screen.findByText('Alert "Renamed" updated.')).toBeInTheDocument();
    expect(mockApi.createAlert).not.toHaveBeenCalled();
  });

  it('Cancel exits edit mode without saving', async () => {
    mockApi.getAlerts.mockResolvedValue([alert()]);
    render(<AlertsCard />);
    await screen.findByText('AAPL RSI Oversold');

    fireEvent.click(screen.getByTitle('Edit alert'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(mockApi.updateAlert).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Add Alert' })).toBeInTheDocument();
    expect(screen.queryByDisplayValue('AAPL RSI Oversold')).toBeNull();
  });

  it('deleting the alert currently being edited exits edit mode', async () => {
    mockApi.getAlerts
      .mockResolvedValueOnce([alert()])
      .mockResolvedValueOnce([]); // after delete
    mockApi.deleteAlert.mockResolvedValue(undefined);
    window.confirm = jest.fn(() => true);
    render(<AlertsCard />);
    await screen.findByText('AAPL RSI Oversold');

    fireEvent.click(screen.getByTitle('Edit alert'));
    fireEvent.click(screen.getByTitle('Delete alert'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add Alert' })).toBeInTheDocument();
    });
  });
});
