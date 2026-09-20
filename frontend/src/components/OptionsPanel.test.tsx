import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { OptionsPanel } from './OptionsPanel';
import api, { OptionsChain, OptionsResponse } from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: { getOptions: jest.fn() },
}));

const mockApi = api as jest.Mocked<typeof api>;

function chain(symbol: string, expiration: string): OptionsChain {
  return {
    symbol,
    expiration,
    calls: [],
    puts: [],
    put_call_ratio: null,
    total_call_volume: null,
    total_put_volume: null,
    avg_iv_call: null,
    avg_iv_put: null,
    unusual_activity: 'normal',
  };
}

function response(symbol: string, expirations: string[]): OptionsResponse {
  return {
    symbol,
    chains: expirations.map(expiration => chain(symbol, expiration)),
    expirations,
    near_term_iv: null,
    iv_rank: null,
    provider: 'test',
    timestamp: '2026-09-20T12:00:00Z',
  };
}

describe('OptionsPanel', () => {
  it('resets an expiration that is unavailable after a symbol change', async () => {
    mockApi.getOptions.mockImplementation(async (symbol: string) => (
      symbol === 'AAPL'
        ? response('AAPL', ['2026-10-16', '2026-11-20'])
        : response('MSFT', ['2026-12-18'])
    ));

    const { rerender } = render(<OptionsPanel symbol="AAPL" />);
    const expiration = await screen.findByLabelText('Expiration:');
    fireEvent.change(expiration, { target: { value: '2026-11-20' } });
    expect(expiration).toHaveValue('2026-11-20');

    rerender(<OptionsPanel symbol="MSFT" />);

    await waitFor(() => {
      expect(screen.getByLabelText('Expiration:')).toHaveValue('2026-12-18');
    });
    expect(screen.getByText('📊 Options Chain')).toBeInTheDocument();
  });
});
