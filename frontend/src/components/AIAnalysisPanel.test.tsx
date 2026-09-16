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
        expect(screen.getAllByText('AAPL')[0]).toHaveClass('chat-ticker-ok');
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
});
