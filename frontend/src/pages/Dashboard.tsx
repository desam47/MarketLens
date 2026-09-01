import React, { useState, useEffect } from 'react';
import api, { RegimeData, TrendData, ConfluenceData, StrategyData, MarketContextData, SectorData } from '../services/api';
import { RegimeCard } from '../components/RegimeCard';
import { TrendCard } from '../components/TrendCard';
import { ConfluenceCard } from '../components/ConfluenceCard';
import { StrategyCard } from '../components/StrategyCard';
import { MarketContextCard } from '../components/MarketContextCard';
import { TopMoversCard } from '../components/TopMoversCard';
import { TransitionsMiniCard } from '../components/TransitionsMiniCard';
import { SymbolInput } from '../components/SymbolInput';
import { NLSearchBar } from '../components/NLSearchBar';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { DashboardSkeleton } from '../components/skeletons/DashboardSkeleton';
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
  const [marketContext, setMarketContext] = useState<MarketContextData | null>(null);
  const [marketContextError, setMarketContextError] = useState<string | null>(null);
  const [sectorData, setSectorData] = useState<SectorData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState<string>('day_trading');

  const fetchData = async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

    // Reset per-card errors so a successful retry clears the red border.
    setRegimeError(null);
    setTrendsError(null);
    setConfluenceError(null);
    setStrategyError(null);
    setMarketContextError(null);

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
      const [regimeResult, trendsResult, confluenceResult, strategyResult, mktCtxResult, sectorResult] = await Promise.all([
        safeCall(() => api.getRegime(symbol)),
        safeCall(() => api.getTrends(symbol, ['1m', '2m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '1wk'])),
        safeCall(() => api.getConfluence(symbol, selectedPreset)),
        safeCall(() => api.getStrategy(symbol)),
        safeCall(() => api.getMarketContext()),
        safeCall(() => api.getSector(symbol)),
      ]);

      setRegime(regimeResult.data);
      setRegimeError(regimeResult.error);
      setTrends(trendsResult.data || []);
      setTrendsError(trendsResult.error);
      setConfluence(confluenceResult.data);
      setConfluenceError(confluenceResult.error);
      setStrategy(strategyResult.data);
      setStrategyError(strategyResult.error);
      setMarketContext(mktCtxResult.data);
      setMarketContextError(mktCtxResult.error);
      setSectorData(sectorResult.data);
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
  }, [symbol, selectedPreset]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => fetchData(true), 30000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh, symbol, selectedPreset]);

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
        <DashboardSkeleton />
      ) : (
        <div className="dashboard-grid">
          <RegimeCard regime={regime} sectorData={sectorData} error={regimeError} />
          <MarketContextCard context={marketContext} error={marketContextError} />
          <ConfluenceCard
            confluence={confluence}
            error={confluenceError}
            selectedPreset={selectedPreset}
            onPresetChange={setSelectedPreset}
          />
          <StrategyCard strategy={strategy} error={strategyError} />
          <div className="trends-section">
            <h2>Multi-Timeframe Trend</h2>
            <div className="trend-grid">
              {trends.slice(0, 5).map((trend) => (
                <TrendCard key={trend.timeframe} trend={trend} />
              ))}
            </div>
            <div className="trend-grid">
              {trends.slice(5).map((trend) => (
                <TrendCard key={trend.timeframe} trend={trend} />
              ))}
            </div>
            {trends.length === 0 && !trendsError && (
              <p className="empty-state">No trend data available</p>
            )}
            {trendsError && (
              <p className="empty-state">⚠ Failed to load trends: {trendsError}</p>
            )}
          </div>
          <TopMoversCard onSelectSymbol={onSymbolChange} />
          <NLSearchBar onSelectSymbol={onSymbolChange} />
          <TransitionsMiniCard symbol={symbol} onSelectSymbol={onSymbolChange} />
        </div>
      )}
    </div>
  );
}
