import React, { useState } from 'react';
import { Dashboard } from './pages/Dashboard';
import { WatchlistPage } from './pages/WatchlistPage';
import { SystemHealth } from './pages/SystemHealth';
import { AlertsPage } from './pages/AlertsPage';
import { BacktestPage } from './pages/BacktestPage';
import { SymbolPage } from './pages/SymbolPage';
import { ScannerPage } from './pages/ScannerPage';
import { HistoricalSignalsPage } from './pages/HistoricalSignalsPage';
import './styles/App.css';

type Page = 'dashboard' | 'watchlist' | 'health' | 'alerts' | 'backtest' | 'symbol' | 'scanner' | 'signals';

export default function App() {
  const [currentPage, setCurrentPage] = useState<Page>('dashboard');
  const [symbol, setSymbol] = useState<string>('SPY');

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return <Dashboard symbol={symbol} onSymbolChange={setSymbol} />;
      case 'watchlist':
        return <WatchlistPage onSelectSymbol={(s) => { setSymbol(s); setCurrentPage('symbol'); }} />;
      case 'scanner':
        return <ScannerPage onSelectSymbol={(s) => { setSymbol(s); setCurrentPage('symbol'); }} />;
      case 'symbol':
        return <SymbolPage symbol={symbol} onSymbolChange={setSymbol} />;
      case 'alerts':
        return <AlertsPage />;
      case 'backtest':
        return <BacktestPage />;
      case 'health':
        return <SystemHealth />;
      case 'signals':
        return <HistoricalSignalsPage />;
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
