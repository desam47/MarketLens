import React from 'react';
import { AlertsCard } from '../components/AlertsCard';

export function AlertsPage() {
  return (
    <div className="page">
      <div className="dashboard-header">
        <div>
          <h1>Alerts</h1>
          <p className="subtitle">
            Price and signal alerts that fire during the next scan.
          </p>
        </div>
      </div>
      <AlertsCard />
    </div>
  );
}

export default AlertsPage;
