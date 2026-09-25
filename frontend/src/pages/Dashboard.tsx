import React, { useState, useEffect, useCallback, useRef } from 'react';
import api, { MarketContextData } from '../services/api';
import { MarketContextCard } from '../components/MarketContextCard';
import { TopMoversCard } from '../components/TopMoversCard';
import { DigestCard } from '../components/DigestCard';
import { NLSearchBar } from '../components/NLSearchBar';
import { SkeletonBlock } from '../components/SkeletonBlock';

// Per-card skeletons rather than the page-level DashboardSkeleton
// component (components/skeletons/DashboardSkeleton.tsx): that one
// blocks on the whole page loading at once and hardcodes a stale layout
// (4 trend cards, no Digest card) from before this page grew to 10
// trend cards + 4 bottom cards. Dashboard deliberately renders each
// section as soon as its own data arrives — these mirror that, so the
// loading state doesn't regress into "nothing renders until everything
// is ready."
function SkeletonCard({ rows = 3 }: { rows?: number }) {
  return (
    <div className="card">
      <SkeletonBlock width="50%" height="1.1rem" />
      <div className="skeleton-rows">
        {Array.from({ length: rows }).map((_, i) => (
          <SkeletonBlock key={i} width={i === rows - 1 ? '70%' : '100%'} height="0.85rem" />
        ))}
      </div>
    </div>
  );
}


interface DashboardProps {
  symbol: string;
  onSymbolChange: (symbol: string) => void;
  /** Open the symbol chart at a specific timeframe (TC-09 chart handoff). */
  onOpenChart?: (symbol: string, timeframe: string) => void;
}

type DashboardSectionId =
  | 'market_context'
  | 'movers'
  | 'search'
  | 'digest';

const SECTION_LABELS: Record<DashboardSectionId, string> = {
  market_context: 'Market context',
  movers: 'Top movers',
  search: 'Natural-language search',
  digest: 'Market digest',
};

const ALL_SECTIONS = Object.keys(SECTION_LABELS) as DashboardSectionId[];

const DEFAULT_LAYOUT = {
  visible: ALL_SECTIONS,
  order: ['movers', 'market_context', 'search', 'digest'] as DashboardSectionId[],
};

export function Dashboard({ symbol, onSymbolChange, onOpenChart }: DashboardProps) {
  const [autoRefresh, setAutoRefresh] = useState(true);

  // Individual card states
  const [marketContext, setMarketContext] = useState<MarketContextData | null>(null);
  const [marketContextLoading, setMarketContextLoading] = useState(true);
  const [marketContextError, setMarketContextError] = useState<string | null>(null);

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

  const isFetchingAllRef = useRef(false);

  const fetchAll = useCallback(() => {
    fetchMarketContext();
  }, [fetchMarketContext]);

  const fetchAllIfIdle = useCallback(() => {
    if (isFetchingAllRef.current) return;
    fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    fetchMarketContext();
  }, [fetchMarketContext]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchAllIfIdle();
    }, 30000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchAllIfIdle]);

  useEffect(() => {
    if (!autoRefresh) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') fetchAllIfIdle();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [autoRefresh, fetchAllIfIdle]);

  const isRefreshing = marketContextLoading;

  const activeLayout = DEFAULT_LAYOUT;

  const renderDashboardSection = (section: DashboardSectionId): React.ReactNode => {
    switch (section) {
      case 'market_context':
        return marketContextLoading && !marketContext ? <SkeletonCard rows={3} /> : <MarketContextCard context={marketContext} error={marketContextError} onRetry={fetchMarketContext} />;
      case 'movers':
        return <TopMoversCard onSelectSymbol={onSymbolChange} autoRefresh={autoRefresh} />;
      case 'search':
        return <NLSearchBar onSelectSymbol={onSymbolChange} />;
      case 'digest':
        return <DigestCard />;
      default:
        return null;
    }
  };

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <h1>Market Analysis Dashboard</h1>
        <div className="header-actions">
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

      <div className="dashboard-grid">
        {activeLayout.order.filter(section => activeLayout.visible.includes(section)).map(section => (
          <React.Fragment key={section}>{renderDashboardSection(section)}</React.Fragment>
        ))}
      </div>
    </div>
  );
}
