import React, { useState, useEffect } from 'react';
import api, { HealthData, SystemStatus } from '../services/api';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';

export function SystemHealth() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [ingestionStatus, setIngestionStatus] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [healthData, statusData, ingestionData] = await Promise.all([
        api.getHealth().catch(() => null),
        api.getSystemStatus().catch(() => null),
        api.getIngestionStatus().catch(() => null),
      ]);
      setHealth(healthData);
      setSystemStatus(statusData);
      setIngestionStatus(ingestionData);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  if (loading) return <LoadingSpinner message="Checking system health..." />;

  return (
    <div className="system-health">
      <div className="health-header">
        <h1>System Health</h1>
        <button className="btn" onClick={fetchData}>↻ Refresh</button>
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <div className="health-grid">
        <div className="health-card">
          <h2>API Service</h2>
          {health ? (
            <div className="health-status">
              <span className={`status-badge ${health.status === 'healthy' ? 'status-ok' : 'status-error'}`}>
                {health.status.toUpperCase()}
              </span>
              <div className="health-details">
                <p><strong>Service:</strong> {health.service}</p>
                <p><strong>Version:</strong> {health.version}</p>
              </div>
            </div>
          ) : (
            <p className="error-text">API not responding</p>
          )}
        </div>

        <div className="health-card">
          <h2>System Configuration</h2>
          {systemStatus ? (
            <div className="health-details">
              <p><strong>Debug Mode:</strong> {systemStatus.debug ? 'Yes' : 'No'}</p>
              <p><strong>Market Data Provider:</strong> {systemStatus.market_data_provider}</p>
              <p><strong>AI Enabled:</strong> {systemStatus.ai_enabled ? 'Yes' : 'No'}</p>
              <p><strong>Version:</strong> {systemStatus.version}</p>
            </div>
          ) : (
            <p className="error-text">Status unavailable</p>
          )}
        </div>

        <div className="health-card">
          <h2>Data Ingestion</h2>
          {ingestionStatus ? (
            <div className="health-details">
              <p>
                <strong>Status:</strong>
                <span className={`status-badge ${ingestionStatus.is_running ? 'status-ok' : 'status-warning'}`}>
                  {ingestionStatus.is_running ? 'Running' : 'Stopped'}
                </span>
              </p>
              <p><strong>Tracked Symbols:</strong> {ingestionStatus.symbols?.join(', ') || 'None'}</p>
              <p><strong>Timeframes:</strong> {ingestionStatus.timeframes?.join(', ') || 'None'}</p>
              {ingestionStatus.last_quote_updates && (() => {
                const lastUpdate = Object.values(ingestionStatus.last_quote_updates)[0];
                return (
                  <p><strong>Last Quote Update:</strong> {String(lastUpdate || 'Never')}</p>
                );
              })()}
            </div>
          ) : (
            <p className="info-text">Ingestion service not available. Start it via API.</p>
          )}
        </div>

        <div className="health-card">
          <h2>Connection Test</h2>
          <div className="connection-status">
            <p>Testing API connectivity...</p>
            <div className="test-results">
              <div className="test-row">
                <span>Health Endpoint:</span>
                <span className={health ? '✓' : '✗'}>{health ? '✓' : '✗'}</span>
              </div>
              <div className="test-row">
                <span>System Status:</span>
                <span className={systemStatus ? '✓' : '✗'}>{systemStatus ? '✓' : '✗'}</span>
              </div>
              <div className="test-row">
                <span>Data Ingestion:</span>
                <span className={ingestionStatus ? '✓' : '✗'}>{ingestionStatus ? '✓' : '✗'}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="health-info">
        <h3>Quick Tips</h3>
        <ul>
          <li>Ensure the API is running on port 5001</li>
          <li>Check that market data providers are configured</li>
          <li>Monitor ingestion status for data freshness</li>
        </ul>
      </div>
    </div>
  );
}
