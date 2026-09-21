import { earningsDaysAway } from './EarningsBadge';

describe('earningsDaysAway', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-09-21T09:00:00'));
  });

  afterEach(() => jest.useRealTimers());

  it('uses the soonest earnings event and ignores other corporate events', () => {
    expect(earningsDaysAway([
      { symbol: 'AAPL', event_type: 'dividend', date: '2026-09-22', source: 'test' },
      { symbol: 'AAPL', event_type: 'earnings', date: '2026-09-25', source: 'test' },
      { symbol: 'AAPL', event_type: 'earnings', date: '2026-09-22', source: 'test' },
    ])).toBe(1);
  });

  it('returns null when an earnings estimate is unavailable', () => {
    expect(earningsDaysAway([{ symbol: 'AAPL', event_type: 'dividend', date: '2026-09-22', source: 'test' }])).toBeNull();
  });
});
