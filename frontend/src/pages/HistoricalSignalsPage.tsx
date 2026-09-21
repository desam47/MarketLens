import React, { useEffect } from 'react';
import { HistoricalReplayPanel } from '../components/HistoricalReplayPanel';
import { HistoricalSignalCard } from '../components/HistoricalSignalCard';
import { SignalResearchDashboard } from '../components/SignalResearchDashboard';
import { SignalAlertCenter } from '../components/SignalAlertCenter';

export function HistoricalSignalsPage() {
  useEffect(() => {
    if (window.location.hash !== '#historical-replay') return;

    const frame = window.requestAnimationFrame(() => {
      document.getElementById('historical-replay')?.scrollIntoView({ block: 'start' });
    });

    return () => window.cancelAnimationFrame(frame);
  }, []);

  return (
    <div className="page">
      <div className="dashboard-header">
        <div>
          <h1>Historical Signals</h1>
          <p className="subtitle">
            Every recorded trend snapshot, with forward 5/10/20-bar returns
            and MFE/MAE computed only after the future data exists. Use this
            page to validate how the trend model has performed across
            different market regimes.
          </p>
        </div>
      </div>
      <SignalAlertCenter />
      <HistoricalReplayPanel />
      <SignalResearchDashboard />
      <HistoricalSignalCard />
    </div>
  );
}

export default HistoricalSignalsPage;
