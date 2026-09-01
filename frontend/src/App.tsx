import React, { useState, useEffect } from 'react';
import api from './services/api';
import { Dashboard } from './pages/Dashboard';
import { WatchlistPage } from './pages/WatchlistPage';
import { SystemHealth } from './pages/SystemHealth';
import { AlertsPage } from './pages/AlertsPage';
import { BacktestPage } from './pages/BacktestPage';
import { SymbolPage } from './pages/SymbolPage';
import { ScannerPage } from './pages/ScannerPage';
import { HistoricalSignalsPage } from './pages/HistoricalSignalsPage';
import { PageErrorBoundary } from './components/PageErrorBoundary';
import './styles/App.css';

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
        return <PageErrorBoundary pageName="Watchlist"><WatchlistPage onSelectSymbol={(s) => { setSymbol(s); setCurrentPage('symbol'); }} /></PageErrorBoundary>;
      case 'scanner':
        return <PageErrorBoundary pageName="Live Scanner"><ScannerPage onSelectSymbol={(s) => { setSymbol(s); setCurrentPage('symbol'); }} /></PageErrorBoundary>;
      case 'symbol':
        return <PageErrorBoundary pageName="Symbol"><SymbolPage symbol={symbol} onSymbolChange={setSymbol} /></PageErrorBoundary>;
      case 'alerts':
        return <PageErrorBoundary pageName="Alerts"><AlertsPage /></PageErrorBoundary>;
      case 'backtest':
        return <PageErrorBoundary pageName="Backtest"><BacktestPage /></PageErrorBoundary>;
      case 'health':
        return <PageErrorBoundary pageName="System Health"><SystemHealth /></PageErrorBoundary>;
      case 'signals':
        return <PageErrorBoundary pageName="Historical Signals"><HistoricalSignalsPage /></PageErrorBoundary>;
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
