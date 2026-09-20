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
});
