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
    expect(screen.getByText('📊 Options Snapshot')).toBeInTheDocument();
  });

  it('fetches a chain when selecting an expiration outside the initial response', async () => {
    mockApi.getOptions.mockImplementation(async (symbol: string, expiration?: string) => {
      if (expiration) return response(symbol, [expiration]);
      return {
        ...response(symbol, ['2026-10-16', '2026-10-23']),
        chains: [chain(symbol, '2026-10-16')],
      };
    });

    render(<OptionsPanel symbol="AAPL" />);

    const expiration = await screen.findByLabelText('Expiration:');
    fireEvent.change(expiration, { target: { value: '2026-10-23' } });

    await waitFor(() => {
      expect(mockApi.getOptions).toHaveBeenLastCalledWith('AAPL', '2026-10-23');
      expect(screen.getByLabelText('Expiration:')).toHaveValue('2026-10-23');
    });
    expect(screen.getByText('📊 Options Snapshot')).toBeInTheDocument();
  });

  it('shows an IV-based expected move and flow flags', async () => {
    mockApi.getOptions.mockResolvedValue({
      ...response('AAPL', ['2026-10-16']),
      near_term_iv: 0.3,
      chains: [{
        ...chain('AAPL', '2026-10-16'),
        put_call_ratio: 1.8,
        avg_iv_call: 0.3,
        avg_iv_put: 0.32,
        unusual_activity: 'high',
        calls: [{ strike: 100, expiration: '2026-10-16', option_type: 'call', bid: 1, ask: 1.2, last: 1.1, volume: 10, open_interest: 100, implied_volatility: 0.3, delta: null, gamma: null, theta: null, vega: null, rho: null, in_the_money: true }],
        puts: [{ strike: 100, expiration: '2026-10-16', option_type: 'put', bid: 2, ask: 2.2, last: 2.1, volume: 20, open_interest: 200, implied_volatility: 0.32, delta: null, gamma: null, theta: null, vega: null, rho: null, in_the_money: false }],
      }],
    });

    render(<OptionsPanel symbol="AAPL" underlyingPrice={100} />);

    expect(await screen.findByText(/±\$/)).toBeInTheDocument();
    expect(screen.getByText('high provider activity')).toBeInTheDocument();
    expect(screen.getByText('Put volume elevated')).toBeInTheDocument();
    expect(screen.getByText('Put open interest elevated')).toBeInTheDocument();
  });

  it('orders paired contracts by ascending strike and marks ITM cells', async () => {
    mockApi.getOptions.mockResolvedValue({
      ...response('AAPL', ['2026-10-16']),
      chains: [{
        ...chain('AAPL', '2026-10-16'),
        calls: [
          { strike: 110, expiration: '2026-10-16', option_type: 'call', bid: 1, ask: 1.2, last: 1.1, volume: 10, open_interest: 100, implied_volatility: 0.3, delta: null, gamma: null, theta: null, vega: null, rho: null, in_the_money: false },
          { strike: 100, expiration: '2026-10-16', option_type: 'call', bid: 2, ask: 2.2, last: 2.1, volume: 20, open_interest: 200, implied_volatility: 0.3, delta: null, gamma: null, theta: null, vega: null, rho: null, in_the_money: true },
        ],
        puts: [
          { strike: 110, expiration: '2026-10-16', option_type: 'put', bid: 2, ask: 2.2, last: 2.1, volume: 20, open_interest: 200, implied_volatility: 0.3, delta: null, gamma: null, theta: null, vega: null, rho: null, in_the_money: true },
          { strike: 100, expiration: '2026-10-16', option_type: 'put', bid: 1, ask: 1.2, last: 1.1, volume: 10, open_interest: 100, implied_volatility: 0.3, delta: null, gamma: null, theta: null, vega: null, rho: null, in_the_money: false },
        ],
      }],
    });

    const { container } = render(<OptionsPanel symbol="AAPL" />);

    await screen.findByText('📊 Options Snapshot');
    expect(screen.getAllByTestId('option-strike').map(cell => cell.firstChild?.textContent)).toEqual(['100.00', '110.00']);
    expect(container.querySelectorAll('.options-contract-itm')).toHaveLength(8);
    expect(container.querySelectorAll('.options-contract-otm')).toHaveLength(8);
  });

  it('shows matching strike counts above and below the underlying price', async () => {
    const contract = (strike: number, optionType: 'call' | 'put', inTheMoney: boolean) => ({
      strike,
      expiration: '2026-10-16',
      option_type: optionType,
      bid: 1,
      ask: 1.2,
      last: 1.1,
      volume: 10,
      open_interest: 100,
      implied_volatility: 0.3,
      delta: null,
      gamma: null,
      theta: null,
      vega: null,
      rho: null,
      in_the_money: inTheMoney,
    });
    const optionStrikes = [70, 80, 90, 95, 105, 110, 120, 130, 140];
    mockApi.getOptions.mockResolvedValue({
      ...response('AAPL', ['2026-10-16']),
      chains: [{
        ...chain('AAPL', '2026-10-16'),
        calls: optionStrikes.map(strike => contract(strike, 'call', strike < 100)),
        puts: optionStrikes.map(strike => contract(strike, 'put', strike > 100)),
      }],
    });

    render(<OptionsPanel symbol="AAPL" underlyingPrice={100} />);

    await screen.findByText('📊 Options Snapshot');
    expect(screen.getAllByTestId('option-strike').map(cell => cell.firstChild?.textContent)).toEqual([
      '70.00', '80.00', '90.00', '95.00', '105.00', '110.00', '120.00', '130.00',
    ]);
  });
});
