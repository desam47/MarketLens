import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ChangeInbox } from './ChangeInbox';
import api from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: { getChangeInbox: jest.fn() },
}));

const mockApi = api as jest.Mocked<typeof api>;

const response = {
  since: '2026-09-23T08:00:00-04:00',
  as_of: '2026-09-23T10:00:00-04:00',
  items: [{
    id: 'alert:alert-trigger:7', category: 'alert' as const, severity: 'warning' as const,
    title: 'Alert fired: Breakout', detail: 'AAPL above 200', symbol: 'AAPL',
    occurred_at: '2026-09-23T09:45:00-04:00', href: '#alerts', dedupe_key: 'alert-trigger:7',
  }],
  counts: { alert: 1 },
  coverage: { alerts: true },
  warnings: ['Catalyst items reflect already-recorded provider activity; this inbox does not poll providers.'],
};

beforeEach(() => {
  jest.clearAllMocks();
  window.localStorage.clear();
  mockApi.getChangeInbox.mockResolvedValue(response);
});

test('loads changes from the last checkpoint and stores the server checkpoint', async () => {
  window.localStorage.setItem('marketlens.ai.changes.last-visit', '2026-09-23T08:00:00-04:00');
  render(<ChangeInbox />);

  expect(await screen.findByText('Alert fired: Breakout')).toBeInTheDocument();
  expect(mockApi.getChangeInbox).toHaveBeenCalledWith('2026-09-23T08:00:00-04:00');
  expect(window.localStorage.getItem('marketlens.ai.changes.last-visit')).toBe(response.as_of);
  expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', '#alerts');
});

test('refresh requests only new activity from the current checkpoint', async () => {
  render(<ChangeInbox />);
  await screen.findByText('Alert fired: Breakout');

  fireEvent.click(screen.getByRole('button', { name: /refresh/i }));
  await waitFor(() => expect(mockApi.getChangeInbox).toHaveBeenCalledTimes(2));
  expect(mockApi.getChangeInbox).toHaveBeenLastCalledWith(response.as_of);
});

test('keeps long change lists in a keyboard-accessible scroll region', async () => {
  const longResponse = {
    ...response,
    items: Array.from({ length: 40 }, (_, index) => ({
      ...response.items[0],
      id: `alert:alert-trigger:${index}`,
      title: `Alert fired: Breakout ${index + 1}`,
    })),
    counts: { alert: 40 },
  };
  mockApi.getChangeInbox.mockResolvedValueOnce(longResponse);

  render(<ChangeInbox />);

  const list = await screen.findByRole('region', { name: 'What changed items' });
  expect(list).toHaveClass('change-inbox-list');
  expect(list).toHaveAttribute('tabindex', '0');
  expect(screen.getByText('Alert fired: Breakout 40')).toBeInTheDocument();
});
