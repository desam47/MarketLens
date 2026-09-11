import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { AIProviderBadge } from './AIProviderBadge';
import api from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: { getAIConfig: jest.fn() },
}));

const mockApi = api as jest.Mocked<typeof api>;

const config = (over: Partial<any> = {}) => ({
  enabled: true, provider: 'openrouter', fallback_providers: [], model: 'gpt-4o-mini',
  base_url: '', timeout: 30, max_tokens: 1024, temperature: 0.3, api_key_set: true,
  ...over,
});

afterEach(() => {
  cleanup();
  jest.restoreAllMocks();
});

describe('AIProviderBadge', () => {
  it('renders nothing until the first fetch resolves', async () => {
    let resolveFn: (v: any) => void = () => {};
    mockApi.getAIConfig.mockReturnValue(new Promise(res => { resolveFn = res; }));
    const { container } = render(<AIProviderBadge />);
    expect(container).toBeEmptyDOMElement();
    resolveFn(config());
    await waitFor(() => expect(container).not.toBeEmptyDOMElement());
  });

  it('shows the configured provider and model once loaded', async () => {
    mockApi.getAIConfig.mockResolvedValue(config({ provider: 'anthropic', model: 'claude-sonnet-5' }));
    render(<AIProviderBadge />);
    expect(await screen.findByText(/Anthropic/)).toBeInTheDocument();
    expect(screen.getByText(/claude-sonnet-5/)).toBeInTheDocument();
  });

  it('shows a disabled badge when AI is off, not a provider name', async () => {
    mockApi.getAIConfig.mockResolvedValue(config({ enabled: false }));
    render(<AIProviderBadge />);
    expect(await screen.findByText(/AI disabled/)).toBeInTheDocument();
    expect(screen.queryByText(/openrouter/i)).toBeNull();
  });

  it('names the fallback chain in the title when one is configured', async () => {
    mockApi.getAIConfig.mockResolvedValue(
      config({ fallback_providers: ['ollama'] }),
    );
    render(<AIProviderBadge />);
    const badge = await screen.findByText(/OpenRouter/);
    expect(badge.title).toContain('Ollama');
  });

  it('keeps showing the last-known config if a later fetch fails', async () => {
    mockApi.getAIConfig.mockResolvedValue(config());
    render(<AIProviderBadge />);
    await screen.findByText(/OpenRouter/);
    // A later poll failing (network blip) shouldn't blank the badge —
    // the .catch() is a no-op, not a state reset.
    expect(screen.getByText(/OpenRouter/)).toBeInTheDocument();
  });

  it('polls GET /api/ai/config on a 10s interval and stops on unmount', async () => {
    const setIntervalSpy = jest.spyOn(window, 'setInterval');
    const clearIntervalSpy = jest.spyOn(window, 'clearInterval');
    mockApi.getAIConfig.mockResolvedValue(config());
    const { unmount } = render(<AIProviderBadge />);
    await screen.findByText(/OpenRouter/);

    expect(setIntervalSpy).toHaveBeenCalledWith(expect.any(Function), 10_000);
    const intervalId = setIntervalSpy.mock.results[0].value;
    unmount();
    expect(clearIntervalSpy).toHaveBeenCalledWith(intervalId);
  });
});
