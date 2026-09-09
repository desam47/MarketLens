import React, { useState, useEffect, useCallback } from 'react';
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
import { ErrorBanner } from '../components/ErrorBanner';
import { FreshnessIndicator } from '../components/FreshnessIndicator';

interface DashboardProps {
  symbol: string;
  onSymbolChange: (symbol: string) => void;
}

export function Dashboard({ symbol, onSymbolChange }: DashboardProps) {
  const [selectedPreset, setSelectedPreset] = useState<string>('day_trading');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(new Date());
  const [globalError, setGlobalError] = useState<string | null>(null);

  // Individual card states
  const [regime, setRegime] = useState<RegimeData | null>(null);
  const [regimeLoading, setRegimeLoading] = useState(true);
  const [regimeError, setRegimeError] = useState<string | null>(null);

  const [trends, setTrends] = useState<TrendData[]>([]);
  const [trendsLoading, setTrendsLoading] = useState(true);
  const [trendsError, setTrendsError] = useState<string | null>(null);

  const [confluence, setConfluence] = useState<ConfluenceData | null>(null);
  const [confluenceLoading, setConfluenceLoading] = useState(true);
  const [confluenceError, setConfluenceError] = useState<string | null>(null);

  const [strategy, setStrategy] = useState<StrategyData | null>(null);
  const [strategyLoading, setStrategyLoading] = useState(true);
  const [strategyError, setStrategyError] = useState<string | null>(null);

  const [marketContext, setMarketContext] = useState<MarketContextData | null>(null);
  const [marketContextLoading, setMarketContextLoading] = useState(true);
  const [marketContextError, setMarketContextError] = useState<string | null>(null);

  const [sectorData, setSectorData] = useState<SectorData | null>(null);

  // Fetch individual sections independently for progressive rendering
  const fetchRegime = useCallback(async () => {
    setRegimeLoading(true);
    setRegimeError(null);
    try {
      const [regRes, secRes] = await Promise.all([
        api.getRegime(symbol),
        api.getSector(symbol)
      ]);
      setRegime(regRes);
      setSectorData(secRes);
    } catch (err: any) {
      setRegimeError(err?.message || 'Failed to load regime');
    } finally {
      setRegimeLoading(false);
    }
  }, [symbol]);

  const fetchTrends = useCallback(async () => {
    setTrendsLoading(true);
    setTrendsError(null);
    try {
      const data = await api.getTrends(symbol, ['1m', '2m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '1wk']);
      setTrends(data || []);
    } catch (err: any) {
      setTrendsError(err?.message || 'Failed to load trends');
    } finally {
      setTrendsLoading(false);
    }
  }, [symbol]);

  const fetchConfluence = useCallback(async () => {
    setConfluenceLoading(true);
    setConfluenceError(null);
    try {
      const data = await api.getConfluence(symbol, selectedPreset);
      setConfluence(data);
    } catch (err: any) {
      setConfluenceError(err?.message || 'Failed to load confluence');
    } finally {
      setConfluenceLoading(false);
    }
  }, [symbol, selectedPreset]);

  const fetchStrategy = useCallback(async () => {
    setStrategyLoading(true);
    setStrategyError(null);
    try {
      const data = await api.getStrategy(symbol);
      setStrategy(data);
    } catch (err: any) {
      setStrategyError(err?.message || 'Failed to load strategy');
    } finally {
      setStrategyLoading(false);
    }
  }, [symbol]);

  const fetchMarketContext = useCallback(async () => {
    setMarketContextLoading(true);
    setMarketContextError(null);
    try {
      const data = await api.getMarketContext();
      setMarketContext(data);
    } catch (err: any) {
      setMarketContextError(err?.message || 'Failed to load market context');
    } finally {
      setMarketContextLoading(false);
    }
  }, []);

  const fetchAll = useCallback(() => {
    fetchRegime();
    fetchTrends();
    fetchConfluence();
    fetchStrategy();
    fetchMarketContext();
    setLastUpdated(new Date());
  }, [fetchRegime, fetchTrends, fetchConfluence, fetchStrategy, fetchMarketContext]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchAll();
    }, 30000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchAll]);

  const isRefreshing = regimeLoading || trendsLoading || confluenceLoading || strategyLoading || marketContextLoading;

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
          <SymbolInput symbol={symbol} onChange={onSymbolChange} onSubmit={fetchAll} />
          <button
            className={`btn ${isRefreshing ? 'btn-loading' : ''}`}
            onClick={fetchAll}
            disabled={isRefreshing}
          >
            {isRefreshing ? '⟳ Refreshing...' : '↻ Refresh'}
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
          Last updated: {lastUpdated.toLocaleString()}
        </div>
      )}

      {globalError && <ErrorBanner message={globalError} onDismiss={() => setGlobalError(null)} />}

      <div className="dashboard-grid">
        {/* Regime Card with Skeleton/Loading state */}
        <div className={regimeLoading && !regime ? 'card-loading-skeleton' : ''}>
          <RegimeCard regime={regime} sectorData={sectorData} error={regimeError} />
        </div>

        {/* Market Context Card */}
        <div className={marketContextLoading && !marketContext ? 'card-loading-skeleton' : ''}>
          <MarketContextCard context={marketContext} error={marketContextError} />
        </div>

        {/* Confluence Card */}
        <div className={confluenceLoading && !confluence ? 'card-loading-skeleton' : ''}>
          <ConfluenceCard
            confluence={confluence}
            error={confluenceError}
            selectedPreset={selectedPreset}
            onPresetChange={setSelectedPreset}
          />
        </div>

        {/* Strategy Card */}
        <div className={strategyLoading && !strategy ? 'card-loading-skeleton' : ''}>
          <StrategyCard strategy={strategy} error={strategyError} />
        </div>

        {/* Trends Section */}
        <div className="trends-section">
          <h2>Multi-Timeframe Trend</h2>
          {trendsLoading && trends.length === 0 ? (
            <div className="card-loading-skeleton" style={{ height: '150px' }} />
          ) : (
            <>
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
            </>
          )}
          {trends.length === 0 && !trendsLoading && !trendsError && (
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
    </div>
  );
}
