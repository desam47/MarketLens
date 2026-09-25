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

interface DashboardLayout {
  id: string;
  name: string;
  visible: DashboardSectionId[];
  order: DashboardSectionId[];
  builtIn?: boolean;
}

const DASHBOARD_LAYOUT_KEY = 'marketlens.dashboard.layout';
const DASHBOARD_CUSTOM_LAYOUTS_KEY = 'marketlens.dashboard.layouts';

// Presets order the market-wide sections by what each workflow reads first.
// The symbol-scoped sections these once also ordered (regime, strategy,
// trends, confluence, transitions) moved to the Symbol page, so a saved
// layout naming them is repaired rather than rejected — see readCustomLayouts.
const DASHBOARD_PRESETS: DashboardLayout[] = [
  {
    id: 'day_trading', name: 'Day trading', builtIn: true,
    visible: ALL_SECTIONS,
    order: ['movers', 'market_context', 'search', 'digest'],
  },
  {
    id: 'market_open', name: 'Market open', builtIn: true,
    visible: ALL_SECTIONS,
    order: ['market_context', 'movers', 'digest', 'search'],
  },
  {
    id: 'research', name: 'Research', builtIn: true,
    visible: ALL_SECTIONS,
    order: ['digest', 'market_context', 'search', 'movers'],
  },
];

function sanitizeSections(value: unknown): DashboardSectionId[] {
  if (!Array.isArray(value)) return [];
  return value.filter((section): section is DashboardSectionId =>
    ALL_SECTIONS.includes(section as DashboardSectionId),
  );
}

function readCustomLayouts(): DashboardLayout[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(DASHBOARD_CUSTOM_LAYOUTS_KEY) || '[]');
    if (!Array.isArray(value)) return [];
    return value
      .filter(layout => layout && typeof layout.id === 'string' && typeof layout.name === 'string')
      .map(layout => {
        // Drop only the sections that no longer exist, keeping the layout
        // itself. Rejecting the whole record would silently discard a saved
        // layout written when the Dashboard still had nine sections.
        const order = sanitizeSections(layout.order);
        const missing = ALL_SECTIONS.filter(section => !order.includes(section));
        return {
          id: layout.id,
          name: layout.name,
          visible: sanitizeSections(layout.visible),
          order: [...order, ...missing],
        };
      });
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

export function Dashboard({ symbol, onSymbolChange, onOpenChart }: DashboardProps) {
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [customLayouts, setCustomLayouts] = useState<DashboardLayout[]>(readCustomLayouts);
  const [selectedLayoutId, setSelectedLayoutId] = useState<string>(readSelectedLayout);
  const [layoutEditorOpen, setLayoutEditorOpen] = useState(false);
  const [layoutDraft, setLayoutDraft] = useState<DashboardLayout | null>(null);
  const [layoutName, setLayoutName] = useState('');

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

  const allLayouts = [...DASHBOARD_PRESETS, ...customLayouts];
  const activeLayout = allLayouts.find(layout => layout.id === selectedLayoutId) || DASHBOARD_PRESETS[0];

  useEffect(() => {
    try {
      window.localStorage.setItem(DASHBOARD_LAYOUT_KEY, selectedLayoutId);
    } catch {
      // Storage can be blocked (private windows); the layout just will not persist.
    }
  }, [selectedLayoutId]);

  useEffect(() => {
    try {
      window.localStorage.setItem(DASHBOARD_CUSTOM_LAYOUTS_KEY, JSON.stringify(customLayouts));
    } catch {
      // Same: a failed write must not break the dashboard.
    }
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
