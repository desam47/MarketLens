import React, { useState, useEffect, lazy, Suspense, useCallback } from 'react';
import api from './services/api';
import { Dashboard } from './pages/Dashboard';
import { PageErrorBoundary } from './components/PageErrorBoundary';
import { StartupModeNotice } from './components/StartupModeNotice';
import { StartupModeContext, type StartupMode } from './contexts/StartupModeContext';
import { hashForPage, pageForHash, type AppPage } from './utils/appNavigation';
import './styles/App.css';

// Phase 3.6.6: code-split all non-dashboard pages.
// Dashboard stays in the main bundle — it's the landing page and must be
// available immediately without a Suspense round-trip.
const WatchlistPage = lazy(() => import('./pages/WatchlistPage').then(m => ({ default: m.WatchlistPage })));
const SystemHealth = lazy(() => import('./pages/SystemHealth').then(m => ({ default: m.SystemHealth })));
const AlertsPage = lazy(() => import('./pages/AlertsPage').then(m => ({ default: m.AlertsPage })));
const BacktestPage = lazy(() => import('./pages/BacktestPage').then(m => ({ default: m.BacktestPage })));
const SymbolPage = lazy(() => import('./pages/SymbolPage').then(m => ({ default: m.SymbolPage })));
const HistoricalSignalsPage = lazy(() => import('./pages/HistoricalSignalsPage').then(m => ({ default: m.HistoricalSignalsPage })));
const ScannerPage = lazy(() => import('./pages/ScannerPage').then(m => ({ default: m.ScannerPage })));
const AIHubPage = lazy(() => import('./pages/AIHubPage').then(m => ({ default: m.AIHubPage })));
const RiskDashboardPage = lazy(() => import('./pages/RiskDashboardPage').then(m => ({ default: m.RiskDashboardPage })));
const TradeJournalPage = lazy(() => import('./pages/TradeJournalPage').then(m => ({ default: m.TradeJournalPage })));
const CalendarPage = lazy(() => import('./pages/CalendarPage').then(m => ({ default: m.CalendarPage })));

// Loading skeleton while the chunk downloads — keeps the layout stable.
const PageLoader = () => (
  <div style={{ padding: '2rem', color: '#888', fontFamily: 'monospace' }}>
    Loading…
  </div>
);

export default function App() {
  const [currentPage, setCurrentPage] = useState<AppPage>(() => pageForHash(window.location.hash));
  // Default to the first symbol from the first populated watchlist.
  // Falls back to 'SPY' only if no watchlist has symbols (e.g. first run).
  const [symbol, setSymbol] = useState<string>('SPY');
  // The AI Hub carries its OWN ticker, independent of the Dashboard /
  // Symbol page — you go there to do AI work on whatever symbol you
  // want without disturbing (or being disturbed by) the rest of the app.
  // Lifted to App (not page-local) so it survives nav-tab switches.
  const [hubSymbol, setHubSymbol] = useState<string>('SPY');
  const [startupMode, setStartupMode] = useState<StartupMode>(null);

  useEffect(() => {
    // Fetch the first populated watchlist and set its first symbol as default.
    // Sequential: try watchlists in order until one has symbols.
    let cancelled = false;
    api.getWatchlists().then(async (watchlists) => {
      for (const wl of watchlists) {
        if (cancelled) break;
        const symbols = await api.getWatchlistSymbols(wl.id);
        if (symbols.length > 0) {
          if (!cancelled) {
            setSymbol(symbols[0].symbol);
            setHubSymbol(symbols[0].symbol); // seed only; diverges freely after
          }
          break;
        }
      }
    }).catch(() => {
      // Network error — stay on SPY so the dashboard still renders.
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const syncPageFromHash = () => {
      setCurrentPage(pageForHash(window.location.hash));
    };

    window.addEventListener('hashchange', syncPageFromHash);
    return () => window.removeEventListener('hashchange', syncPageFromHash);
  }, []);

  const navigateTo = useCallback((page: AppPage) => {
    const nextHash = hashForPage(page);
    setCurrentPage(page);

    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.getSystemStatus()
      .then(status => {
        if (!cancelled) setStartupMode(status.startup_mode);
      })
      .catch(() => {
        // The banner is supplementary; keep the application usable when
        // a backend is still starting or an older server omits this route.
      });
    return () => { cancelled = true; };
  }, []);

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return <PageErrorBoundary key={currentPage} pageName="Dashboard"><Dashboard symbol={symbol} onSymbolChange={setSymbol} /></PageErrorBoundary>;
      case 'watchlist':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="Watchlist"><WatchlistPage onSelectSymbol={(s) => { setSymbol(s); navigateTo('symbol'); }} /></PageErrorBoundary></Suspense>;
      case 'symbol':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="Symbol"><SymbolPage symbol={symbol} onSymbolChange={setSymbol} /></PageErrorBoundary></Suspense>;
      case 'hub':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="AI Hub"><AIHubPage symbol={hubSymbol} onSymbolChange={setHubSymbol} /></PageErrorBoundary></Suspense>;
      case 'alerts':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="Alerts"><AlertsPage /></PageErrorBoundary></Suspense>;
      case 'backtest':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="Backtest"><BacktestPage /></PageErrorBoundary></Suspense>;
      case 'health':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="System Health"><SystemHealth /></PageErrorBoundary></Suspense>;
      case 'signals':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="Historical Signals"><HistoricalSignalsPage /></PageErrorBoundary></Suspense>;
      case 'scanner':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="Scanner"><ScannerPage onSelectSymbol={(s) => { setSymbol(s); navigateTo('symbol'); }} /></PageErrorBoundary></Suspense>;
      case 'risk':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="Risk Dashboard"><RiskDashboardPage /></PageErrorBoundary></Suspense>;
      case 'journal':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="Trade Journal"><TradeJournalPage /></PageErrorBoundary></Suspense>;
      case 'calendar':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary key={currentPage} pageName="Earnings & Events"><CalendarPage /></PageErrorBoundary></Suspense>;
      default:
        return <PageErrorBoundary key="dashboard" pageName="Dashboard"><Dashboard symbol={symbol} onSymbolChange={setSymbol} /></PageErrorBoundary>;
    }
  };

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="logo">
          <span className="logo-icon">📊</span>
          <span className="logo-text">MarketLens</span>
        </div>
        <ul className="nav-links">
          <li>
            <button className={currentPage === 'calendar' ? 'active' : ''} onClick={() => navigateTo('calendar')}><span className="nav-icon">🗓️</span>Earnings &amp; Events</button>
          </li>
          <li>
            <button
              className={currentPage === 'scanner' ? 'active' : ''}
              onClick={() => navigateTo('scanner')}
            >
              <span className="nav-icon">🧭</span>
              Scanner
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'dashboard' ? 'active' : ''}
              onClick={() => navigateTo('dashboard')}
            >
              <span className="nav-icon">📈</span>
              Dashboard
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'risk' ? 'active' : ''}
              onClick={() => navigateTo('risk')}
            >
              <span className="nav-icon">🛡️</span>
              Risk Dashboard
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'journal' ? 'active' : ''}
              onClick={() => navigateTo('journal')}
            >
              <span className="nav-icon">📝</span>
              Trade Journal
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'symbol' ? 'active' : ''}
              onClick={() => navigateTo('symbol')}
            >
              <span className="nav-icon">🔬</span>
              Symbol
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'hub' ? 'active' : ''}
              onClick={() => navigateTo('hub')}
            >
              <span className="nav-icon">🤖</span>
              AI Hub
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'watchlist' ? 'active' : ''}
              onClick={() => navigateTo('watchlist')}
            >
              <span className="nav-icon">📋</span>
              Watchlist
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'alerts' ? 'active' : ''}
              onClick={() => navigateTo('alerts')}
            >
              <span className="nav-icon">🔔</span>
              Alerts
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'backtest' ? 'active' : ''}
              onClick={() => navigateTo('backtest')}
            >
              <span className="nav-icon">⏪</span>
              Backtest
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'signals' ? 'active' : ''}
              onClick={() => navigateTo('signals')}
            >
              <span className="nav-icon">📜</span>
              Historical Signals
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'health' ? 'active' : ''}
              onClick={() => navigateTo('health')}
            >
              <span className="nav-icon">💚</span>
              System Health
            </button>
          </li>
        </ul>
      </nav>
      <StartupModeContext.Provider value={startupMode}>
        <main className="main-content">
          <StartupModeNotice startupMode={startupMode} />
          {renderPage()}
        </main>
      </StartupModeContext.Provider>
    </div>
  );
}
