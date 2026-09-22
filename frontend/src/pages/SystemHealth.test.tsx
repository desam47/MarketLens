import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import api from '../services/api';
import { SystemHealth } from './SystemHealth';

describe('SystemHealth', () => {
  beforeEach(() => {
    jest.spyOn(api, 'getHealth').mockResolvedValue({ status: 'healthy', service: 'MarketLens', version: 'test' });
    jest.spyOn(api, 'getSystemConfig').mockResolvedValue({
      service: 'MarketLens', version: 'test', market_data_primary_provider: 'yfinance',
      market_data_fallback_providers: [], ai_enabled: false, config_source: 'env', timestamp: '2026-09-21T14:00:00Z',
    });
    jest.spyOn(api, 'getSystemStatus').mockResolvedValue({
      service: 'MarketLens', version: 'test', debug: false, startup_mode: 'full',
      market_data_provider: 'yfinance', market_data_fallback_providers: [], ai_enabled: false,
      timestamp: '2026-09-21T14:00:00Z',
    });
    jest.spyOn(api, 'getIngestionStatus').mockResolvedValue({ is_running: true, timeframes: ['1m'] });
    jest.spyOn(api, 'getBackupStatus').mockResolvedValue({
      timestamp: '2026-09-21T14:00:00Z', journal_mode: 'wal', wal_checkpoint_busy: false,
      wal_checkpoint_frames: 0, wal_checkpoint_end: 0, wal_size_bytes: 0, shm_size_bytes: 0,
      litestream_reachable: false, litestream_generation: null, litestream_dbs: null,
    });
    jest.spyOn(api, 'getSystemPerformance').mockResolvedValue({
      timestamp: '2026-09-21T14:00:00Z',
      ingestion: { is_running: true, total_bars_ingested: 100, last_bar_time: '2026-09-21T13:59:45Z', tf_update_latency_seconds: 15 },
      cache: {
        cache: { bar_hits: 9, bar_misses: 1, quote_hits: 4, quote_misses: 1, bar_hit_rate: 90, quote_hit_rate: 80 },
        redis: { enabled: true, connected: true, status: 'running' },
      },
      providers: { yfinance: { is_healthy: true, circuit_breaker_state: 'CLOSED' } },
    });
    jest.spyOn(api, 'getAuxiliaryProviderStatuses').mockResolvedValue({
      timestamp: '2026-09-21T14:00:00Z',
      news: [{ provider_name: 'finnhub', provider_type: 'news', is_healthy: true, last_error: null, last_success: '2026-09-21T14:00:00Z', timestamp: '2026-09-21T14:00:00Z' }],
      fundamentals: [],
      options: [],
    });
  });

  afterEach(() => jest.restoreAllMocks());

  it('shows cache, freshness, and provider availability without exposing configuration secrets', async () => {
    render(<SystemHealth />);

    await waitFor(() => expect(screen.getByText('Quote Cache Hit Rate:')).toBeInTheDocument());
    expect(screen.getByText('80.0%')).toBeInTheDocument();
    expect(screen.getByText('Pipeline:')).toBeInTheDocument();
    expect(screen.getByText('Fresh')).toBeInTheDocument();
    expect(screen.getByText('Redis:')).toBeInTheDocument();
    expect(screen.getAllByText('Running')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: 'Provider Availability' })).toBeInTheDocument();
    expect(screen.getAllByText('yfinance')).toHaveLength(2);
    expect(screen.getByText('finnhub')).toBeInTheDocument();
  });
});
