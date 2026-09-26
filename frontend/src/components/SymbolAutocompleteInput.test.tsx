import React, { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { clearSymbolCatalogCache, resolveWatchlistSymbol, SymbolAutocompleteInput } from './SymbolAutocompleteInput';

describe('SymbolAutocompleteInput', () => {
  let catalogSpy: jest.SpyInstance;

  beforeEach(() => {
    clearSymbolCatalogCache();
    catalogSpy = jest.spyOn(api, 'getSymbolCatalog').mockResolvedValue([]);
  });

  afterEach(() => jest.restoreAllMocks());

  it('updates a controlled field through the supplied change handler', () => {
    function Harness() {
      const [value, setValue] = useState('');
      return <SymbolAutocompleteInput value={value} onChange={setValue} aria-label="Symbol" />;
    }
    render(<Harness />);
    const input = screen.getByRole('textbox', { name: 'Symbol' });
    fireEvent.change(input, { target: { value: 'aapl' } });
    expect(input).toHaveValue('AAPL');
  });

  it('offers filtered Watchlist symbols and fills the field when a suggestion is chosen', async () => {
    catalogSpy.mockResolvedValue(['NVDA', 'SPY']);
    const onChange = jest.fn();
    render(<SymbolAutocompleteInput value="" onChange={onChange} aria-label="Symbol" />);

    const input = screen.getByRole('textbox', { name: 'Symbol' });
    fireEvent.focus(input);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'n' } });
    expect(await screen.findByRole('option', { name: 'NVDA' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'NVDA' }));
    expect(onChange).toHaveBeenCalledWith('NVDA');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('loads the catalog on focus without showing a menu until search begins', async () => {
    catalogSpy.mockResolvedValue(['CTNT']);
    render(<SymbolAutocompleteInput value="SPY" onChange={jest.fn()} aria-label="Symbol" />);

    fireEvent.focus(screen.getByRole('textbox', { name: 'Symbol' }));

    await waitFor(() => expect(catalogSpy).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('uses the highlighted suggestion with keyboard navigation', () => {
    catalogSpy.mockResolvedValue(['NVDA']);
    const onChange = jest.fn();
    const view = render(<SymbolAutocompleteInput value="" onChange={onChange} aria-label="Symbol" />);
    const input = screen.getByRole('textbox', { name: 'Symbol' });

    fireEvent.focus(input);
    return waitFor(() => expect(catalogSpy).toHaveBeenCalledTimes(1)).then(() => {
      fireEvent.change(input, { target: { value: 'nv' } });
      view.rerender(<SymbolAutocompleteInput value="NV" onChange={onChange} aria-label="Symbol" />);
      fireEvent.keyDown(input, { key: 'Enter' });

      expect(onChange).toHaveBeenCalledWith('NVDA');
    });
  });

  it('does not intercept Enter when the current value already matches the suggestion', () => {
    const onChange = jest.fn();
    const onKeyDown = jest.fn();
    render(<SymbolAutocompleteInput value="SPY" onChange={onChange} onKeyDown={onKeyDown} aria-label="Symbol" />);
    const input = screen.getByRole('textbox', { name: 'Symbol' });

    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onKeyDown).toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('loads the local catalog once and offers symbols outside the common fallback', async () => {
    catalogSpy.mockResolvedValue(['CTNT', 'CYN', 'DVLT', 'FTEC', 'SNXX', 'IBIT', 'NOW', 'SMCI', 'SOFI']);
    const onChange = jest.fn();
    render(<SymbolAutocompleteInput value="" onChange={onChange} aria-label="Symbol" />);
    const input = screen.getByRole('textbox', { name: 'Symbol' });

    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'ct' } });
    await waitFor(() => expect(catalogSpy).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('option', { name: 'CTNT' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'SOFI' })).toBeInTheDocument();
    expect(catalogSpy).toHaveBeenCalledTimes(1);
  });

  it('does not show hardcoded or recent symbols that are absent from Watchlists', async () => {
    catalogSpy.mockResolvedValue(['CTNT']);
    render(<SymbolAutocompleteInput value="" onChange={jest.fn()} aria-label="Symbol" />);

    fireEvent.focus(screen.getByRole('textbox', { name: 'Symbol' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Symbol' }), { target: { value: 'ct' } });

    expect(await screen.findByRole('option', { name: 'CTNT' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'NVDA' })).not.toBeInTheDocument();
  });

  it('rejects typed symbols that are absent from Watchlists', async () => {
    catalogSpy.mockResolvedValue(['AAPL']);
    function Harness() {
      const [value, setValue] = useState('');
      return <SymbolAutocompleteInput value={value} onChange={setValue} aria-label="Symbol" />;
    }
    render(<Harness />);
    const input = screen.getByRole('textbox', { name: 'Symbol' });
    fireEvent.focus(input);
    await waitFor(() => expect(catalogSpy).toHaveBeenCalledTimes(1));
    fireEvent.change(input, { target: { value: 'zzzz' } });

    await waitFor(() => expect(input).toBeInvalid());
    expect(screen.getByText('No Watchlist matches')).toBeInTheDocument();
    await expect(resolveWatchlistSymbol('ZZZZ')).resolves.toBeNull();
  });

  it('reloads the Watchlist catalog after its cache is cleared', async () => {
    catalogSpy.mockResolvedValueOnce(['AAPL']).mockResolvedValueOnce(['MSFT']);
    await expect(resolveWatchlistSymbol('AAPL')).resolves.toBe('AAPL');
    clearSymbolCatalogCache();
    await expect(resolveWatchlistSymbol('MSFT')).resolves.toBe('MSFT');
  });
});
