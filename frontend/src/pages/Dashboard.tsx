import React, { useState, useEffect, useCallback, useRef } from 'react';
import api, { RegimeData, TrendData, ConfluenceData, StrategyData, MarketContextData, SectorData, RealtimeEvent, LiveQuoteUpdateData, RealtimeConnectionStatus } from '../services/api';
import { RegimeCard } from '../components/RegimeCard';
import { TrendCard } from '../components/TrendCard';
import { ConfluenceCard } from '../components/ConfluenceCard';
import { StrategyCard } from '../components/StrategyCard';
import { MarketContextCard } from '../components/MarketContextCard';
import { TopMoversCard } from '../components/TopMoversCard';
import { TransitionsMiniCard } from '../components/TransitionsMiniCard';
import { SymbolInput } from '../components/SymbolInput';
import { DigestCard } from '../components/DigestCard';
import { NLSearchBar } from '../components/NLSearchBar';
import { FreshnessIndicator } from '../components/FreshnessIndicator';
import { formatETDateTime } from '../components/chartMath';
import { SkeletonBlock } from '../components/SkeletonBlock';
import { MarketDataFreshnessBadge } from '../components/MarketDataFreshnessBadge';

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

function TrendCardSkeleton() {
  return (
    <div className="card trend-card">
      <div className="trend-header">
        <SkeletonBlock width="45%" height="0.9rem" />
        <SkeletonBlock width="1.1rem" height="1.1rem" radius={999} />
      </div>
      <SkeletonBlock width="65%" height="1rem" />
      <div className="skeleton-rows">
        <SkeletonBlock width="80%" height="0.75rem" />
        <SkeletonBlock width="60%" height="0.75rem" />
      </div>
    </div>
  );
}

interface DashboardProps {
  symbol: string;
  onSymbolChange: (symbol: string) => void;
}

type DashboardSectionId =
  | 'regime'
  | 'market_context'
  | 'confluence'
  | 'strategy'
  | 'trends'
  | 'movers'
  | 'search'
  | 'digest'
  | 'transitions';

interface DashboardLayout {
  id: string;
  name: string;
  visible: DashboardSectionId[];
  order: DashboardSectionId[];
  builtIn?: boolean;
}

const DASHBOARD_LAYOUT_KEY = 'marketlens.dashboard.layout';
const DASHBOARD_CUSTOM_LAYOUTS_KEY = 'marketlens.dashboard.layouts';

const SECTION_LABELS: Record<DashboardSectionId, string> = {
  regime: 'Market regime',
  market_context: 'Market context',
  confluence: 'Multi-timeframe confluence',
  strategy: 'Strategy guidance',
  trends: 'Multi-timeframe trends',
  movers: 'Top movers',
  search: 'Natural-language search',
  digest: 'Market digest',
  transitions: 'Recent transitions',
};

const ALL_SECTIONS = Object.keys(SECTION_LABELS) as DashboardSectionId[];

const DASHBOARD_PRESETS: DashboardLayout[] = [
  {
    id: 'day_trading', name: 'Day trading', builtIn: true,
    visible: ALL_SECTIONS,
    order: ['movers', 'trends', 'confluence', 'regime', 'market_context', 'search', 'strategy', 'transitions', 'digest'],
  },
  {
    id: 'swing_trading', name: 'Swing trading', builtIn: true,
    visible: ALL_SECTIONS,
    order: ['confluence', 'regime', 'trends', 'strategy', 'market_context', 'transitions', 'movers', 'digest', 'search'],
  },
  {
    id: 'earnings_research', name: 'Earnings research', builtIn: true,
    visible: ALL_SECTIONS,
    order: ['digest', 'market_context', 'regime', 'strategy', 'confluence', 'trends', 'transitions', 'movers', 'search'],
  },
  {
    id: 'options_research', name: 'Options research', builtIn: true,
    visible: ALL_SECTIONS,
    order: ['market_context', 'confluence', 'trends', 'movers', 'regime', 'strategy', 'digest', 'transitions', 'search'],
  },
  {
    id: 'long_term', name: 'Long-term investing', builtIn: true,
    visible: ALL_SECTIONS,
    order: ['regime', 'market_context', 'strategy', 'trends', 'confluence', 'digest', 'transitions', 'movers', 'search'],
  },
];

function readCustomLayouts(): DashboardLayout[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(DASHBOARD_CUSTOM_LAYOUTS_KEY) || '[]');
    if (!Array.isArray(value)) return [];
    return value.filter((layout): layout is DashboardLayout => (
      layout && typeof layout.id === 'string' && typeof layout.name === 'string'
      && Array.isArray(layout.visible) && Array.isArray(layout.order)
      && layout.visible.every((section: unknown) => ALL_SECTIONS.includes(section as DashboardSectionId))
      && layout.order.every((section: unknown) => ALL_SECTIONS.includes(section as DashboardSectionId))
    ));
  } catch {
    return [];
  }
}

function readSelectedLayout(): string {
  try {
    const value = window.localStorage.getItem(DASHBOARD_LAYOUT_KEY);
    return value && [...DASHBOARD_PRESETS, ...readCustomLayouts()].some(layout => layout.id === value)
      ? value
      : DASHBOARD_PRESETS[0].id;
  } catch {
    return DASHBOARD_PRESETS[0].id;
  }
}

export function Dashboard({ symbol, onSymbolChange }: DashboardProps) {
  const [customLayouts, setCustomLayouts] = useState<DashboardLayout[]>(readCustomLayouts);
  const [selectedLayoutId, setSelectedLayoutId] = useState<string>(readSelectedLayout);
  const [layoutEditorOpen, setLayoutEditorOpen] = useState(false);
  const [layoutDraft, setLayoutDraft] = useState<DashboardLayout | null>(null);
  const [layoutName, setLayoutName] = useState('');
  const [selectedPreset, setSelectedPreset] = useState<string>('day_trading');
  // Defaults on so a tab left open doesn't silently freeze — matches the
  // fix already applied to the Watchlist and Symbol page this session.
  // Confirmed live: AAPL's regime engine was current server-side, but a
  // Dashboard tab with this off showed "Stuck · 2d ago" the whole time.
  const [autoRefresh, setAutoRefresh] = useState(true);

  // Last close data from price-range endpoint
  const [lastClose, setLastClose] = useState<{
    latest_close: number | null;
    latest_close_timestamp: string | null;
    fetched_at: string | null;
    // Prior session's close — the baseline the live quote's change/change_pct
    // are computed against, so they tick with the price instead of waiting
    // on the 30s price-range refetch.
    prev_close: number | null;
  } | null>(null);

  // Latest quote (for current price)
  const [latestQuote, setLatestQuote] = useState<{
    price: number | null;
    timestamp: string | null;
  } | null>(null);
  const [liveQuote, setLiveQuote] = useState<LiveQuoteUpdateData | null>(null);
  const [quoteConnectionStatus, setQuoteConnectionStatus] = useState<RealtimeConnectionStatus>('closed');
  useEffect(() => {
    const subscriber = api.createRealtimeSubscriber?.();
    if (!subscriber) return;
    const unsubscribe = subscriber.onEvent((event: RealtimeEvent) => {
      if (event.type !== 'quote_update' || event.symbol !== symbol.toUpperCase()) return;
      setLiveQuote(event.data);
    });
    const unsubscribeStatus = subscriber.onStatus(setQuoteConnectionStatus);
    subscriber.subscribeQuote(symbol);
    return () => {
      unsubscribe();
      unsubscribeStatus();
      subscriber.unsubscribeQuote(symbol);
      subscriber.disconnect();
    };
  }, [symbol]);
  useEffect(() => {
    const live = liveQuote;
    if (live?.price == null) return;
    setLatestQuote({ price: live.price, timestamp: live.timestamp });
  }, [liveQuote]);

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
  const [sectorLoading, setSectorLoading] = useState(true);

  // Kept in sync with the latest props on every render (not just on commit
  // via an effect) so in-flight fetches below can tell, the moment they
  // resolve, whether the symbol/preset they were issued for is still the
  // one the user is looking at. A slow response for a since-abandoned
  // symbol/preset is dropped instead of overwriting newer data.
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;
  const presetRef = useRef(selectedPreset);
  presetRef.current = selectedPreset;

  // Fetch individual sections independently for progressive rendering
  const fetchRegime = useCallback(async () => {
    const requestSymbol = symbol;
    setRegimeLoading(true);
    setRegimeError(null);
    try {
      const data = await api.getRegime(requestSymbol);
      if (symbolRef.current !== requestSymbol) return;
      setRegime(data);
    } catch (err: any) {
      if (symbolRef.current !== requestSymbol) return;
      setRegimeError(err?.message || 'Failed to load regime');
    } finally {
      if (symbolRef.current === requestSymbol) setRegimeLoading(false);
    }
  }, [symbol]);

  // Split out from fetchRegime: sector data is supplementary (rendered as
  // an optional footer on the Regime card), so a sector failure shouldn't
  // blank out an otherwise-successful regime fetch, or vice versa.
  const fetchSector = useCallback(async () => {
    const requestSymbol = symbol;
    setSectorLoading(true);
    try {
      const data = await api.getSector(requestSymbol);
      if (symbolRef.current !== requestSymbol) return;
      setSectorData(data);
    } catch {
      if (symbolRef.current !== requestSymbol) return;
      setSectorData(null);
    } finally {
      if (symbolRef.current === requestSymbol) setSectorLoading(false);
    }
  }, [symbol]);

  const fetchTrends = useCallback(async () => {
    const requestSymbol = symbol;
    setTrendsLoading(true);
    setTrendsError(null);
    try {
      const data = await api.getTrends(requestSymbol, ['1m', '2m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '1wk']);
      if (symbolRef.current !== requestSymbol) return;
      setTrends(data || []);
    } catch (err: any) {
      if (symbolRef.current !== requestSymbol) return;
      setTrendsError(err?.message || 'Failed to load trends');
    } finally {
      if (symbolRef.current === requestSymbol) setTrendsLoading(false);
    }
  }, [symbol]);

  const fetchConfluence = useCallback(async () => {
    const requestSymbol = symbol;
    const requestPreset = selectedPreset;
    setConfluenceLoading(true);
    setConfluenceError(null);
    try {
      // Use snapshot endpoint for richer data (trend states, quality metrics)
      const response = await api.getMTFSnapshot(requestSymbol, requestPreset);
      if (symbolRef.current !== requestSymbol || presetRef.current !== requestPreset) return;
      const snap = response.snapshot;
      if (snap) {
        // Transform snapshot to ConfluenceData format
        const data = {
          symbol: snap.symbol,
          direction: snap.direction,
          strength: snap.strength,
          alignment_score: snap.alignment_score,
          timeframe_signals: Object.fromEntries(
            Object.entries(snap.timeframe_snapshots).map(([tf, tfSnap]) => [
              tf,
              {
                direction: tfSnap.direction,
                strength: tfSnap.strength,
                confidence: tfSnap.confidence,
                timestamp: tfSnap.timestamp,
              },
            ])
          ),
          timestamp: snap.timestamp,
          bullish_alignment: snap.bullish_alignment,
          bearish_alignment: snap.bearish_alignment,
          conflicting: snap.conflicting,
          short_term_direction: snap.short_term_direction,
          intermediate_direction: snap.intermediate_direction,
          higher_direction: snap.higher_direction,
          preset: snap.preset,
          short_term_state: snap.short_term_state,
          intermediate_state: snap.intermediate_state,
          higher_state: snap.higher_state,
        };
        setConfluence(data);
      } else {
        setConfluence(null);
      }
    } catch (err: any) {
      if (symbolRef.current !== requestSymbol || presetRef.current !== requestPreset) return;
      setConfluenceError(err?.message || 'Failed to load confluence');
    } finally {
      if (symbolRef.current === requestSymbol && presetRef.current === requestPreset) setConfluenceLoading(false);
    }
  }, [symbol, selectedPreset]);

  const fetchStrategy = useCallback(async () => {
    const requestSymbol = symbol;
    setStrategyLoading(true);
    setStrategyError(null);
    try {
      const data = await api.getStrategy(requestSymbol);
      if (symbolRef.current !== requestSymbol) return;
      setStrategy(data);
    } catch (err: any) {
      if (symbolRef.current !== requestSymbol) return;
      setStrategyError(err?.message || 'Failed to load strategy');
    } finally {
      if (symbolRef.current === requestSymbol) setStrategyLoading(false);
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

  const fetchLastClose = useCallback(async () => {
    const requestSymbol = symbol;
    try {
      const data = await api.getPriceRange(requestSymbol, '1d');
      if (symbolRef.current !== requestSymbol) return;
      // Today's period entry in price_history carries close and
      // change (vs. the prior close), so prev_close = close - change.
      // Uses "today_high" or "today_low" type; the period key is "today"
      let prev_close: number | null = null;
      if (data.price_history && Array.isArray(data.price_history)) {
        const todayEntry = data.price_history.find((entry: any) =>
          entry.type === 'today_high' || entry.type === 'today_low'
        );
        if (todayEntry && todayEntry.close != null && todayEntry.change != null) {
          prev_close = todayEntry.close - todayEntry.change;
        }
      }
      setLastClose({
        latest_close: data.latest_close ?? null,
        latest_close_timestamp: data.latest_close_timestamp ?? null,
        fetched_at: data.fetched_at ?? null,
        prev_close,
      });
    } catch (err: any) {
      if (symbolRef.current !== requestSymbol) return;
      console.error('Failed to load last close:', err?.message);
    }
  }, [symbol]);

  const fetchQuote = useCallback(async () => {
    const requestSymbol = symbol;
    try {
      const data = await api.getQuote(requestSymbol);
      if (symbolRef.current !== requestSymbol) return;
      // The quote has no change fields; change/change_pct are derived at
      // render time from this price and lastClose.prev_close.
      setLatestQuote({
        price: data.price ?? null,
        timestamp: data.timestamp ?? null,
      });
    } catch (err: any) {
      if (symbolRef.current !== requestSymbol) return;
      console.error('Failed to load quote:', err?.message);
    }
  }, [symbol]);

  // Tracks whether a fetchSymbolSections cycle is in flight so the
  // interval and visibility-regain triggers below can skip firing a
  // redundant, overlapping cycle on top of one still resolving. Only
  // those two are gated on it — fetchSymbolSections itself always runs
  // unconditionally, since the mount/symbol-change effect and a manual
  // click represent real new intent (a new symbol to load) rather than a
  // passive re-poll, and must never be silently dropped just because the
  // previous symbol's requests hadn't resolved yet.
  const isFetchingAllRef = useRef(false);

  // The five symbol/preset-scoped sections — kept separate from
  // fetchMarketContext (below) since market context doesn't depend on
  // the selected symbol. Bundling it into this used to mean switching
  // symbols refetched the market-wide SPY/QQQ/IWM/VIX aggregate for no
  // reason.
  const fetchSymbolSections = useCallback(() => {
    isFetchingAllRef.current = true;
    const pending = [
      fetchRegime(),
      fetchSector(),
      fetchTrends(),
      fetchConfluence(),
      fetchStrategy(),
      fetchLastClose(),
      fetchQuote(),
    ];
    Promise.all(pending).finally(() => {
      isFetchingAllRef.current = false;
    });
  }, [fetchRegime, fetchSector, fetchTrends, fetchConfluence, fetchStrategy, fetchLastClose, fetchQuote]);

  // Used by the interval/visibility/manual-refresh triggers, which should
  // still catch up market context on their own cadence — just not on
  // every symbol change.
  const fetchAll = useCallback(() => {
    fetchSymbolSections();
    fetchMarketContext();
  }, [fetchSymbolSections, fetchMarketContext]);

  const fetchAllIfIdle = useCallback(() => {
    if (isFetchingAllRef.current) return;
    fetchAll();
  }, [fetchAll]);

  useEffect(() => {
    fetchSymbolSections();
  }, [fetchSymbolSections]);

  // fetchMarketContext has no dependencies (it's symbol-independent), so
  // this effect fires exactly once, on mount — never again on a symbol
  // change. The interval/visibility/manual paths above still refresh it
  // via fetchAll.
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

  // Browsers throttle setInterval heavily in backgrounded/inactive tabs
  // (Chrome can drop a 30s timer to firing once a minute or less), and
  // regime.data_age_seconds is a server-computed snapshot from the last
  // successful fetch, not something that live-ticks on the client — so a
  // tab left in the background sits on an increasingly stale snapshot
  // until its throttled timer eventually fires again. Root cause of a
  // live report (2026-09-16): the freshness pill intermittently showing
  // "Stuck · 1h ago" then recovering, even though the backend regime
  // engine itself was never more than ~3min stale. Refetching immediately
  // on tab-focus-regain closes that gap regardless of how long the timer
  // was throttled.
  useEffect(() => {
    if (!autoRefresh) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') fetchAllIfIdle();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [autoRefresh, fetchAllIfIdle]);

  // REST remains a fallback; the shared WebSocket is the primary live source.
  useEffect(() => {
    if (!autoRefresh) return;
    // Initial fetch
    fetchQuote();
    const interval = setInterval(() => {
      fetchQuote();
    }, 30000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchQuote]);

  const prevClose = lastClose?.prev_close ?? null;
  const effectiveQuote = liveQuote || latestQuote;
  const liveChange =
    effectiveQuote?.price != null && prevClose ? effectiveQuote.price - prevClose : null;
  const liveChangePct =
    liveChange != null && prevClose ? (liveChange / prevClose) * 100 : null;

  const isRefreshing = regimeLoading || sectorLoading || trendsLoading || confluenceLoading || strategyLoading || marketContextLoading;

  const allLayouts = [...DASHBOARD_PRESETS, ...customLayouts];
  const activeLayout = allLayouts.find(layout => layout.id === selectedLayoutId) || DASHBOARD_PRESETS[0];

  useEffect(() => {
    window.localStorage.setItem(DASHBOARD_LAYOUT_KEY, selectedLayoutId);
  }, [selectedLayoutId]);

  useEffect(() => {
    window.localStorage.setItem(DASHBOARD_CUSTOM_LAYOUTS_KEY, JSON.stringify(customLayouts));
  }, [customLayouts]);

  const openLayoutEditor = () => {
    setLayoutDraft({ ...activeLayout, visible: [...activeLayout.visible], order: [...activeLayout.order] });
    setLayoutName(activeLayout.builtIn ? '' : activeLayout.name);
    setLayoutEditorOpen(true);
  };

  const toggleLayoutSection = (section: DashboardSectionId) => {
    setLayoutDraft(current => {
      if (!current) return current;
      const visible = current.visible.includes(section)
        ? current.visible.filter(item => item !== section)
        : [...current.visible, section];
      return { ...current, visible };
    });
  };

  const moveLayoutSection = (section: DashboardSectionId, direction: -1 | 1) => {
    setLayoutDraft(current => {
      if (!current) return current;
      const index = current.order.indexOf(section);
      const nextIndex = index + direction;
      if (index < 0 || nextIndex < 0 || nextIndex >= current.order.length) return current;
      const order = [...current.order];
      [order[index], order[nextIndex]] = [order[nextIndex], order[index]];
      return { ...current, order };
    });
  };

  const saveLayout = () => {
    if (!layoutDraft) return;
    const name = layoutName.trim();
    if (!name) return;
    const id = layoutDraft.builtIn ? `custom_${Date.now()}` : layoutDraft.id;
    const saved: DashboardLayout = {
      id,
      name,
      visible: layoutDraft.visible,
      order: layoutDraft.order,
    };
    setCustomLayouts(current => [...current.filter(layout => layout.id !== id), saved]);
    setSelectedLayoutId(id);
    setLayoutEditorOpen(false);
    setLayoutDraft(null);
  };

  const deleteSelectedLayout = () => {
    if (activeLayout.builtIn) return;
    setCustomLayouts(current => current.filter(layout => layout.id !== activeLayout.id));
    setSelectedLayoutId(DASHBOARD_PRESETS[0].id);
    setLayoutEditorOpen(false);
    setLayoutDraft(null);
  };

  const renderDashboardSection = (section: DashboardSectionId): React.ReactNode => {
    switch (section) {
      case 'regime':
        return regimeLoading && !regime ? <SkeletonCard rows={4} /> : <RegimeCard regime={regime} sectorData={sectorData} error={regimeError} onRetry={fetchRegime} />;
      case 'market_context':
        return marketContextLoading && !marketContext ? <SkeletonCard rows={3} /> : <MarketContextCard context={marketContext} error={marketContextError} onRetry={fetchMarketContext} />;
      case 'confluence':
        return confluenceLoading && !confluence ? <SkeletonCard rows={5} /> : <ConfluenceCard confluence={confluence} error={confluenceError} selectedPreset={selectedPreset} onPresetChange={setSelectedPreset} onRetry={fetchConfluence} />;
      case 'strategy':
        return strategyLoading && !strategy ? <SkeletonCard rows={3} /> : <StrategyCard strategy={strategy} error={strategyError} onRetry={fetchStrategy} />;
      case 'trends':
        return (
          <div className="trends-section">
            <h2>Multi-Timeframe Trend</h2>
            {trendsLoading && trends.length === 0 ? (
              <>
                <div className="trend-grid">{Array.from({ length: 5 }).map((_, i) => <TrendCardSkeleton key={i} />)}</div>
                <div className="trend-grid">{Array.from({ length: 5 }).map((_, i) => <TrendCardSkeleton key={i} />)}</div>
              </>
            ) : (
              <>
                <div className="trend-grid">{trends.slice(0, 5).map(trend => <TrendCard key={trend.timeframe} trend={trend} />)}</div>
                <div className="trend-grid">{trends.slice(5).map(trend => <TrendCard key={trend.timeframe} trend={trend} />)}</div>
              </>
            )}
            {trends.length === 0 && !trendsLoading && !trendsError && <p className="empty-state">No trend data available</p>}
            {trendsError && (
              <div className="empty-state">
                <p>⚠ Failed to load trends: {trendsError}</p>
                <button className="btn btn-small data-state-retry" onClick={fetchTrends}>Retry</button>
              </div>
            )}
          </div>
        );
      case 'movers':
        return <TopMoversCard onSelectSymbol={onSymbolChange} autoRefresh={autoRefresh} />;
      case 'search':
        return <NLSearchBar onSelectSymbol={onSymbolChange} />;
      case 'digest':
        return <DigestCard />;
      case 'transitions':
        return <TransitionsMiniCard symbol={symbol} onSelectSymbol={onSymbolChange} autoRefresh={autoRefresh} />;
      default:
        return null;
    }
  };

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
          {(effectiveQuote && effectiveQuote.price != null) && (
            <div className="last-close-info">
              <span className="last-close-label">Latest Price</span>
              <span className="last-close-price">${effectiveQuote.price.toFixed(4)}</span>
              {liveChange != null && liveChangePct != null && (
                <>
                  <span className={`last-close-change ${liveChange >= 0 ? 'positive' : 'negative'}`}>
                    {liveChange >= 0 ? '+' : ''}{liveChange.toFixed(4)}
                  </span>
                  <span className={`last-close-change-pct ${liveChangePct >= 0 ? 'positive' : 'negative'}`}>
                    ({liveChangePct >= 0 ? '+' : ''}{liveChangePct.toFixed(2)}%)
                  </span>
                </>
              )}
              {effectiveQuote.timestamp && (
                <span className="last-close-fetched">
                  {formatETDateTime(effectiveQuote.timestamp)}
                </span>
              )}
              <MarketDataFreshnessBadge
                dataStatus={liveQuote ? 'LIVE' : 'STALE'}
                timestamp={effectiveQuote.timestamp}
                showAge
                connectionStatus={quoteConnectionStatus}
                provider={liveQuote?.provider}
              />
            </div>
          )}
        </div>
        <div className="header-actions">
          <div className="dashboard-layout-controls">
            <label className="dashboard-layout-select-label">
              <span>Layout</span>
              <select
                aria-label="Dashboard layout"
                value={selectedLayoutId}
                onChange={event => {
                  setSelectedLayoutId(event.target.value);
                  setLayoutEditorOpen(false);
                }}
              >
                <optgroup label="Presets">
                  {DASHBOARD_PRESETS.map(layout => <option key={layout.id} value={layout.id}>{layout.name}</option>)}
                </optgroup>
                {customLayouts.length > 0 && <optgroup label="Saved layouts">{customLayouts.map(layout => <option key={layout.id} value={layout.id}>{layout.name}</option>)}</optgroup>}
              </select>
            </label>
            <button className="btn btn-secondary" type="button" onClick={openLayoutEditor}>Customize</button>
          </div>
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

      {layoutEditorOpen && layoutDraft && (
        <div className="dashboard-layout-editor card">
          <div className="dashboard-layout-editor-header">
            <div><h2>Customize dashboard</h2><p className="label">Choose visible sections and their order, then save this view for quick access.</p></div>
            <button className="btn btn-secondary" type="button" onClick={() => { setLayoutEditorOpen(false); setLayoutDraft(null); }}>Close</button>
          </div>
          <div className="dashboard-layout-editor-list">
            {layoutDraft.order.map((section, index) => (
              <div className={`dashboard-layout-editor-row ${layoutDraft.visible.includes(section) ? '' : 'is-hidden'}`} key={section}>
                <label><input aria-label={`Show ${SECTION_LABELS[section]}`} type="checkbox" checked={layoutDraft.visible.includes(section)} onChange={() => toggleLayoutSection(section)} /> {SECTION_LABELS[section]}</label>
                <div className="dashboard-layout-editor-actions">
                  <button className="btn btn-secondary btn-small" type="button" disabled={index === 0} onClick={() => moveLayoutSection(section, -1)} aria-label={`Move ${SECTION_LABELS[section]} up`}>↑</button>
                  <button className="btn btn-secondary btn-small" type="button" disabled={index === layoutDraft.order.length - 1} onClick={() => moveLayoutSection(section, 1)} aria-label={`Move ${SECTION_LABELS[section]} down`}>↓</button>
                </div>
              </div>
            ))}
          </div>
          <div className="dashboard-layout-save-row">
            <input aria-label="Saved layout name" value={layoutName} onChange={event => setLayoutName(event.target.value)} placeholder="e.g. My morning scan" maxLength={50} />
            <button className="btn btn-primary" type="button" onClick={saveLayout} disabled={!layoutName.trim()}>Save layout</button>
            {!activeLayout.builtIn && <button className="btn btn-danger" type="button" onClick={deleteSelectedLayout}>Delete saved layout</button>}
          </div>
        </div>
      )}

      <div className="dashboard-grid">
        {activeLayout.order.filter(section => activeLayout.visible.includes(section)).map(section => (
          <React.Fragment key={section}>{renderDashboardSection(section)}</React.Fragment>
        ))}
      </div>
    </div>
  );
}
