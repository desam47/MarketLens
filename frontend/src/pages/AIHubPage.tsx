/**
 * AIHubPage — the hub for every AI capability in one place.
 *
 * The Hub carries its OWN ticker (App's `hubSymbol`, separate from the
 * app-wide `symbol` that drives the Dashboard / Symbol page). Changing
 * the ticker here — via the picker or an AI Search result — does not
 * move the rest of the app, and vice versa.
 *
 * Layout (from the ai-advisor-page-design workflow): a single vertical
 * scroll of section anchors with a sticky section-jump nav.
 *   - Symbol-scoped: Chat, then AI Analysis (incl. the Trade Setup /
 *     advisor block), then Templates — driven by the page's own ticker.
 *   - Market-wide: the premarket/close AI Digest and AI Stock Search
 *     (picking a result repoints the symbol-scoped sections in place,
 *     without leaving the page).
 *
 * Load-on-reveal: only Chat mounts on open. Every other section's heavy
 * panel (and its API calls) stays dormant behind a placeholder until it
 * scrolls near the viewport or you jump to it — then it mounts and stays
 * mounted, so state is kept. This keeps the first paint to a single
 * chat-session request instead of a five-panel burst (auto-analyze +
 * chat + templates + digest + search all at once).
 *
 * The Analysis section must stay ABOVE Templates: AITemplatesPanel's
 * "⏱ Background" button reaches out of React to
 * document.getElementById('ai-analysis-panel').runBackground(), so it
 * must be mounted first. Revealing Templates therefore also reveals
 * Analysis (same implicit contract SymbolPage had before these panels
 * moved here). Chat above Analysis is fine.
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
  { id: 'chat', label: 'Chat' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'templates', label: 'Templates' },
  { id: 'digest', label: 'Digest' },
  { id: 'search', label: 'Search' },
] as const;
type SectionId = typeof SECTIONS[number]['id'];

export function AIHubPage({ symbol, onSymbolChange }: AIHubPageProps) {
  const [timeframe, setTimeframe] = useState('1d');
  const [templatesReloadKey, setTemplatesReloadKey] = useState(0);
  const [activeSection, setActiveSection] = useState<SectionId>('chat');
  // Which sections have been mounted. Chat mounts on open; the rest are
  // added as they scroll into view or get jumped to, and never removed.
  const [revealed, setRevealed] = useState<Set<SectionId>>(() => new Set<SectionId>(['chat']));

  // The header ↻ and the SymbolInput submit re-key AITemplatesPanel only.
  // Re-mounting AIAnalysisPanel would orphan an in-flight billable
  // background job and re-fire analyzeSymbol; re-mounting ChatPanel would
  // tear down the open session + transcript. Both of those already refetch
  // from their own symbol/timeframe effects and have their own refresh
  // controls ("Analyze" / "Clear").
  const handleRefresh = useCallback(() => setTemplatesReloadKey(k => k + 1), []);

  // Mount a section (idempotent). Templates drags in Analysis so the
  // AITemplatesPanel → getElementById('ai-analysis-panel').runBackground()
  // bridge always has its target mounted.
  const reveal = useCallback((id: SectionId) => {
    setRevealed(prev => {
      const needsAnalysis = id === 'templates' && !prev.has('analysis');
      if (prev.has(id) && !needsAnalysis) return prev;
      const next = new Set(prev);
      next.add(id);
      if (id === 'templates') next.add('analysis');
      return next;
    });
  }, []);

  const jumpTo = (id: SectionId) => {
    reveal(id);
    // The <section> anchor is always in the DOM (only the panel inside is
    // gated), so the scroll target exists whether or not it's revealed yet.
    document.getElementById(`hub-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Two observers over the always-mounted <section> anchors:
  //   • scrollspy — highlight the jump-nav chip for the topmost visible section.
  //   • reveal — mount a section's panel ~200px before it enters the
  //     viewport, then stop watching it.
  useEffect(() => {
    const spy = new IntersectionObserver(
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
    const revealObs = new IntersectionObserver(
      entries => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          revealObs.unobserve(e.target);
          reveal(e.target.id.replace('hub-', '') as SectionId);
        }
      },
      { rootMargin: '200px 0px 200px 0px', threshold: 0 },
    );
    SECTIONS.forEach(s => {
      const el = document.getElementById(`hub-${s.id}`);
      if (!el) return;
      spy.observe(el);
      if (s.id !== 'chat') revealObs.observe(el);
    });
    return () => {
      spy.disconnect();
      revealObs.disconnect();
    };
  }, [reveal]);

  return (
    <div className="ai-hub-page">
      <div className="dashboard-header">
        <div>
          <h1>AI Hub</h1>
          <p className="subtitle">
            Chat, analysis &amp; templates for <b>{symbol}</b> (independent of the rest of the app)
            &middot; market digest &amp; AI search
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

      <section id="hub-chat" className="ai-hub-section">
        {revealed.has('chat') ? (
          <PageErrorBoundary pageName="AI Chat">
            <Suspense fallback={<div className="panel-skeleton">Loading chat…</div>}>
              <ChatPanel symbol={symbol} />
            </Suspense>
          </PageErrorBoundary>
        ) : (
          <div className="panel-skeleton" style={{ minHeight: 240 }}>Chat</div>
        )}
      </section>

      {/* Analysis stays ABOVE Templates: AITemplatesPanel's "⏱ Background"
          button calls document.getElementById('ai-analysis-panel').runBackground()
          out of React, so the Analysis section must render before it. */}
      <section id="hub-analysis" className="ai-hub-section">
        {revealed.has('analysis') ? (
          <PageErrorBoundary pageName="AI Analysis">
            <Suspense fallback={<div className="panel-skeleton">Loading AI analysis…</div>}>
              <AIAnalysisPanel symbol={symbol} timeframe={timeframe} />
            </Suspense>
          </PageErrorBoundary>
        ) : (
          <div className="panel-skeleton" style={{ minHeight: 240 }}>AI analysis</div>
        )}
      </section>

      <section id="hub-templates" className="ai-hub-section">
        {revealed.has('templates') ? (
          <PageErrorBoundary pageName="AI Templates">
            <Suspense fallback={<div className="panel-skeleton">Loading AI templates…</div>}>
              <AITemplatesPanel key={templatesReloadKey} symbol={symbol} timeframe={timeframe} />
            </Suspense>
          </PageErrorBoundary>
        ) : (
          <div className="panel-skeleton" style={{ minHeight: 240 }}>AI templates</div>
        )}
      </section>

      <h4 className="ai-hub-divider">Market-wide AI</h4>

      <section id="hub-digest" className="ai-hub-section">
        {revealed.has('digest') ? (
          <PageErrorBoundary pageName="AI Digest">
            <DigestCard />
          </PageErrorBoundary>
        ) : (
          <div className="panel-skeleton" style={{ minHeight: 240 }}>Market digest</div>
        )}
      </section>

      <section id="hub-search" className="ai-hub-section">
        {revealed.has('search') ? (
          <PageErrorBoundary pageName="AI Search">
            <NLSearchBar onSelectSymbol={onSymbolChange} />
          </PageErrorBoundary>
        ) : (
          <div className="panel-skeleton" style={{ minHeight: 240 }}>AI search</div>
        )}
      </section>
    </div>
  );
}

export default AIHubPage;
