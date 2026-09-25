import React from 'react';
import { render, screen } from '@testing-library/react';
import { TrendCard } from './TrendCard';
import { TrendData } from '../services/api';

function trend(overrides: Partial<TrendData> = {}): TrendData {
  return {
    symbol: 'SPY',
    timeframe: '1m',
    direction: 'uptrend',
    strength: 'moderate',
    confidence: 0.75,
    timestamp: '2026-09-24T10:00:00-04:00',
    data_age_seconds: 20,
    data_status: 'ok',
    provider: 'webull',
    session: 'regular',
    bar_closed: true,
    evidence: {
      freshness_state: 'live',
      age_seconds: 20,
      valid: true,
      invalid_reason: null,
      warmup_bars: 50,
      required_warmup_bars: 50,
      source_timestamp: '2026-09-24T10:00:00-04:00',
      source_as_of: '2026-09-24T10:00:00-04:00',
      data_status: 'ok',
      bar_closed: true,
      provider: 'webull',
      session: 'regular',
    },
    ...overrides,
  };
}

describe('TrendCard evidence contract', () => {
  it('shows server-owned live evidence and its age separately from bar completion', () => {
    render(<TrendCard trend={trend()} />);

    expect(screen.getByLabelText('Evidence: Live, 20s old')).toBeInTheDocument();
    expect(screen.getByText('Bar closed')).toBeInTheDocument();
    expect(screen.getByText(/As of/)).toBeInTheDocument();
  });

  it('does not present a cold engine as an ordinary valid signal', () => {
    render(<TrendCard trend={trend({
      evidence: {
        ...trend().evidence!,
        freshness_state: 'warming',
        valid: false,
        invalid_reason: 'insufficient_warmup',
        warmup_bars: 12,
      },
    })} />);

    expect(screen.getByLabelText('Evidence: Warming, 20s old')).toBeInTheDocument();
    expect(screen.getByText('12 / 50 bars')).toBeInTheDocument();
    expect(screen.getByText('More history needed')).toBeInTheDocument();
  });

  it('preserves an incomplete derived bar as unavailable after market close', () => {
    render(<TrendCard trend={trend({
      timeframe: '1wk',
      data_status: 'incomplete',
      evidence: {
        ...trend().evidence!,
        freshness_state: 'unavailable',
        valid: false,
        invalid_reason: 'data_status_incomplete',
        data_status: 'incomplete',
        source_timestamp: '2026-09-21T00:00:00-04:00',
        source_as_of: '2026-09-21T00:00:00-04:00',
      },
    })} />);

    expect(screen.getByLabelText('Evidence: Unavailable, 20s old')).toBeInTheDocument();
    expect(screen.getAllByText('incomplete')).toHaveLength(2);
  });

  it('shows a closed-market state without relabeling it as stale', () => {
    render(<TrendCard trend={trend({
      evidence: {
        ...trend().evidence!,
        freshness_state: 'closed_session',
        age_seconds: 3009,
      },
    })} />);

    expect(screen.getByLabelText('Evidence: Market closed, 50m old')).toBeInTheDocument();
    expect(screen.queryByText('Source is stale')).not.toBeInTheDocument();
  });

  it('makes an open-market stale source explicit', () => {
    render(<TrendCard trend={trend({
      evidence: {
        ...trend().evidence!,
        freshness_state: 'stale',
        valid: false,
        invalid_reason: 'source_stale',
        age_seconds: 600,
      },
    })} />);

    expect(screen.getByLabelText('Evidence: Stale, 10m old')).toBeInTheDocument();
    expect(screen.getByText('Source is stale')).toBeInTheDocument();
  });
});
