const API_BASE = process.env.REACT_APP_API_URL || 'http://localhost:5001/api';

export interface RegimeData {
  symbol: string;
  regime: string;
  confidence: number;
  strength: number;
  supporting_factors: Record<string, any>;
  timestamp: string | null;
  data_age_seconds: number | null;
  freshness: 'fresh' | 'recent' | 'stale' | 'stuck' | 'unknown';
}

export interface TrendData {
  symbol: string;
  timeframe: string;
  direction: string;
  strength: string;
  confidence: number;
  timestamp: string | null;
}

export interface ConfluenceData {
  symbol: string;
  direction: string;
  strength: number;
  alignment_score: number;
  timeframe_signals: Record<string, any>;
  timestamp: string | null;
}

export interface StrategyData {
  symbol: string;
  strategy_type: string;
  confidence: number;
  timeframe: string;
  parameters: Record<string, any>;
  regime_signal: any;
  trend_signal: any;
  confluence_signal: any;
  timestamp: string | null;
}

export interface HealthData {
  status: string;
  service: string;
  version: string;
}

export interface SystemStatus {
  service: string;
  version: string;
  debug: boolean;
  market_data_provider: string;
  ai_enabled: boolean;
  timestamp: string;
}

export interface Watchlist {
  id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  symbols?: WatchlistSymbol[];
}

export interface WatchlistSymbol {
  id: number;
  watchlist_id: number;
  symbol: string;
  is_enabled: boolean;
  added_at: string;
  position: number;
}

// Alerts
export interface Alert {
  id: number;
  name: string;
  symbol: string;
  condition_type: string;
  parameter: string;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AlertTrigger {
  id: number;
  alert_id: number;
  symbol: string;
  observed_value: string | null;
  message: string | null;
  triggered_at: string;
}

// Backtest
export type BacktestStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface BacktestRun {
  id: number;
  symbol: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  signals_requested: string;
  status: BacktestStatus;
  total_bars: number | null;
  total_signals: number | null;
  win_rate_1d: number | null;
  avg_return_1d: number | null;
  avg_return_5d: number | null;
  avg_return_20d: number | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface BacktestTrade {
  id: number;
  run_id: number;
  signal: string;
  entry_date: string;
  entry_price: number;
  exit_date_1d: string | null;
  exit_price_1d: number | null;
  return_1d: number | null;
  exit_date_5d: string | null;
  exit_price_5d: number | null;
  return_5d: number | null;
  exit_date_20d: string | null;
  exit_price_20d: number | null;
  return_20d: number | null;
}

class ApiService {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE) {
    this.baseUrl = baseUrl;
  }

  private async fetch<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }

  /**
   * DELETE returns 204 No Content. ``fetch<T>`` always calls
   * ``response.json()`` which would throw on 204, so deletes go
   * through this helper instead.
   */
  private async del(endpoint: string, options?: RequestInit): Promise<void> {
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
  }

  // Health & System
  async getHealth(): Promise<HealthData> {
    return this.fetch<HealthData>('/health');
  }

  async getSystemStatus(): Promise<SystemStatus> {
    return this.fetch<SystemStatus>('/system/status');
  }

  // Market Regime
  async getRegime(symbol: string): Promise<RegimeData> {
    return this.fetch<RegimeData>(`/regime/${symbol}/current`);
  }

  async getRegimeHistory(symbol: string, limit: number = 10): Promise<{ symbol: string; history: RegimeData[]; count: number }> {
    return this.fetch(`/regime/${symbol}/history?limit=${limit}`);
  }

  async updateRegime(symbol: string, price: number, volume: number, high?: number, low?: number): Promise<any> {
    return this.fetch(`/regime/${symbol}/update`, {
      method: 'POST',
      body: JSON.stringify({ price, volume, high, low }),
    });
  }

  // Trend Analysis
  async getTrend(symbol: string, timeframe: string): Promise<TrendData> {
    return this.fetch<TrendData>(`/trend/${symbol}/current/${timeframe}`);
  }

  async getTrends(symbol: string, timeframes: string[] = ['1h', '4h', '1d']): Promise<TrendData[]> {
    const results = await Promise.all(
      timeframes.map(tf => this.getTrend(symbol, tf).catch(() => null))
    );
    return results.filter((r): r is TrendData => r !== null);
  }

  async getTrendHistory(symbol: string, timeframe: string, limit: number = 10): Promise<any> {
    return this.fetch(`/trend/${symbol}/history/${timeframe}?limit=${limit}`);
  }

  // Multi-Timeframe
  async getConfluence(symbol: string): Promise<ConfluenceData> {
    return this.fetch<ConfluenceData>(`/multitimeframe/${symbol}/confluence`);
  }

  async getConfluenceHistory(symbol: string, limit: number = 10): Promise<any> {
    return this.fetch(`/multitimeframe/${symbol}/history?limit=${limit}`);
  }

  // Strategy
  async getStrategy(symbol: string): Promise<StrategyData> {
    return this.fetch<StrategyData>(`/strategy/${symbol}/current`);
  }

  async getStrategyHistory(symbol: string, limit: number = 10): Promise<any> {
    return this.fetch(`/strategy/${symbol}/history?limit=${limit}`);
  }

  // Watchlists (backend uses /api/watchlists prefix - plural)
  async getWatchlists(): Promise<Watchlist[]> {
    return this.fetch<Watchlist[]>('/watchlists/');
  }

  async getWatchlist(id: number): Promise<Watchlist> {
    return this.fetch<Watchlist>(`/watchlists/${id}`);
  }

  async getWatchlistSymbols(watchlistId: number): Promise<WatchlistSymbol[]> {
    return this.fetch<WatchlistSymbol[]>(`/watchlists/${watchlistId}/symbols`);
  }

  async createWatchlist(name: string, description?: string): Promise<Watchlist> {
    return this.fetch<Watchlist>('/watchlists/', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    });
  }

  async updateWatchlist(id: number, data: Partial<Watchlist>): Promise<Watchlist> {
    return this.fetch<Watchlist>(`/watchlists/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async deleteWatchlist(id: number): Promise<void> {
    return this.del(`/watchlists/${id}`);
  }

  async addSymbolToWatchlist(watchlistId: number, symbol: string): Promise<WatchlistSymbol> {
    return this.fetch<WatchlistSymbol>(`/watchlists/${watchlistId}/symbols`, {
      method: 'POST',
      body: JSON.stringify({ symbol }),
    });
  }

  async removeSymbolFromWatchlist(watchlistId: number, symbol: string): Promise<void> {
    return this.del(`/watchlists/${watchlistId}/symbols/${symbol}`);
  }

  async reorderSymbols(watchlistId: number, symbols: string[]): Promise<void> {
    return this.fetch(`/watchlists/${watchlistId}/symbols/reorder`, {
      method: 'PUT',
      body: JSON.stringify(symbols),
    });
  }

  // Alerts
  async getAlerts(): Promise<Alert[]> {
    return this.fetch<Alert[]>('/alerts/');
  }

  async createAlert(payload: {
    name: string;
    symbol: string;
    condition_type: string;
    parameter: string;
  }): Promise<Alert> {
    return this.fetch<Alert>('/alerts/', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateAlert(id: number, payload: Partial<Alert>): Promise<Alert> {
    return this.fetch<Alert>(`/alerts/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  }

  async deleteAlert(id: number): Promise<void> {
    return this.del(`/alerts/${id}`);
  }

  async getAlertTriggers(alertId: number, limit = 100): Promise<AlertTrigger[]> {
    return this.fetch<AlertTrigger[]>(`/alerts/${alertId}/triggers?limit=${limit}`);
  }

  async getActiveAlertTriggers(): Promise<AlertTrigger[]> {
    return this.fetch<AlertTrigger[]>('/alerts/active');
  }

  // Backtest
  async getBacktestRuns(limit = 20): Promise<BacktestRun[]> {
    return this.fetch<BacktestRun[]>(`/backtest/?limit=${limit}`);
  }

  async createBacktest(payload: {
    symbol: string;
    start_date: string;
    end_date: string;
    signals?: string[];
  }): Promise<BacktestRun> {
    return this.fetch<BacktestRun>('/backtest/', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async getBacktestRun(id: number): Promise<BacktestRun> {
    return this.fetch<BacktestRun>(`/backtest/${id}`);
  }

  async getBacktestTrades(id: number): Promise<BacktestTrade[]> {
    return this.fetch<BacktestTrade[]>(`/backtest/${id}/trades`);
  }

  async deleteBacktestRun(id: number): Promise<void> {
    return this.del(`/backtest/${id}`);
  }

  // Market Data
  async getQuote(symbol: string): Promise<any> {
    return this.fetch(`/market-data/quote/${symbol}`);
  }

  async getLatestBars(symbol: string): Promise<Record<string, any>> {
    return this.fetch(`/market-data/bars/${symbol}`);
  }

  async getIngestionStatus(): Promise<any> {
    return this.fetch('/market-data/ingestion/status');
  }
}

export const api = new ApiService();
export default api;
