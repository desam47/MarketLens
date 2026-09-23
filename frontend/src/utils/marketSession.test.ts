import { classifySessionFromTimestamp } from './marketSession';

describe('classifySessionFromTimestamp', () => {
  it('returns null for a missing timestamp', () => {
    expect(classifySessionFromTimestamp(null)).toBeNull();
    expect(classifySessionFromTimestamp(undefined)).toBeNull();
  });

  it.each([
    ['2026-09-22T03:59:00-04:00', 'closed'],
    ['2026-09-22T04:00:00-04:00', 'premarket'],
    ['2026-09-22T09:29:00-04:00', 'premarket'],
    ['2026-09-22T09:30:00-04:00', 'regular'],
    ['2026-09-22T15:59:00-04:00', 'regular'],
    ['2026-09-22T16:00:00-04:00', 'after_hours'],
    ['2026-09-22T19:59:00-04:00', 'after_hours'],
    ['2026-09-22T20:00:00-04:00', 'closed'],
  ])('classifies %s as %s (matches backend USMarketCalendar boundaries)', (timestamp, expected) => {
    expect(classifySessionFromTimestamp(timestamp)).toBe(expected);
  });
});
