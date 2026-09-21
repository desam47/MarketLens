import React from 'react';
import { AlertsCard } from '../components/AlertsCard';

export function AlertsPage() {
  return (
    <div className="page">
      <div className="dashboard-header">
        <div>
          <h1>Alerts</h1>
          <p className="subtitle">
            Price, scanner, technical, and provider-data alerts with in-app and external delivery options.
          </p>
        </div>
      </div>
      <AlertsCard />
    </div>
  );
}

export default AlertsPage;
