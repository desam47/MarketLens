import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { SymbolPage } from './SymbolPage';
import api from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: {
    getQuote: jest.fn(),
    getTransitions: jest.fn(),
    getPriceRange: jest.fn(),
    getDivergences: jest.fn(),
    getAnalysisBars: jest.fn(),
    getScanResult: jest.fn(),
    getMTFSnapshot: jest.fn(),
    getTape: jest.fn(),
  },
}));

jest.mock('../components/CandlestickChart', () => ({ CandlestickChart: () => <div /> }));
jest.mock('../components/MultiTimeframeChartGrid', () => ({ MultiTimeframeChartGrid: () => <div /> }));
jest.mock('../components/ConfluenceCard', () => ({ ConfluenceCard: () => <div /> }));
jest.mock('../components/SignalExplanationPanel', () => ({ SignalExplanationPanel: () => <div /> }));
jest.mock('../components/TapePressureCard', () => ({ TapePressureCard: () => <div /> }));
jest.mock('../components/SymbolInput', () => {
  const React = require('react');
  return {
    SymbolInput: React.forwardRef(() => <input aria-label="Symbol" />),
  };
});
jest.mock('../components/MarketDataFreshnessBadge', () => ({ MarketDataFreshnessBadge: () => <span /> }));
jest.mock('../components/ScoreDetailPanel', () => ({
  ScoreDetailPanel: ({ symbol, totalScore }: { symbol: string; totalScore: number }) => (
    <div data-testid="score-detail">{symbol}:{totalScore}</div>
  ),
}));
jest.mock('../components/AIAnalysisPanel', () => ({ AIAnalysisPanel: () => <div /> }));
jest.mock('../components/CatalystTimelinePanel', () => ({ CatalystTimelinePanel: () => <div /> }));
jest.mock('../components/OptionsPanel', () => ({ OptionsPanel: () => <div /> }));
jest.mock('../components/CustomIndicatorsPanel', () => ({ CustomIndicatorsPanel: () => <div /> }));

const mockApi = api as jest.Mocked<typeof api>;

function scan(symbol: string, totalScore: number) {
  return {
    symbol,
    total_score: totalScore,
    scores: {},
    signals: [],
    timestamp: '2026-09-21T12:00:00Z',
  };
}

describe('SymbolPage', () => {
  beforeEach(() => {
    mockApi.getQuote.mockResolvedValue({ price: 100, timestamp: '2026-09-21T12:00:00Z' } as never);
    mockApi.getTransitions.mockResolvedValue({ transitions: [], latest_score: 0, latest_timestamp: null } as never);
    mockApi.getPriceRange.mockResolvedValue({ levels: [], price_history: [], latest_close: null } as never);
    mockApi.getDivergences.mockResolvedValue({ divergences: [] } as never);
    mockApi.getAnalysisBars.mockResolvedValue({ bars: [] } as never);
    mockApi.getMTFSnapshot.mockResolvedValue({ snapshot: null } as never);
    mockApi.getTape.mockResolvedValue({ snapshot: null } as never);
  });

  it('clears prior-symbol score data while the new scan is loading', async () => {
    let resolveMsftScan: ((value: ReturnType<typeof scan>) => void) | undefined;
    mockApi.getScanResult.mockImplementation((symbol: string) => {
      if (symbol === 'AAPL') return Promise.resolve(scan('AAPL', 42) as never);
      return new Promise(resolve => {
        resolveMsftScan = resolve as (value: ReturnType<typeof scan>) => void;
      }) as never;
    });

    const { rerender } = render(<SymbolPage symbol="AAPL" onSymbolChange={() => undefined} />);
    expect(await screen.findByTestId('score-detail')).toHaveTextContent('AAPL:42');

    rerender(<SymbolPage symbol="MSFT" onSymbolChange={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByTestId('score-detail')).toHaveTextContent('MSFT:0');
    });

    resolveMsftScan?.(scan('MSFT', 25));
    expect(await screen.findByText('MSFT:25')).toBeInTheDocument();
  });
});
