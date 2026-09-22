/**
 * Regression test for useMarketStream's subscribe/unsubscribe bookkeeping.
 *
 * A prior version diffed `subscriptions` against a `prevSubsRef` to only
 * send the delta to RealtimeSubscriber, but its cleanup unconditionally
 * unsubscribed everything in the current closure without updating that
 * ref. After any cleanup+re-run with the same subscriptions -- notably
 * React 18 StrictMode's dev-only mount -> cleanup -> mount on initial
 * mount, which this test exercises directly -- the diff believed those
 * keys were still subscribed and never resubscribed them, silently
 * dropping live updates for the rest of the component's life.
 */
import React from 'react';
import { renderHook } from '@testing-library/react';
import { useMarketStream } from './useMarketStream';

// CRA's Jest preset runs with `resetMocks: true`, which wipes every
// jest.fn()'s implementation before each test -- including one set at
// module scope, since that scope evaluates before the first test's
// reset runs. So the mock functions are declared here and given their
// implementations in beforeEach instead of inline at creation.
const mockSubscriber = {
  subscribe: jest.fn(),
  unsubscribe: jest.fn(),
  disconnect: jest.fn(),
  connect: jest.fn(),
  onEvent: jest.fn(),
  onStatus: jest.fn(),
};

jest.mock('../services/api', () => ({
  __esModule: true,
  default: {
    createRealtimeSubscriber: () => mockSubscriber,
  },
}));

describe('useMarketStream', () => {
  beforeEach(() => {
    mockSubscriber.onEvent.mockImplementation(() => () => {});
    mockSubscriber.onStatus.mockImplementation((listener: (s: string) => void) => {
      listener('closed');
      return () => {};
    });
  });

  it('resubscribes after a StrictMode mount -> cleanup -> mount cycle', () => {
    const subscriptions = [{ symbol: 'AAPL', timeframe: '1m' }];

    renderHook(() => useMarketStream({ subscriptions }), {
      wrapper: ({ children }) => <React.StrictMode>{children}</React.StrictMode>,
    });

    // StrictMode runs: effect (subscribe) -> cleanup (unsubscribe) ->
    // effect (subscribe) again, all against the same subscriptions
    // reference. The final state must be "subscribed" -- i.e. the second
    // effect run must have actually resubscribed, not silently no-op'd
    // because stale bookkeeping believed it already was.
    expect(mockSubscriber.subscribe).toHaveBeenCalledWith('AAPL', '1m');
    expect(mockSubscriber.unsubscribe).toHaveBeenCalledWith('AAPL', '1m');
    expect(mockSubscriber.subscribe.mock.calls.length).toBeGreaterThan(
      mockSubscriber.unsubscribe.mock.calls.length
    );
  });

  it('unsubscribes everything on real unmount', () => {
    const subscriptions = [
      { symbol: 'AAPL', timeframe: '1m' },
      { symbol: 'MSFT', timeframe: '5m' },
    ];

    const { unmount } = renderHook(() => useMarketStream({ subscriptions }));
    mockSubscriber.unsubscribe.mockClear();

    unmount();

    expect(mockSubscriber.unsubscribe).toHaveBeenCalledWith('AAPL', '1m');
    expect(mockSubscriber.unsubscribe).toHaveBeenCalledWith('MSFT', '5m');
  });
});
