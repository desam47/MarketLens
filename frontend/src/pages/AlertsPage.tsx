import React, { useEffect, useState } from 'react';
import api, { SystemPerformance } from '../services/api';
import { AlertsCard } from '../components/AlertsCard';
import { MarketDataUpdateStatus } from '../components/MarketDataUpdateStatus';
import { useMarketSession } from '../hooks/useMarketSession';

export function AlertsPage() {
  const [performance, setPerformance] = useState<SystemPerformance | null>(null);
  const marketSession = useMarketSession();

  useEffect(() => {
    let active = true;
    api.getSystemPerformance().then(data => {
      if (active) setPerformance(data);
    }).catch(() => {
      if (active) setPerformance(null);
    });
    return () => { active = false; };
  }, []);

  const quoteEntitlement = performance?.provider_observability?.entitlements?.rest_quotes;
  return (
    <div className="page">
      <div className="dashboard-header">
        <div>
          <h1>Alerts</h1>
          <p className="subtitle">
            Price, scanner, technical, and provider-data alerts with in-app and external delivery options.
          </p>
          <MarketDataUpdateStatus
            timestamp={performance?.timestamp}
            provider={quoteEntitlement?.provider}
            dataStatus={performance ? 'LIVE' : 'ERROR'}
            marketSession={marketSession?.session}
          />
        </div>
      </div>
      <AlertsCard />
    </div>
  );
}

export default AlertsPage;
