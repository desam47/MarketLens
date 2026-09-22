import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { TradeJournalPage } from './TradeJournalPage';

describe('TradeJournalPage', () => {
  beforeEach(() => {
    window.localStorage.clear();
    jest.spyOn(api, 'getLatestSignalsForSymbol').mockResolvedValue({
      '1d': {
        id: 1,
        symbol: 'AAPL',
        timestamp: '2026-09-19T20:00:00Z',
        timeframe: '1d',
        price: 190,
        trend_score: 0.8,
        trend_state: 'bullish',
        strength: 0.7,
        market_regime: 'risk_on',
        relative_strength: null,
        sector_alignment: null,
        volume_state: null,
        momentum: null,
        structure: null,
        confidence_inputs: null,
        strategy_version: null,
        data_quality: 'good',
        return_5b: 2.5,
        return_10b: null,
        return_20b: null,
        mfe: null,
        mae: null,
        created_at: null,
      },
    } as any);
    // Journal enrichment is best-effort; keep this unit test offline and
    // deterministic instead of letting jsdom attempt real localhost calls.
    jest.spyOn(api, 'getScanResult').mockResolvedValue({} as any);
    jest.spyOn(api, 'getTape').mockResolvedValue({ snapshot: null } as any);
  });

  afterEach(() => jest.restoreAllMocks());

  it('records a closed trade, calculates P&L, and attaches the latest signal', async () => {
    render(<TradeJournalPage />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Trade symbol' }), { target: { value: 'aapl' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Trade status' }), { target: { value: 'closed' } });
    fireEvent.change(screen.getByLabelText('Exit date'), { target: { value: '2026-09-20' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Trade quantity' }), { target: { value: '10' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Entry price' }), { target: { value: '180' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Exit price' }), { target: { value: '190' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Trade thesis' }), { target: { value: 'Breakout with strong breadth' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save trade' }));

    await waitFor(() => expect(window.localStorage.getItem('marketlens.trade.journal')).toContain('"symbol":"AAPL"'));
    expect(screen.getAllByText('$100.00', { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByText('Breakout with strong breadth')).toBeInTheDocument();
    expect(screen.getByText(/Signal attached:/)).toBeInTheDocument();
    expect(window.localStorage.getItem('marketlens.trade.journal')).toContain('AAPL');
  });

  it('requires exit details for closed trades', () => {
    render(<TradeJournalPage />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Trade status' }), { target: { value: 'closed' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Trade symbol' }), { target: { value: 'SPY' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Trade quantity' }), { target: { value: '1' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Entry price' }), { target: { value: '500' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save trade' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Closed trades need an exit date and exit price.');
  });
});
