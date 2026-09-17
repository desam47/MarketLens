/**
 * @jest-environment node
 *
 * Tests for ApiService's request timeout/cancellation wrapper
 * (`withTimeout` / `normalizeAbortError`). Both are private, so they're
 * exercised indirectly through public methods that go through the
 * shared `fetch` helper.
 *
 * Forced to the `node` environment: this suite exercises nothing DOM-
 * specific, and the jsdom bundled with this project's Jest version
 * (16.7.0, from jest-environment-jsdom@27) predates AbortController's
 * `reason` parameter / `AbortSignal.reason` — `controller.abort(reason)`
 * silently drops the reason there, which this suite's mock fetch relies
 * on to know why a request was aborted. Node's native AbortController
 * supports it correctly; real browsers have too since ~2022, so this is
 * a test-environment gap, not a production one.
 */
import { api } from './api';

function hangingFetchMock(): jest.Mock {
  // Never resolves on its own — only settles if its AbortSignal fires,
  // mirroring a real fetch() against a combined AbortSignal.
  return jest.fn((_url: string, init?: RequestInit) => {
    return new Promise((_resolve, reject) => {
      const signal = init?.signal as AbortSignal | undefined;
      if (!signal) return;
      if (signal.aborted) {
        reject(signal.reason);
        return;
      }
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    });
  });
}

describe('ApiService request timeout/cancellation', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    jest.useRealTimers();
  });

  it('rejects with a clear timeout error when the backend never responds', async () => {
    jest.useFakeTimers();
    global.fetch = hangingFetchMock() as any;

    const promise = api.getRegime('AAPL');
    jest.advanceTimersByTime(15000);

    await expect(promise).rejects.toThrow(/timed out/i);
  });

  it('aborts the underlying fetch once the timeout elapses', async () => {
    jest.useFakeTimers();
    const mock = hangingFetchMock();
    global.fetch = mock as any;

    api.getRegime('AAPL').catch(() => {});
    jest.advanceTimersByTime(15000);
    await Promise.resolve();

    const [, init] = mock.mock.calls[0];
    expect((init.signal as AbortSignal).aborted).toBe(true);
  });

  it('honors a caller-provided AbortSignal independent of the timeout', async () => {
    global.fetch = hangingFetchMock() as any;
    const controller = new AbortController();

    const promise = api.nlSearch({ query: 'x' }, controller.signal);
    controller.abort();

    await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('resolves normally when the backend responds before the timeout', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ symbol: 'AAPL' }),
    }) as any;

    await expect(api.getRegime('AAPL')).resolves.toEqual({ symbol: 'AAPL' });
  });

  it('surfaces a non-ok response as an API error, not a timeout', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
    }) as any;

    await expect(api.getRegime('AAPL')).rejects.toThrow(/500/);
  });
});
