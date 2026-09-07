import React, { useState, useEffect, lazy, Suspense } from 'react';
import api from './services/api';
import { Dashboard } from './pages/Dashboard';
import { PageErrorBoundary } from './components/PageErrorBoundary';
import './styles/App.css';

// Phase 3.6.6: code-split all non-dashboard pages.
// Dashboard stays in the main bundle — it's the landing page and must be
// available immediately without a Suspense round-trip.
const WatchlistPage = lazy(() => import('./pages/WatchlistPage').then(m => ({ default: m.WatchlistPage })));
const SystemHealth = lazy(() => import('./pages/SystemHealth').then(m => ({ default: m.SystemHealth })));
const AlertsPage = lazy(() => import('./pages/AlertsPage').then(m => ({ default: m.AlertsPage })));
const BacktestPage = lazy(() => import('./pages/BacktestPage').then(m => ({ default: m.BacktestPage })));
const SymbolPage = lazy(() => import('./pages/SymbolPage').then(m => ({ default: m.SymbolPage })));
const ScannerPage = lazy(() => import('./pages/ScannerPage').then(m => ({ default: m.ScannerPage })));
const HistoricalSignalsPage = lazy(() => import('./pages/HistoricalSignalsPage').then(m => ({ default: m.HistoricalSignalsPage })));

// Loading skeleton while the chunk downloads — keeps the layout stable.
const PageLoader = () => (
  <div style={{ padding: '2rem', color: '#888', fontFamily: 'monospace' }}>
    Loading…
  </div>
);

type Page = 'dashboard' | 'watchlist' | 'health' | 'alerts' | 'backtest' | 'symbol' | 'scanner' | 'signals';

export default function App() {
  const [currentPage, setCurrentPage] = useState<Page>('dashboard');
  // Default to the first symbol from the first populated watchlist.
  // Falls back to 'SPY' only if no watchlist has symbols (e.g. first run).
  const [symbol, setSymbol] = useState<string>('SPY');

  useEffect(() => {
    // Fetch the first populated watchlist and set its first symbol as default.
    // Sequential: try watchlists in order until one has symbols.
    let cancelled = false;
    api.getWatchlists().then(async (watchlists) => {
      for (const wl of watchlists) {
        if (cancelled) break;
        const symbols = await api.getWatchlistSymbols(wl.id);
        if (symbols.length > 0) {
          if (!cancelled) setSymbol(symbols[0].symbol);
          break;
        }
      }
    }).catch(() => {
      // Network error — stay on SPY so the dashboard still renders.
    });
    return () => { cancelled = true; };
  }, []);

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return <PageErrorBoundary pageName="Dashboard"><Dashboard symbol={symbol} onSymbolChange={setSymbol} /></PageErrorBoundary>;
      case 'watchlist':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary pageName="Watchlist"><WatchlistPage onSelectSymbol={(s) => { setSymbol(s); setCurrentPage('symbol'); }} /></PageErrorBoundary></Suspense>;
      case 'scanner':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary pageName="Live Scanner"><ScannerPage onSelectSymbol={(s) => { setSymbol(s); setCurrentPage('symbol'); }} /></PageErrorBoundary></Suspense>;
      case 'symbol':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary pageName="Symbol"><SymbolPage symbol={symbol} onSymbolChange={setSymbol} /></PageErrorBoundary></Suspense>;
      case 'alerts':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary pageName="Alerts"><AlertsPage /></PageErrorBoundary></Suspense>;
      case 'backtest':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary pageName="Backtest"><BacktestPage /></PageErrorBoundary></Suspense>;
      case 'health':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary pageName="System Health"><SystemHealth /></PageErrorBoundary></Suspense>;
      case 'signals':
        return <Suspense fallback={<PageLoader />}><PageErrorBoundary pageName="Historical Signals"><HistoricalSignalsPage /></PageErrorBoundary></Suspense>;
      default:
        return <PageErrorBoundary pageName="Dashboard"><Dashboard symbol={symbol} onSymbolChange={setSymbol} /></PageErrorBoundary>;
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
            <button
              className={currentPage === 'dashboard' ? 'active' : ''}
              onClick={() => setCurrentPage('dashboard')}
            >
              <span className="nav-icon">📈</span>
              Dashboard
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'symbol' ? 'active' : ''}
              onClick={() => setCurrentPage('symbol')}
            >
              <span className="nav-icon">🔬</span>
              Symbol
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'watchlist' ? 'active' : ''}
              onClick={() => setCurrentPage('watchlist')}
            >
              <span className="nav-icon">📋</span>
              Watchlist
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'scanner' ? 'active' : ''}
              onClick={() => setCurrentPage('scanner')}
            >
              <span className="nav-icon">🔴</span>
              Live Scanner
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'alerts' ? 'active' : ''}
              onClick={() => setCurrentPage('alerts')}
            >
              <span className="nav-icon">🔔</span>
              Alerts
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'backtest' ? 'active' : ''}
              onClick={() => setCurrentPage('backtest')}
            >
              <span className="nav-icon">⏪</span>
              Backtest
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'signals' ? 'active' : ''}
              onClick={() => setCurrentPage('signals')}
            >
              <span className="nav-icon">📜</span>
              Historical Signals
            </button>
          </li>
          <li>
            <button
              className={currentPage === 'health' ? 'active' : ''}
              onClick={() => setCurrentPage('health')}
            >
              <span className="nav-icon">💚</span>
              System Health
            </button>
          </li>
        </ul>
      </nav>
      <main className="main-content">
        {renderPage()}
      </main>
    </div>
  );
}
