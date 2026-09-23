import React from 'react';
import { BacktestCard } from '../components/BacktestCard';
import type { NavigationState } from '../utils/appNavigation';

export function BacktestPage({ navigation }: { navigation?: NavigationState }) {
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
      <BacktestCard defaultNavigation={navigation} />
    </div>
  );
}

export default BacktestPage;
