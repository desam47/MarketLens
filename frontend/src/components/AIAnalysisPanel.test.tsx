/**
 * AIAnalysisPanel — Phase 16 + O10 + O12 tests.
 *
 * Covers the optional peer/model controls added for O10 (cross-ticker
 * correlation context) and O12 (provider-specific model routing).
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

describe('AIAnalysisPanel — O10 + O12 controls', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('renders peer and model inputs empty by default', async () => {
        setConfig(false);
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        expect(screen.getByPlaceholderText(/MSFT,GOOG,SPY/i)).toHaveValue('');
        expect(screen.getByPlaceholderText(/openai:gpt-4o-mini/i)).toHaveValue('');
    });

    it('passes peer symbols and model override to analyzeSymbol', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult());
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        // Auto-run on mount consumes the first analyzeSymbol call; wait for it
        await waitFor(() => {
            expect(mockApi.analyzeSymbol).toHaveBeenCalledTimes(1);
        });
        const peerInput = screen.getByPlaceholderText(/MSFT,GOOG,SPY/i);
        const modelInput = screen.getByPlaceholderText(/openai:gpt-4o-mini/i);
        await act(async () => {
            fireEvent.change(peerInput, { target: { value: 'MSFT,GOOG,SPY' } });
            fireEvent.change(modelInput, { target: { value: 'openai:gpt-4o-mini' } });
        });
        const analyzeBtn = screen.getByRole('button', { name: /Re-run/i });
        await act(async () => {
            fireEvent.click(analyzeBtn);
        });
        await waitFor(() => {
            expect(mockApi.analyzeSymbol).toHaveBeenLastCalledWith(
                'AAPL',
                '1d',
                expect.objectContaining({
                    portfolio_symbols: 'MSFT,GOOG,SPY',
                    model: 'openai:gpt-4o-mini',
                }),
            );
        });
    });

    it('omits portfolio_symbols and model when inputs are empty', async () => {
        setConfig(true);
        mockApi.analyzeSymbol.mockResolvedValue(makeResult());
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        await waitFor(() => {
            expect(mockApi.analyzeSymbol).toHaveBeenCalledTimes(1);
        });
        // First call (auto-run on mount) should have undefined for both
        const firstCall = mockApi.analyzeSymbol.mock.calls[0];
        expect(firstCall[2]).toEqual(
            expect.objectContaining({
                portfolio_symbols: undefined,
                model: undefined,
            }),
        );
    });

    it('disables inputs while loading', async () => {
        setConfig(true);
        // Keep the promise pending so loading stays true
        mockApi.analyzeSymbol.mockReturnValue(new Promise(() => {}));
        await act(async () => {
            render(<AIAnalysisPanel symbol="AAPL" />);
        });
        expect(screen.getByPlaceholderText(/MSFT,GOOG,SPY/i)).toBeDisabled();
        expect(screen.getByPlaceholderText(/openai:gpt-4o-mini/i)).toBeDisabled();
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