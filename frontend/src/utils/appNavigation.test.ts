import { hashForPage, pageForHash } from './appNavigation';

describe('application hash navigation', () => {
  it('uses a unique hash for each top-level page', () => {
    expect(hashForPage('scanner')).toBe('#scanner');
    expect(hashForPage('hub')).toBe('#ai-hub');
    expect(hashForPage('health')).toBe('#system-health');
  });

  it('opens Historical Signals for the replay deep link', () => {
    expect(pageForHash('#historical-replay')).toBe('signals');
  });

  it('falls back safely for a missing or unrecognized hash', () => {
    expect(pageForHash('')).toBe('dashboard');
    expect(pageForHash('#not-a-page')).toBe('dashboard');
  });
});
