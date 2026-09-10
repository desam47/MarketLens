/**
 * AIHubPage — the hub for every AI capability in one place.
 *
 * Layout (from the ai-advisor-page-design workflow): a single vertical
 * scroll of always-mounted sections with a sticky section-jump nav.
 *   - Symbol-scoped: AI Analysis (incl. the Trade Setup / advisor
 *     block), Chat, Templates — driven by the page's symbol picker.
 *   - Market-wide: the premarket/close AI Digest and AI Stock Search
 *     (picking a result repoints the symbol-scoped sections in place,
 *     without leaving the page).
 *
 * AIAnalysisPanel renders FIRST and stays mounted: AITemplatesPanel's
 * "⏱ Background" button reaches out of React to
 * document.getElementById('ai-analysis-panel').runBackground(), so the
 * Analysis section must precede the Templates section (same implicit
 * contract SymbolPage had before these panels moved here).
 */
import React, { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { PageErrorBoundary } from '../components/PageErrorBoundary';
import { SymbolInput } from '../components/SymbolInput';
import { DigestCard } from '../components/DigestCard';
import { NLSearchBar } from '../components/NLSearchBar';
import { TIMEFRAMES, TIMEFRAME_LABELS } from '../utils/timeframeUtils';

// Heavy, self-fetching panels — same lazy pattern + same chunks
// SymbolPage used for these.
const AIAnalysisPanel = lazy(() =>
  import('../components/AIAnalysisPanel').then(m => ({ default: m.AIAnalysisPanel })),
);
const ChatPanel = lazy(() =>
  import('../components/ChatPanel').then(m => ({ default: m.ChatPanel })),
);
const AITemplatesPanel = lazy(() =>
  import('../components/AITemplatesPanel').then(m => ({ default: m.AITemplatesPanel })),
);

interface AIHubPageProps {
  symbol: string;
  onSymbolChange: (symbol: string) => void;
}

const SECTIONS = [
  { id: 'analysis', label: 'Analysis' },
  { id: 'chat', label: 'Chat' },
  { id: 'templates', label: 'Templates' },
  { id: 'digest', label: 'Digest' },
  { id: 'search', label: 'Search' },
] as const;
type SectionId = typeof SECTIONS[number]['id'];

export function AIHubPage({ symbol, onSymbolChange }: AIHubPageProps) {
  const [timeframe, setTimeframe] = useState('1d');
  const [templatesReloadKey, setTemplatesReloadKey] = useState(0);
  const [activeSection, setActiveSection] = useState<SectionId>('analysis');

  // The header ↻ and the SymbolInput submit re-key AITemplatesPanel only.
  // Re-mounting AIAnalysisPanel would orphan an in-flight billable
  // background job and re-fire analyzeSymbol; re-mounting ChatPanel would
  // tear down the open session + transcript. Both of those already refetch
  // from their own symbol/timeframe effects and have their own refresh
  // controls ("Analyze" / "Clear").
  const handleRefresh = useCallback(() => setTemplatesReloadKey(k => k + 1), []);

  const jumpTo = (id: SectionId) =>
    document.getElementById(`hub-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Scrollspy — highlight the jump-nav chip for the topmost visible
  // section. Every <section> is always mounted (only the lazy panel
  // inside is Suspense-gated), so the id nodes exist on first paint.
  useEffect(() => {
    const obs = new IntersectionObserver(
      entries => {
        const top = entries
          .filter(e => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) {
          setActiveSection(top.target.id.replace('hub-', '') as SectionId);
        }
      },
      { rootMargin: '-70px 0px -55% 0px', threshold: 0 },
    );
    SECTIONS.forEach(s => {
      const el = document.getElementById(`hub-${s.id}`);
      if (el) obs.observe(el);
    });
    return () => obs.disconnect();
  }, []);

  return (
    <div className="ai-hub-page">
      <div className="dashboard-header">
        <div>
          <h1>AI Hub</h1>
          <p className="subtitle">
            Analysis, chat &amp; templates for <b>{symbol}</b> &middot; market digest &amp; AI search
          </p>
        </div>
        <div className="header-actions">
          <select
            className="timeframe-select"
            value={timeframe}
            onChange={e => setTimeframe(e.target.value)}
            aria-label="Timeframe — applies to Analysis and Templates"
          >
            {TIMEFRAMES.map(tf => (
              <option key={tf} value={tf}>{TIMEFRAME_LABELS[tf] || tf}</option>
            ))}
          </select>
          <SymbolInput
            key={symbol}
            symbol={symbol}
            onChange={onSymbolChange}
            onSubmit={handleRefresh}
          />
          <button className="btn btn-secondary" type="button" onClick={handleRefresh}>
            ↻ Refresh
          </button>
        </div>
      </div>

      <nav className="ai-hub-nav" aria-label="Jump to section">
        {SECTIONS.map(s => (
          <button
            key={s.id}
            type="button"
            className={activeSection === s.id ? 'active' : ''}
            onClick={() => jumpTo(s.id)}
          >
            {s.label}
          </button>
        ))}
      </nav>

      <section id="hub-analysis" className="ai-hub-section">
        <PageErrorBoundary pageName="AI Analysis">
          <Suspense fallback={<div className="panel-skeleton">Loading AI analysis…</div>}>
            <AIAnalysisPanel symbol={symbol} timeframe={timeframe} />
          </Suspense>
        </PageErrorBoundary>
      </section>

      <section id="hub-chat" className="ai-hub-section">
        <PageErrorBoundary pageName="AI Chat">
          <Suspense fallback={<div className="panel-skeleton">Loading chat…</div>}>
            <ChatPanel symbol={symbol} />
          </Suspense>
        </PageErrorBoundary>
      </section>

      <section id="hub-templates" className="ai-hub-section">
        <PageErrorBoundary pageName="AI Templates">
          <Suspense fallback={<div className="panel-skeleton">Loading AI templates…</div>}>
            <AITemplatesPanel key={templatesReloadKey} symbol={symbol} timeframe={timeframe} />
          </Suspense>
        </PageErrorBoundary>
      </section>

      <h4 className="ai-hub-divider">Market-wide AI</h4>

      <section id="hub-digest" className="ai-hub-section">
        <PageErrorBoundary pageName="AI Digest">
          <DigestCard />
        </PageErrorBoundary>
      </section>

      <section id="hub-search" className="ai-hub-section">
        <PageErrorBoundary pageName="AI Search">
          <NLSearchBar onSelectSymbol={onSymbolChange} />
        </PageErrorBoundary>
      </section>
    </div>
  );
}

export default AIHubPage;
