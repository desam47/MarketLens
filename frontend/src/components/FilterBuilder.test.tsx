import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { FilterBuilder } from './FilterBuilder';
import api from '../services/api';

jest.mock('../services/api', () => ({
  __esModule: true,
  default: {
    applyFilter: jest.fn(),
  },
}));

const mockApi = api as unknown as { applyFilter: jest.Mock };

function open() {
  fireEvent.click(screen.getByText('Filter Builder'));
}

function addFilterAndSetMatch(match: 'AND' | 'OR') {
  fireEvent.click(screen.getByText('+ Add Filter'));
  fireEvent.change(document.querySelector('.filter-match-select') as HTMLSelectElement, {
    target: { value: match },
  });
}

describe('FilterBuilder auto-apply debounce', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockApi.applyFilter.mockReset();
    mockApi.applyFilter.mockResolvedValue([]);
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  // Regression 2026-09-18: clearing filters while match='OR' used to leave
  // the auto-apply effect's skip guard unsatisfied (it only skipped the
  // AND case), so ~600ms after Clear the effect fired api.applyFilter with
  // an empty filter list. The backend treats an empty filter list as
  // TrueFilter (matches everything) regardless of AND/OR, so that call
  // silently re-activated filter mode right after the user cleared it.
  it('does not re-apply after Clear even when match was OR', async () => {
    const onResults = jest.fn();
    const onClear = jest.fn();
    render(<FilterBuilder symbols={['AAPL']} onResults={onResults} onClear={onClear} />);

    open();
    addFilterAndSetMatch('OR');

    // Let the initial add+match-change debounce settle and apply once.
    await act(async () => {
      jest.advanceTimersByTime(600);
      await Promise.resolve();
    });
    expect(mockApi.applyFilter).toHaveBeenCalledTimes(1);
    mockApi.applyFilter.mockClear();

    fireEvent.click(screen.getByText('Clear'));
    expect(onClear).toHaveBeenCalledTimes(1);

    await act(async () => {
      jest.advanceTimersByTime(600);
      await Promise.resolve();
    });

    expect(mockApi.applyFilter).not.toHaveBeenCalled();
  });

  it('still auto-applies after a debounced filter change (not a regression of the intended feature)', async () => {
    const onResults = jest.fn();
    const onClear = jest.fn();
    render(<FilterBuilder symbols={['AAPL']} onResults={onResults} onClear={onClear} />);

    open();
    fireEvent.click(screen.getByText('+ Add Filter'));

    await act(async () => {
      jest.advanceTimersByTime(600);
      await Promise.resolve();
    });

    expect(mockApi.applyFilter).toHaveBeenCalledTimes(1);
    expect(onResults).toHaveBeenCalledTimes(1);
  });
});
