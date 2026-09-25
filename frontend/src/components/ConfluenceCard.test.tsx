import { render, screen } from '@testing-library/react';
import { ConfluenceCard } from './ConfluenceCard';
import { ConfluenceData } from '../services/api';

function confluence(overrides: Partial<ConfluenceData> = {}): ConfluenceData {
  return {
    symbol: 'SPY',
    direction: 'neutral',
    strength: 0.5,
    alignment_score: 0.5,
    timeframe_signals: {},
    timestamp: null,
    ...overrides,
  };
}

describe('ConfluenceCard timing label', () => {
  it('does not call opposing continuation directions aligned', () => {
    render(
      <ConfluenceCard
        confluence={confluence({
          short_term_state: 'continuation',
          short_term_direction: 'uptrend',
          higher_direction: 'downtrend',
        })}
        selectedPreset="day_trading"
        onPresetChange={jest.fn()}
      />,
    );

    expect(screen.getByText('Timing: Countertrend')).toBeInTheDocument();
  });

  it('labels missing directional evidence neutral', () => {
    render(
      <ConfluenceCard
        confluence={confluence()}
        selectedPreset="day_trading"
        onPresetChange={jest.fn()}
      />,
    );

    expect(screen.getByText('Timing: Neutral')).toBeInTheDocument();
  });

  it('renders the shared per-timeframe evidence state instead of implying every input is valid', () => {
    render(
      <ConfluenceCard
        confluence={confluence({
          timeframe_signals: {
            '1d': {
              direction: 'uptrend',
              strength: 'moderate',
              confidence: 0.75,
              timestamp: '2026-09-24T16:00:00-04:00',
              evidence: {
                freshness_state: 'unavailable',
                age_seconds: 300,
                valid: false,
                invalid_reason: 'data_status_incomplete',
                warmup_bars: 50,
                required_warmup_bars: 50,
                source_timestamp: '2026-09-21T00:00:00-04:00',
                source_as_of: '2026-09-21T00:00:00-04:00',
                data_status: 'incomplete',
                bar_closed: true,
                provider: 'aggregated_from_1d',
                session: 'regular',
              },
            },
          },
        })}
        selectedPreset="day_trading"
        onPresetChange={jest.fn()}
      />,
    );

    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(screen.getByTitle(/Evidence: Unavailable.*data status incomplete/i)).toBeInTheDocument();
  });
});
