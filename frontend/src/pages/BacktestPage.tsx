import React from 'react';
import { BacktestCard } from '../components/BacktestCard';

export function BacktestPage() {
  return (
    <div className="page">
      <div className="dashboard-header">
        <div>
          <h1>Backtest</h1>
          <p className="subtitle">
            Replay the scanner's bar-derived signals over historical daily bars
            and see the forward 1d / 5d / 20d return each one would have
            produced.
          </p>
        </div>
      </div>
      <BacktestCard />
    </div>
  );
}

export default BacktestPage;
