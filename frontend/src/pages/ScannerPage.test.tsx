import React from 'react';
import { render, screen } from '@testing-library/react';
import api from '../services/api';
import { ScannerPage } from './ScannerPage';

jest.mock('../components/FilterBuilder', () => ({
  FilterBuilder: () => <div>Filter builder</div>,
}));
jest.mock('../components/NamedRankingsPanel', () => ({
  NamedRankingsPanel: () => <div>Rankings</div>,
}));

describe('ScannerPage data states', () => {
  afterEach(() => jest.restoreAllMocks());

  it('explains how to populate an empty scan universe', async () => {
    jest.spyOn(api, 'getWatchlists').mockResolvedValue([]);

    render(<ScannerPage onSelectSymbol={jest.fn()} />);

    expect(await screen.findByText('No watchlists yet.')).toBeInTheDocument();
    expect(screen.getByText('Create a watchlist and add symbols before running a scan.')).toBeInTheDocument();
    expect(screen.getByText('Choose a quick scan or add filters to search your watchlist.')).toBeInTheDocument();
  });
});
