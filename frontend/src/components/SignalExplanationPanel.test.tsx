import React from 'react';
import { render, screen } from '@testing-library/react';
import { SignalExplanationPanel } from './SignalExplanationPanel';

const explanation = {
  direction: 'bullish' as const,
  confidence: 78,
  drivers: [{
    key: 'rsi', label: 'RSI', value: 28, direction: 'bullish' as const,
    impact: 'bullish' as const, description: 'RSI is oversold at 28.0.',
  }],
  timeframe_agreement: {
    bullish: 2, bearish: 0, neutral: 0, total: 2, dominant: 'bullish' as const,
    alignment_pct: 100, timeframes: [
      { timeframe: '1h', direction: 'bullish' as const, raw_direction: 'uptrend', confidence: 0.8 },
    ],
  },
  data_freshness: {
    status: 'fresh' as const, age_seconds: 20, quote_timestamp: null,
    scan_timestamp: '2026-09-20T19:00:00', provider: 'test-provider',
  },
  changes: null,
  historical_performance: {
    total: 12, with_outcomes: 10, directional_outcomes: 8, avg_return_5b: 1.25, avg_return_10b: 2.5, win_rate: 0.7,
  },
};

test('renders signal drivers, timeframe agreement, freshness, and history', () => {
  render(<SignalExplanationPanel symbol="AAPL" explanation={explanation} />);

  expect(screen.getByText('Signal Explanation')).toBeInTheDocument();
  expect(screen.getByText('78% confidence')).toBeInTheDocument();
  expect(screen.getByText('RSI is oversold at 28.0.')).toBeInTheDocument();
  expect(screen.getByText('100%')).toBeInTheDocument();
  expect(screen.getByText(/Fresh · 20s ago/)).toBeInTheDocument();
  expect(screen.getByText('70%')).toBeInTheDocument();
  expect(screen.getByText('8 completed bullish/bearish calls from 12 recorded signals.')).toBeInTheDocument();
  expect(screen.getByText('avg signal 5-bar')).toBeInTheDocument();
});
