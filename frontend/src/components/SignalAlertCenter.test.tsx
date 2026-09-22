import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { SignalAlertCenter } from './SignalAlertCenter';

const profile = JSON.stringify({
  direction: 'bullish',
  min_score: 70,
  min_strength: 0.7,
  market_regime: 'risk_on',
  timeframe: '1d',
  cooldown_minutes: 60,
});

describe('SignalAlertCenter', () => {
  afterEach(() => {
    jest.restoreAllMocks();
    window.localStorage.clear();
  });

  it('creates a guided signal profile and renders active triggers', async () => {
    const alert = {
      id: 4,
      name: 'SPY setup',
      symbol: 'SPY',
      condition_type: 'signal_profile',
      parameter: profile,
      is_enabled: true,
      created_at: '2026-09-01T10:00:00-04:00',
      updated_at: '2026-09-01T10:00:00-04:00',
    };
    jest.spyOn(api, 'getAlerts').mockResolvedValue([alert]);
    jest.spyOn(api, 'getActiveAlertTriggers').mockResolvedValue([{
      id: 7,
      alert_id: 4,
      symbol: 'SPY',
      observed_value: 'score=80',
      message: 'SPY signal profile matched',
      triggered_at: '2026-09-02T10:00:00-04:00',
      ai_commentary: null,
    }]);
    jest.spyOn(api, 'getAlertDeliveries').mockResolvedValue([]);
    jest.spyOn(api, 'getAlertDeliverySummary').mockResolvedValue({ total: 0, by_status: {}, by_channel: {} });
    const create = jest.spyOn(api, 'createAlert').mockResolvedValue(alert);

    render(<SignalAlertCenter />);

    await waitFor(() => expect(screen.getByText('SPY setup')).toBeInTheDocument());
    expect(screen.getByText('SPY · bullish · score ≥ 70 · strength ≥ 0.7 · 1d · risk_on · any session')).toBeInTheDocument();
    expect(screen.getByText('SPY signal profile matched')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('SPY bullish setup'), { target: { value: 'New setup' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Signal Alert' }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0][0]).toMatchObject({ condition_type: 'signal_profile', symbol: 'SPY' });
  });
});
