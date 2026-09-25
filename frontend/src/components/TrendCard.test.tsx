import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
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
  it('shows measured short-term momentum instead of default ADX-style strength on 1m', () => {
    render(<TrendCard trend={trend({
      strength: 'moderate',
      short_horizon_momentum: 'persistent',
      short_horizon_momentum_score: 0.92,
    })} />);

    expect(screen.getByText('Momentum')).toBeInTheDocument();
    expect(screen.getByText('PERSISTENT')).toBeInTheDocument();
    expect(screen.queryByText('Strength')).not.toBeInTheDocument();
    expect(screen.getByText(/persistent short-term momentum/)).toBeInTheDocument();
  });

  it('labels agreement with its scoring profile instead of implying universal confidence', () => {
    render(<TrendCard trend={trend({
      scoring: {
        id: 'directional_core',
        label: 'Directional core',
        components: ['EMA', 'RSI', 'MACD'],
        confidence_semantics: 'weighted_indicator_agreement',
        score_comparable_across_timeframes: false,
        calibration_state: 'profile_specific_not_calibrated',
      },
    })} />);

    expect(screen.getByText('Agreement')).toBeInTheDocument();
    expect(screen.queryByText('Confidence')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Scoring profile: Directional core · 3 inputs')).toBeInTheDocument();
    expect(screen.getAllByTitle(/not a probability.*not calibrated/i)).not.toHaveLength(0);
  });

  it('does not present default Moderate as a measurement while momentum warms up', () => {
    render(<TrendCard trend={trend({
      strength: 'moderate',
      short_horizon_momentum: null,
    })} />);

    expect(screen.getByText('Momentum')).toBeInTheDocument();
    expect(screen.getByText('AWAITING BARS')).toBeInTheDocument();
    expect(screen.queryByText('MODERATE')).not.toBeInTheDocument();
  });

  it('renders the measured Very Strong state on ADX-capable timeframes', () => {
    render(<TrendCard trend={trend({
      timeframe: '4h',
      strength: 'very_strong',
      short_horizon_momentum: null,
    })} />);

    expect(screen.getByText('Strength')).toBeInTheDocument();
    expect(screen.getByText('VERY STRONG')).toBeInTheDocument();
  });

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

describe('TrendCard provider provenance (TC-02)', () => {
  it('names a mixed-source aggregate instead of a single misleading provider', () => {
    render(<TrendCard trend={trend({
      evidence: { ...trend().evidence!, provider: 'mixed' },
    })} />);

    expect(screen.getByText('Mixed sources')).toBeInTheDocument();
  });

  it('renders a readable label for a derived aggregate source', () => {
    render(<TrendCard trend={trend({
      evidence: { ...trend().evidence!, provider: 'aggregated_from_1m' },
    })} />);

    expect(screen.getByText('Aggregated from 1m')).toBeInTheDocument();
  });

  it('renders live 1m aggregation provenance', () => {
    render(<TrendCard trend={trend({
      evidence: { ...trend().evidence!, provider: 'live_from_1m' },
    })} />);

    expect(screen.getByText('Live aggregation from 1m')).toBeInTheDocument();
  });

  it('falls back to the trend-level provider when evidence carries none', () => {
    render(<TrendCard trend={trend({
      provider: 'alpaca',
      evidence: { ...trend().evidence!, provider: null },
    })} />);

    expect(screen.getByText('Alpaca')).toBeInTheDocument();
  });

  it('hides provenance entirely rather than showing a bare "unknown"', () => {
    render(<TrendCard trend={trend({
      provider: 'unknown',
      evidence: { ...trend().evidence!, provider: 'unknown' },
    })} />);

    expect(screen.queryByText(/unknown/i)).not.toBeInTheDocument();
  });
});

describe('TrendCard score and classification (TC-07)', () => {
  it('shows an explicit positive-signed score with its classification', () => {
    render(<TrendCard trend={trend({ score: 82, classification: 'strong_bullish' })} />);

    expect(screen.getByText('Score +82 · Strong bullish')).toBeInTheDocument();
  });

  it('shows a negative sign and rounds the score for display', () => {
    render(<TrendCard trend={trend({ score: -33.2, classification: 'bearish' })} />);

    expect(screen.getByText('Score -33 · Bearish')).toBeInTheDocument();
  });

  it('renders an unsigned zero at the neutral boundary', () => {
    render(<TrendCard trend={trend({ score: 0, classification: 'neutral' })} />);

    expect(screen.getByText('Score 0 · Neutral')).toBeInTheDocument();
  });

  it('exposes the score bucket thresholds through an accessible tooltip', () => {
    render(<TrendCard trend={trend({ score: 65, classification: 'bullish' })} />);

    expect(screen.getByText('Score +65 · Bullish')).toHaveAttribute(
      'title',
      expect.stringContaining('-100 to +100'),
    );
  });

  it('shows the classification alone when the score is unavailable', () => {
    render(<TrendCard trend={trend({ score: null, classification: 'no_signal' })} />);

    expect(screen.getByText('No signal')).toBeInTheDocument();
    expect(screen.queryByText(/^Score/)).not.toBeInTheDocument();
  });

  it('renders no score line when neither score nor classification is present', () => {
    render(<TrendCard trend={trend({ score: null, classification: null })} />);

    expect(screen.queryByText(/^Score/)).not.toBeInTheDocument();
    expect(screen.queryByText('No signal')).not.toBeInTheDocument();
  });
});

describe('TrendCard chart handoff (TC-09)', () => {
  it('opens the chart at this timeframe on click', () => {
    const onOpenChart = jest.fn();
    render(<TrendCard trend={trend({ symbol: 'SPY', timeframe: '1m' })} onOpenChart={onOpenChart} />);

    const card = screen.getByRole('button', { name: 'Open SPY 1 Min chart' });
    fireEvent.click(card);

    expect(onOpenChart).toHaveBeenCalledTimes(1);
  });

  it('is keyboard-operable with Enter and Space', () => {
    const onOpenChart = jest.fn();
    render(<TrendCard trend={trend({ symbol: 'AAPL', timeframe: '1h' })} onOpenChart={onOpenChart} />);

    const card = screen.getByRole('button', { name: 'Open AAPL 1 Hour chart' });
    fireEvent.keyDown(card, { key: 'Enter' });
    fireEvent.keyDown(card, { key: ' ' });

    expect(onOpenChart).toHaveBeenCalledTimes(2);
  });

  it('is not interactive when no handoff handler is provided', () => {
    render(<TrendCard trend={trend()} />);

    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('TrendCard change history (TC-09)', () => {
  it('shows how many closed bars an observed run has held', () => {
    render(<TrendCard trend={trend({
      change_history: {
        direction: 'downtrend', bars_in_state: 7, previous_direction: 'uptrend',
        changed_at: '2026-09-24T09:00:00-04:00', witnessed_change: true,
      },
    })} />);

    expect(screen.getByText('Held 7 bars')).toBeInTheDocument();
  });

  it('marks a fresh reversal and names the prior direction', () => {
    render(<TrendCard trend={trend({
      change_history: {
        direction: 'downtrend', bars_in_state: 1, previous_direction: 'uptrend',
        changed_at: '2026-09-24T10:00:00-04:00', witnessed_change: true,
      },
    })} />);

    expect(screen.getByText('New (was uptrend)')).toBeInTheDocument();
  });

  it('presents an unwitnessed run length as a lower bound', () => {
    render(<TrendCard trend={trend({
      change_history: {
        direction: 'uptrend', bars_in_state: 5, previous_direction: null,
        changed_at: null, witnessed_change: false,
      },
    })} />);

    expect(screen.getByText('Held 5+ bars')).toBeInTheDocument();
  });

  it('renders no change-history line before any closed bar', () => {
    render(<TrendCard trend={trend({ change_history: null })} />);

    expect(screen.queryByText(/Held|New/)).not.toBeInTheDocument();
  });
});

describe('TrendCard indicator attribution and key levels (TC-09)', () => {
  it('lists each component contribution with a sign', () => {
    render(<TrendCard trend={trend({
      attribution: [
        { component: 'EMA', signal: 0.9, weight: 1.5, contribution: 40 },
        { component: 'RSI', signal: -0.4, weight: 1, contribution: -12 },
      ],
    })} />);

    expect(screen.getByText('Why this score')).toBeInTheDocument();
    expect(screen.getByText('EMA')).toBeInTheDocument();
    expect(screen.getByText('+40')).toBeInTheDocument();
    expect(screen.getByText('RSI')).toBeInTheDocument();
    expect(screen.getByText('-12')).toBeInTheDocument();
  });

  it('shows SuperTrend flip and Bollinger levels when available', () => {
    render(<TrendCard trend={trend({
      key_levels: {
        supertrend: { flip_price: 231.4, direction: 'up', band_distance_atr: 1.2 },
        bollinger: { upper: 240, middle: 232, lower: 224 },
      },
    })} />);

    expect(screen.getByText(/SuperTrend ↑ 231.4/)).toBeInTheDocument();
    expect(screen.getByText(/Bollinger 224–240/)).toBeInTheDocument();
  });

  it('renders neither section when the timeframe supplies no attribution or levels', () => {
    render(<TrendCard trend={trend({ attribution: [], key_levels: {} })} />);

    expect(screen.queryByText('Why this score')).not.toBeInTheDocument();
    expect(screen.queryByText(/SuperTrend|Bollinger/)).not.toBeInTheDocument();
  });

  it('expanding the attribution panel does not trigger chart handoff', () => {
    const onOpenChart = jest.fn();
    render(<TrendCard
      trend={trend({ attribution: [{ component: 'EMA', signal: 0.9, weight: 1.5, contribution: 40 }] })}
      onOpenChart={onOpenChart}
    />);

    fireEvent.click(screen.getByText('Why this score'));

    expect(onOpenChart).not.toHaveBeenCalled();
  });
});
