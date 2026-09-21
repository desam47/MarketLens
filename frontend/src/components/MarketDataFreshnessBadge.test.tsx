import React from 'react';
import { render, screen } from '@testing-library/react';
import { StartupModeContext } from '../contexts/StartupModeContext';
import { MarketDataFreshnessBadge } from './MarketDataFreshnessBadge';

describe('MarketDataFreshnessBadge', () => {
  it.each([
    ['LIVE', 'Live'],
    ['DELAYED', 'Delayed'],
    ['HISTORICAL', 'Cached'],
    ['STALE', 'Stale'],
    ['ERROR', 'Unavailable'],
  ])('labels %s market data as %s', (dataStatus, label) => {
    render(<MarketDataFreshnessBadge dataStatus={dataStatus} />);

    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('does not present persisted data as live when API mode is active', () => {
    render(
      <StartupModeContext.Provider value="api">
        <MarketDataFreshnessBadge dataStatus="LIVE" />
      </StartupModeContext.Provider>,
    );

    expect(screen.getByText('Paused · Cached')).toBeInTheDocument();
  });

  it('uses scanner freshness when a quote has no data status', () => {
    render(<MarketDataFreshnessBadge freshness="recent" ageSeconds={120} showAge />);

    expect(screen.getByText('Delayed · 2m ago')).toBeInTheDocument();
  });
});
