import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import api from '../services/api';
import { NaturalLanguageScannerBuilder } from './NaturalLanguageScannerBuilder';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: { previewScannerFilters: jest.fn() },
}));

const mockApi = api as unknown as { previewScannerFilters: jest.Mock };

describe('NaturalLanguageScannerBuilder', () => {
  beforeEach(() => mockApi.previewScannerFilters.mockReset());

  it('shows a non-executing preview and hands the exact payload to Filter Builder', async () => {
    mockApi.previewScannerFilters.mockResolvedValue({
      query: 'oversold with rising volume',
      filters: [
        { type: 'rsi_oversold', params: { threshold: 30 } },
        { type: 'volume_expansion', params: { min_ratio: 1.5 } },
      ],
      match: 'AND',
      filter_description: 'rsi oversold AND volume expansion',
      filter_schema: {},
      parser_used: 'rules',
      ai_translation_used: false,
      ambiguous: false,
      unresolved: [],
      timestamp: '2026-09-23T09:30:00-04:00',
    });
    const onUsePreview = jest.fn();
    render(<NaturalLanguageScannerBuilder watchlistId={7} onUsePreview={onUsePreview} />);

    fireEvent.change(screen.getByLabelText('Describe the scan you want'), {
      target: { value: 'oversold with rising volume' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Preview filters' }));

    expect(await screen.findByText('Filter preview')).toBeInTheDocument();
    expect(screen.getByText(/Rsi Oversold/)).toBeInTheDocument();
    expect(screen.getByText('No scan runs during preview')).toBeInTheDocument();
    expect(mockApi.previewScannerFilters).toHaveBeenCalledWith({
      query: 'oversold with rising volume', watchlist_id: 7, scope: 'watchlist',
    });

    fireEvent.click(screen.getByRole('button', { name: 'Use editable filters' }));
    expect(onUsePreview).toHaveBeenCalledWith([
      { type: 'rsi_oversold', params: { threshold: 30 } },
      { type: 'volume_expansion', params: { min_ratio: 1.5 } },
    ], 'AND');
  });

  it('surfaces unresolved criteria as a review requirement', async () => {
    mockApi.previewScannerFilters.mockResolvedValue({
      query: 'news catalyst', filters: [], match: 'AND',
      filter_description: 'No executable Scanner filters were recognized.',
      filter_schema: {}, parser_used: 'rules', ai_translation_used: false,
      ambiguous: true,
      unresolved: ['Scanner has an earnings-exclusion catalyst filter, but no verified news-catalyst filter yet.'],
      timestamp: '2026-09-23T09:30:00-04:00',
    });
    render(<NaturalLanguageScannerBuilder watchlistId={7} onUsePreview={jest.fn()} />);

    fireEvent.change(screen.getByLabelText('Describe the scan you want'), { target: { value: 'news catalyst' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview filters' }));

    expect(await screen.findByText('Review before use · rules')).toBeInTheDocument();
    expect(screen.getByText(/no verified news-catalyst filter yet/)).toBeInTheDocument();
  });
});
