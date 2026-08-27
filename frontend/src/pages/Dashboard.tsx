import React, { useState, useEffect } from 'react';
import api, { RegimeData, TrendData, ConfluenceData, StrategyData } from '../services/api';
import { RegimeCard } from '../components/RegimeCard';
import { TrendCard } from '../components/TrendCard';
import { ConfluenceCard } from '../components/ConfluenceCard';
import { StrategyCard } from '../components/StrategyCard';
import { SymbolInput } from '../components/SymbolInput';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { FreshnessIndicator } from '../components/FreshnessIndicator';

interface DashboardProps {
  symbol: string;
  onSymbolChange: (symbol: string) => void;
}

export function Dashboard({ symbol, onSymbolChange }: DashboardProps) {
  const [regime, setRegime] = useState<RegimeData | null>(null);
  const [regimeError, setRegimeError] = useState<string | null>(null);
  const [trends, setTrends] = useState<TrendData[]>([]);
  const [trendsError, setTrendsError] = useState<string | null>(null);
  const [confluence, setConfluence] = useState<ConfluenceData | null>(null);
  const [confluenceError, setConfluenceError] = useState<string | null>(null);
  const [strategy, setStrategy] = useState<StrategyData | null>(null);
  const [strategyError, setStrategyError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const fetchData = async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

    // Reset per-card errors so a successful retry clears the red border.
    setRegimeError(null);
    setTrendsError(null);
    setConfluenceError(null);
    setStrategyError(null);

    // Each call is independent — one failing shouldn't blank the whole page.
    // We capture (data, error) per call so each card can show its own
    // status instead of silently rendering as empty.
    const safeCall = async <T,>(fn: () => Promise<T>): Promise<{ data: T | null; error: string | null }> => {
      try {
        const data = await fn();
        return { data, error: null };
      } catch (err: any) {
        return { data: null, error: err?.message || 'Request failed' };
      }
    };

    try {
      const [regimeResult, trendsResult, confluenceResult, strategyResult] = await Promise.all([
        safeCall(() => api.getRegime(symbol)),
        safeCall(() => api.getTrends(symbol, ['15m', '1h', '4h', '1d'])),
        safeCall(() => api.getConfluence(symbol)),
        safeCall(() => api.getStrategy(symbol)),
      ]);

      setRegime(regimeResult.data);
      setRegimeError(regimeResult.error);
      setTrends(trendsResult.data || []);
      setTrendsError(trendsResult.error);
      setConfluence(confluenceResult.data);
      setConfluenceError(confluenceResult.error);
      setStrategy(strategyResult.data);
      setStrategyError(strategyResult.error);
      setLastUpdated(new Date());
    } catch (err: any) {
      setError(err.message || 'Failed to fetch data');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => fetchData(true), 30000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh, symbol]);

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <div>
          <h1>Market Analysis Dashboard</h1>
          <p className="subtitle">
            Real-time market intelligence for {symbol}
            {' · '}
            <FreshnessIndicator regime={regime} />
          </p>
        </div>
        <div className="header-actions">
          <SymbolInput symbol={symbol} onChange={onSymbolChange} onSubmit={() => fetchData(true)} />
          <button
            className={`btn ${refreshing ? 'btn-loading' : ''}`}
            onClick={() => fetchData(true)}
            disabled={refreshing}
          >
            {refreshing ? '⟳ Refreshing...' : '↻ Refresh'}
          </button>
          <label className="auto-refresh">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto (30s)
          </label>
        </div>
      </div>

      {lastUpdated && (
        <div className="last-updated">
          Last updated: {lastUpdated.toLocaleTimeString()}
        </div>
      )}

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {loading ? (
        <LoadingSpinner message="Loading market data..." />
      ) : (
        <div className="dashboard-grid">
          <RegimeCard regime={regime} error={regimeError} />
          <ConfluenceCard confluence={confluence} error={confluenceError} />
          <StrategyCard strategy={strategy} error={strategyError} />
          <div className="trends-section">
            <h2>Multi-Timeframe Trend</h2>
            <div className="trend-grid">
              {trends.map((trend) => (
                <TrendCard key={trend.timeframe} trend={trend} />
              ))}
              {trends.length === 0 && !trendsError && (
                <p className="empty-state">No trend data available</p>
              )}
              {trendsError && (
                <p className="empty-state">⚠ Failed to load trends: {trendsError}</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
