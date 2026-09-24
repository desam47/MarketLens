/**
 * AIAnalysisPanel — provider/model attribution.
 *
 * The optional Peers/Model override controls (O10 cross-ticker
 * correlation context, O12 provider-specific model routing) were
 * removed from the panel (2026-09-16) — analyzeSymbol() is now called
 * with no options. This covers what's left: the panel surfaces which
 * model actually answered.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { act } from '@testing-library/react';
import { AIAnalysisPanel } from './AIAnalysisPanel';
import api from '../services/api';

jest.mock('../services/api', () => ({
    __esModule: true,
    default: {
        getAIConfig: jest.fn(),
        analyzeSymbol: jest.fn(),
        setAIEnabled: jest.fn(),
        getAIStatus: jest.fn(),
    },
}));

const mockApi = api as unknown as {
    getAIConfig: jest.Mock;
    analyzeSymbol: jest.Mock;
    setAIEnabled: jest.Mock;
    getAIStatus: jest.Mock;
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

function setConfig(enabled = true) {
    mockApi.getAIConfig.mockResolvedValue({
        enabled,
        provider: 'ollama',
        fallback_providers: [],
        model: 'llama3.2',
        base_url: 'http://localhost:11434/v1',
        api_key_set: false,
        structured_output: true,
        max_tokens: 1000,
        temperature: 0.3,
    });
}

describe('AIAnalysisPanel', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('runs analysis with no options (no Peers/Model controls)', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult());
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        await waitFor(() => {
            expect(mockApi.analyzeSymbol).toHaveBeenCalledWith('AAPL', '1d');
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
        expect(await screen.findByText('Market-data evidence')).toBeInTheDocument();
        expect(screen.getByText('Cached analysis')).toBeInTheDocument();
        expect(screen.getByText(/Price \$201.25/)).toBeInTheDocument();
        expect(screen.getByText(/Market regime:/)).toHaveTextContent('risk on');
        expect(screen.getByText('1d').parentElement).toHaveTextContent('1d · bullish · strong · 82%');
    });
});
