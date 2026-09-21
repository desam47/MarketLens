import React from 'react';
import { render, screen } from '@testing-library/react';
import { FreshnessIndicator } from './FreshnessIndicator';

describe('FreshnessIndicator', () => {
  it('makes stale market data visible in the dashboard header', () => {
    render(
      <FreshnessIndicator
        regime={
          {
            freshness: 'stale',
            data_age_seconds: 300,
            timestamp: '2026-09-20T14:00:00Z',
          } as any
        }
      />,
    );

    expect(screen.getByText('Stale · 5m ago')).toBeInTheDocument();
  });
});
