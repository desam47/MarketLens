/**
 * AIAnalysisPanel — provider/model attribution.
 *
 * The optional Peers/Model override controls (O10 cross-ticker
 * correlation context, O12 provider-specific model routing) were
 * removed from the panel (2026-09-16) — analyzeSymbol() is now called
 * with no options. This covers what's left: the panel surfaces which
 * model actually answered.
 */
import React, { createRef } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { act } from '@testing-library/react';
import { AIAnalysisPanel, AIAnalysisPanelHandle, AI_BACKGROUND_TIMEOUT_MS } from './AIAnalysisPanel';
import api from '../services/api';

jest.mock('../services/api', () => ({
    __esModule: true,
    default: {
        getAIConfig: jest.fn(),
        analyzeSymbol: jest.fn(),
        enqueueAIJob: jest.fn(),
        getAIJob: jest.fn(),
        cancelAIJob: jest.fn(),
        setAIEnabled: jest.fn(),
        getAIStatus: jest.fn(),
        trackTradePlan: jest.fn(),
    },
}));

const mockApi = api as unknown as {
    getAIConfig: jest.Mock;
    analyzeSymbol: jest.Mock;
    enqueueAIJob: jest.Mock;
    getAIJob: jest.Mock;
    cancelAIJob: jest.Mock;
    setAIEnabled: jest.Mock;
    getAIStatus: jest.Mock;
    trackTradePlan: jest.Mock;
};

interface AIAnalysisResult {
    summary: string;
    trend: string;
    confidence: number;
    supporting_factors: string[];
    risk_factors: string[];
    timeframe_conflicts: string[];
    key_levels: string[];
    trade_plan?: unknown | null;
    provider: string;
    model: string;
    is_uncertain: boolean;
    template_id?: number | null;
    symbol?: string;
    timeframe?: string;
    price?: number | null;
    source_timestamp?: string | null;
    data_age_seconds?: number | null;
    data_status?: string;
    market_data_provider?: string | null;
    market_session?: 'premarket' | 'regular' | 'after_hours' | 'closed' | 'unknown';
    cache_status?: 'fresh' | 'cached';
    market_regime?: Record<string, unknown>;
    timeframe_scores?: Record<string, { direction?: string; strength?: string; confidence?: number }>;
    track_record?: Record<string, unknown>;
    correlation_context?: Record<string, unknown>;
    trade_plan_validation?: { status?: 'verified' | 'unavailable' | 'not_applicable'; reason?: string };
}

function makeResult(overrides: Partial<AIAnalysisResult> = {}): AIAnalysisResult {
    return {
        summary: 'Test analysis here.',
        trend: 'bullish',
        confidence: 0.7,
        supporting_factors: [],
        risk_factors: [],
        timeframe_conflicts: [],
        key_levels: [],
        trade_plan: null,
        provider: 'ollama',
        model: 'llama3.2',
        is_uncertain: false,
        symbol: 'AAPL',
        timeframe: '1d',
        price: 201.25,
        source_timestamp: '2026-09-24T15:30:00-04:00',
        data_age_seconds: 12,
        data_status: 'LIVE',
        market_data_provider: 'webull',
        market_session: 'regular',
        cache_status: 'fresh',
        ...overrides,
    };
}

function setConfig(enabled = true, tracking = false) {
    mockApi.getAIConfig.mockResolvedValue({
        enabled,
        provider: 'ollama',
        fallback_providers: [],
        model: 'llama3.2',
        base_url: 'http://localhost:11434/v1',
        api_key_set: false,
        trade_plan_tracking_enabled: tracking,
        structured_output: true,
        max_tokens: 1000,
        temperature: 0.3,
    });
}

async function runManualAnalysis() {
    const button = await screen.findByRole('button', { name: /Analyze$/ });
    await act(async () => {
        button.click();
    });
}

describe('AIAnalysisPanel', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    it('runs analysis with no options (no Peers/Model controls)', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult());
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        expect(mockApi.analyzeSymbol).not.toHaveBeenCalled();
        await runManualAnalysis();
        await waitFor(() => {
            expect(mockApi.analyzeSymbol).toHaveBeenCalledWith(
                'AAPL',
                '1d',
                expect.objectContaining({ force_refresh: false, signal: expect.anything() }),
            );
        });
    });

    it('color-codes signed numbers, tickers, and sentiment words in the summary', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult({
            summary: 'AAPL is bullish, up +3.2% today on a breakout above resistance.',
            supporting_factors: ['RSI is oversold around 26.6'],
        }));
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        await runManualAnalysis();
        await waitFor(() => {
            expect(screen.getByText('+3.2%')).toHaveClass('chat-num-pos');
        });
        expect(screen.getByText('bullish')).toHaveClass('chat-num-pos');
        expect(screen.getByText('breakout')).toHaveClass('chat-num-pos');
        expect(screen.getAllByText('AAPL').find(node => node.classList.contains('chat-ticker-ok'))).toBeDefined();
        expect(screen.getByText('RSI')).toHaveClass('chat-metric');
        // 26.6 has no sign of its own — inherits "oversold" (bullish),
        // the last metric/sentiment word before it in the same sentence.
        expect(screen.getByText('26.6')).toHaveClass('chat-num-pos');
    });

    it('shows the model attribution from the response', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult({ model: 'gpt-4o-mini' }));
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        await runManualAnalysis();
        await waitFor(() => {
            expect(screen.getByText(/gpt-4o-mini/i)).toBeInTheDocument();
        });
    });

    it('shows server-authored market-data evidence and cached status', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult({
            cache_status: 'cached',
            market_regime: { regime: 'risk_on' },
            timeframe_scores: { '1d': { direction: 'bullish', strength: 'strong', confidence: 0.82 } },
        }));
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        await runManualAnalysis();
        expect(await screen.findByText('Market-data evidence')).toBeInTheDocument();
        expect(screen.getByText('Cached analysis')).toBeInTheDocument();
        expect(screen.getByText(/Price \$201.25/)).toBeInTheDocument();
        expect(screen.getByText(/Market regime:/)).toHaveTextContent('risk on');
        expect(screen.getByText('1d').parentElement).toHaveTextContent('1d · bullish · strong · 82%');
    });

    it('shows track record and peer context returned by the backend', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult({
            track_record: {
                sample_size: 7,
                win_rate: 0.71,
                all_time_win_count: 6,
                all_time_loss_count: 2,
                all_time_open_count: 1,
                all_time_expired_count: 0,
            },
            correlation_context: {
                peer_count: 2,
                aligned: 1,
                opposed: 1,
                same_sector_count: 1,
                primary_sector: 'Technology',
                peers: [
                    { symbol: 'MSFT', direction: 'bullish', strength: 'strong' },
                    { symbol: 'TSLA', direction: 'bearish', strength: 'weak' },
                ],
            },
        }));
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        await runManualAnalysis();
        expect(await screen.findByText('Track record')).toBeInTheDocument();
        expect(screen.getByText(/7 resolved calls · 71% win rate/)).toBeInTheDocument();
        expect(screen.getByText(/6 wins · 2 losses · 1 open · 0 expired/)).toBeInTheDocument();
        expect(screen.getByText('Peer context')).toBeInTheDocument();
        expect(screen.getByText(/2 peers · 1 aligned · 1 opposed · 1 same sector · Technology/)).toBeInTheDocument();
        const peerItems = screen.getAllByRole('listitem');
        expect(peerItems[0]).toHaveTextContent(/MSFT.*bullish.*strong/);
        expect(peerItems[1]).toHaveTextContent(/TSLA.*bearish.*weak/);
    });

    it('withholds an unvalidated trade setup with its server-authored reason', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult({
            trade_plan_validation: {
                status: 'unavailable',
                reason: 'Market data is stale, so no actionable setup was validated.',
            },
        }));
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        await runManualAnalysis();
        expect(await screen.findByText('No validated trade setup.')).toBeInTheDocument();
        expect(screen.getByText(/Market data is stale/)).toBeInTheDocument();
    });

    it('requires confirmation before tracking a validated setup', async () => {
        setConfig(true, true);
        const plan = {
            recommendation: 'buy',
            conviction: 'high',
            time_horizon: 'swing',
            entry_zone_low: 100,
            entry_zone_high: 102,
            stop_loss: 96,
            targets: [108],
            risk_reward: 1.5,
            thesis: 'Buy the pullback.',
            invalidation: 'Close below support.',
        };
        mockApi.analyzeSymbol.mockResolvedValue(makeResult({
            trade_plan: plan,
            trade_plan_validation: { status: 'verified', quote_price: 101 },
        }));
        mockApi.trackTradePlan.mockResolvedValue({
            tracked: true,
            duplicate: false,
            outcome_id: 42,
            validation: { status: 'verified' },
        });
        jest.spyOn(window, 'confirm').mockReturnValue(true);
        render(<AIAnalysisPanel symbol="AAPL" />);
        await runManualAnalysis();

        const trackButton = await screen.findByRole('button', { name: /track this setup/i });
        await act(async () => {
            trackButton.click();
        });
        expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Track this validated BUY setup'));
        expect(mockApi.trackTradePlan).toHaveBeenCalledWith('AAPL', '1d', plan);
        expect(await screen.findByText('✓ Setup tracked')).toBeInTheDocument();
    });

    it('ignores an older response after the symbol changes', async () => {
        setConfig(true);
        let resolveAAPL!: (result: AIAnalysisResult) => void;
        let resolveMSFT!: (result: AIAnalysisResult) => void;
        mockApi.analyzeSymbol
            .mockImplementationOnce(() => new Promise(resolve => { resolveAAPL = resolve; }))
            .mockImplementationOnce(() => new Promise(resolve => { resolveMSFT = resolve; }));
        const { rerender } = render(<AIAnalysisPanel symbol="AAPL" />);
        await runManualAnalysis();

        rerender(<AIAnalysisPanel symbol="MSFT" />);
        await runManualAnalysis();

        await act(async () => {
            resolveAAPL(makeResult({ summary: 'AAPL stale response.' }));
            resolveMSFT(makeResult({ symbol: 'MSFT', summary: 'MSFT current response.' }));
        });

        expect(document.querySelector('.ai-summary')).toHaveTextContent('MSFT current response.');
        expect(screen.queryByText(/AAPL stale response/)).not.toBeInTheDocument();
    });

    it('refresh button bypasses the cached result', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult());
        render(<AIAnalysisPanel symbol="AAPL" />);
        await runManualAnalysis();
        await waitFor(() => expect(mockApi.analyzeSymbol).toHaveBeenCalledTimes(1));
        await screen.findByText('Market-data evidence');

        await act(async () => {
            screen.getByRole('button', { name: /refresh & rerun/i }).click();
        });
        await waitFor(() => expect(mockApi.analyzeSymbol).toHaveBeenCalledTimes(2));
        expect(mockApi.analyzeSymbol.mock.calls[1][2]).toEqual(
            expect.objectContaining({ force_refresh: true, signal: expect.anything() }),
        );
    });

    it('runs a template job through the React handle and renders its result', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult());
        mockApi.enqueueAIJob.mockResolvedValue({ job_id: 'job-1' });
        mockApi.getAIJob.mockResolvedValue({
            job_id: 'job-1',
            status: 'finished',
            symbol: 'AAPL',
            timeframe: '1d',
            result: makeResult({ summary: 'Background template result.' }),
            error: null,
        });
        const ref = createRef<AIAnalysisPanelHandle>();
        render(<AIAnalysisPanel ref={ref} symbol="AAPL" />);

        await act(async () => {
            await ref.current!.runBackground(7);
        });

        expect(mockApi.enqueueAIJob).toHaveBeenCalledWith(
            { symbol: 'AAPL', timeframe: '1d', template_id: 7 },
            expect.anything(),
        );
        expect(await screen.findByText('Background template result.')).toBeInTheDocument();
        expect((document.getElementById('ai-analysis-panel') as any).runBackground).toBeUndefined();
    });

    it('cancels polling and offers a retry for a queued job', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult());
        mockApi.enqueueAIJob.mockResolvedValue({ job_id: 'job-2' });
        mockApi.getAIJob.mockResolvedValue({
            job_id: 'job-2',
            status: 'queued',
            symbol: 'AAPL',
            timeframe: '1d',
            result: null,
            error: null,
        });
        mockApi.cancelAIJob.mockResolvedValue({ status: 'cancelled', cancelled: true });
        const ref = createRef<AIAnalysisPanelHandle>();
        render(<AIAnalysisPanel ref={ref} symbol="AAPL" />);

        await act(async () => {
            await ref.current!.runBackground(9);
        });
        await waitFor(() => expect(mockApi.getAIJob).toHaveBeenCalledWith('job-2', expect.anything()));
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();

        await act(async () => {
            await ref.current!.cancelBackground();
        });
        expect(mockApi.cancelAIJob).toHaveBeenCalledWith('job-2');
        expect(await screen.findByText(/Background analysis was cancelled/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /retry background analysis/i })).toBeInTheDocument();
    });

    it('times out a job that never reaches a terminal state', async () => {
        jest.useFakeTimers();
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult());
        mockApi.enqueueAIJob.mockResolvedValue({ job_id: 'job-3' });
        mockApi.getAIJob.mockResolvedValue({
            job_id: 'job-3',
            status: 'started',
            symbol: 'AAPL',
            timeframe: '1d',
            result: null,
            error: null,
        });
        const ref = createRef<AIAnalysisPanelHandle>();
        render(<AIAnalysisPanel ref={ref} symbol="AAPL" />);
        await act(async () => {
            await Promise.resolve();
            await Promise.resolve();
        });

        await act(async () => {
            await ref.current!.runBackground();
        });
        await act(async () => {
            await Promise.resolve();
        });
        await act(async () => {
            jest.advanceTimersByTime(AI_BACKGROUND_TIMEOUT_MS);
        });

        expect(screen.getByText(/background analysis timed out/i)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /retry background analysis/i })).toBeInTheDocument();
    });
});
