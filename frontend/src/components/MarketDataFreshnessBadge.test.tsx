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

  it('relabels a delayed reading as Closed and drops the age when the session is closed', () => {
    render(
      <MarketDataFreshnessBadge dataStatus="DELAYED" ageSeconds={3600} showAge marketSession="closed" />,
    );

    // The age is suppressed deliberately: the point of the Closed relabel is to stop an
    // ever-growing "Xh ago" reading alarming the user over an evening or a weekend, when
    // nothing is trading and the price is simply the last one available.
    expect(screen.getByText('Closed')).toBeInTheDocument();
    expect(screen.queryByText(/ago/)).not.toBeInTheDocument();
  });

  it('relabels a freshly-polled live quote as Closed during a closed session', () => {
    render(
      <MarketDataFreshnessBadge dataStatus="LIVE" ageSeconds={5} showAge marketSession="closed" />,
    );

    // This assertion is the reverse of what it used to be. It previously expected
    // "Live · 5s ago" to survive a closed session, on the reading that a fresh quote is
    // genuinely live whatever the clock says. In practice that surfaced "Live · WEBULL · 4s
    // ago" on the Alerts page at times when no equity was trading: the age reflects how
    // recently the backend was polled, not that a trade occurred. Outside every session
    // (premarket/regular/after-hours are all separately reported) a 5-second-old equity
    // quote is a re-sent last value, so Closed is the honest label.
    expect(screen.getByText('Closed')).toBeInTheDocument();
    expect(screen.queryByText(/Live/)).not.toBeInTheDocument();
  });

  it('leaves the delayed label alone when the session is open', () => {
    render(
      <MarketDataFreshnessBadge dataStatus="DELAYED" ageSeconds={3600} showAge marketSession="regular" />,
    );

    expect(screen.getByText('Delayed · 1h ago')).toBeInTheDocument();
  });
});
