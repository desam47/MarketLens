import { render, screen } from '@testing-library/react';
import { NewsPanel } from './NewsPanel';
import api from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: { getNews: jest.fn() },
}));

const mockApi = api as jest.Mocked<typeof api>;

describe('NewsPanel', () => {
  it('opens provider article URLs in a safe new tab', async () => {
    mockApi.getNews.mockResolvedValue({
      symbol: 'AAPL',
      provider: 'finnhub_news',
      timestamp: '2026-09-20T12:00:00Z',
      items: [{
        headline: 'Apple headline',
        source: 'Example',
        timestamp: '2026-09-20T12:00:00Z',
        symbol: 'AAPL',
        relevance: 0.8,
        url: 'https://example.com/apple',
      }],
    });

    render(<NewsPanel symbol="AAPL" />);

    const link = await screen.findByRole('link', { name: 'Apple headline' });
    expect(link).toHaveAttribute('href', 'https://example.com/apple');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });
});
