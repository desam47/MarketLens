import React, { useState } from 'react';
import { Dashboard } from './pages/Dashboard';
import { WatchlistPage } from './pages/WatchlistPage';
import { SystemHealth } from './pages/SystemHealth';
import { AlertsPage } from './pages/AlertsPage';
import { BacktestPage } from './pages/BacktestPage';
import './styles/App.css';

type Page = 'dashboard' | 'watchlist' | 'health' | 'alerts' | 'backtest';

export default function App() {
  const [currentPage, setCurrentPage] = useState<Page>('dashboard');
  const [symbol, setSymbol] = useState<string>('AAPL');

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return <Dashboard symbol={symbol} onSymbolChange={setSymbol} />;
      case 'watchlist':
        return <WatchlistPage onSelectSymbol={(s) => { setSymbol(s); setCurrentPage('dashboard'); }} />;
      case 'alerts':
        return <AlertsPage />;
      case 'backtest':
        return <BacktestPage />;
      case 'health':
        return <SystemHealth />;
      default:
        return <Dashboard symbol={symbol} onSymbolChange={setSymbol} />;
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
              className={currentPage === 'watchlist' ? 'active' : ''}
              onClick={() => setCurrentPage('watchlist')}
            >
              <span className="nav-icon">📋</span>
              Watchlist
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
