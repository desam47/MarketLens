import React from 'react';
import { OptionsPanel } from '../components/OptionsPanel';
import type { NavigationState } from '../utils/appNavigation';

export function OptionsPage({ navigation }: { navigation?: NavigationState }) {
  const symbol = String(navigation?.symbol || 'SPY').toUpperCase();
  return (
    <section className="page options-page" aria-labelledby="options-page-title">
      <div className="page-header">
        <div>
          <h1 id="options-page-title">Options research</h1>
          <p className="info-text">Evidence-backed options chains for {symbol}.</p>
        </div>
      </div>
      <OptionsPanel symbol={symbol} />
    </section>
  );
}

export default OptionsPage;
