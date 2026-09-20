import { render, screen, waitFor } from '@testing-library/react';
import { CandlestickChart } from './CandlestickChart';

const mockSeries = {
  setData: jest.fn(),
  setMarkers: jest.fn(),
};
const mockChart = {
  addCandlestickSeries: jest.fn(() => mockSeries),
  addBarSeries: jest.fn(() => mockSeries),
  addLineSeries: jest.fn(() => mockSeries),
  addAreaSeries: jest.fn(() => mockSeries),
  addHistogramSeries: jest.fn(() => mockSeries),
  removeSeries: jest.fn(),
  remove: jest.fn(),
  applyOptions: jest.fn(),
  priceScale: jest.fn(() => ({ applyOptions: jest.fn() })),
  timeScale: jest.fn(() => ({ fitContent: jest.fn() })),
};

jest.mock('lightweight-charts', () => ({
  createChart: jest.fn(() => mockChart),
  ColorType: { Solid: 'solid' },
  CrosshairMode: { Normal: 0 },
}));

class ResizeObserverMock {
  observe() {}
  disconnect() {}
}

describe('CandlestickChart controlled initial type', () => {
  beforeAll(() => {
    (global as any).ResizeObserver = ResizeObserverMock;
  });

  it('synchronizes when a shared parent toolbar changes initialChartType', async () => {
    const { rerender } = render(
      <CandlestickChart bars={[]} symbol="SPY" initialChartType="heikin-ashi" />,
    );

    expect(screen.getByRole('button', { name: 'HA' })).toHaveClass('active');

    rerender(<CandlestickChart bars={[]} symbol="SPY" initialChartType="line" />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Line' })).toHaveClass('active');
    });
  });
});
