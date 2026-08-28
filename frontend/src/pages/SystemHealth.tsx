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
  const [toggling, setToggling] = useState(false);

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

  const handleToggle = async () => {
    if (toggling || !ingestionStatus) return;
    const targetState = !ingestionStatus.is_running;
    setToggling(true);
    try {
      const result = await api.toggleIngestion();
      // Optimistic update: apply the response immediately so the checkbox
      // doesn't desync from the backend. The background-thread cleanup on
      // stop() can take a few seconds to fully unwind, so we keep `toggling`
      // set for a short window to absorb rapid re-clicks.
      setIngestionStatus((prev: any) => prev ? { ...prev, is_running: result.is_running } : prev);
      // Re-fetch the full status once the backend settles. Stopping a daemon
      // thread can take up to ~5s (join timeout in ingestion_service.stop),
      // so we wait long enough for is_running to reflect the final state.
      setTimeout(() => fetchData(), targetState ? 1500 : 5500);
    } catch (err: any) {
      setError(`Toggle failed: ${err.message}`);
    } finally {
      // Hold the disabled state for ~1s after the API call returns so a
      // rapid second click doesn't queue a duplicate request.
      setTimeout(() => setToggling(false), 1000);
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
              <div className="toggle-row">
                <label className="toggle-switch">
                  <input
                    type="checkbox"
                    checked={!!ingestionStatus.is_running}
                    disabled={toggling}
                    onChange={handleToggle}
                  />
                  <span className="toggle-slider" />
                </label>
                <span className="toggle-label">
                  {toggling
                    ? 'Switching…'
                    : ingestionStatus.is_running
                    ? 'Ingestion is ON — click to stop'
                    : 'Ingestion is OFF — click to start'}
                </span>
              </div>
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
