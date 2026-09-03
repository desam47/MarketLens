import React, { useState, useEffect } from 'react';
import api, { HealthData, SystemStatus, SystemConfig, BackupStatusData } from '../services/api';
import { SkeletonBlock } from '../components/SkeletonBlock';
import { ErrorBanner } from '../components/ErrorBanner';

/** Human-readable byte formatter for WAL/SHM file sizes. */
function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function SystemHealth() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [systemConfig, setSystemConfig] = useState<SystemConfig | null>(null);
  const [ingestionStatus, setIngestionStatus] = useState<any>(null);
  const [backupStatus, setBackupStatus] = useState<BackupStatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [healthData, statusData, configData, ingestionData, backupData] = await Promise.all([
        api.getHealth().catch(() => null),
        api.getSystemStatus().catch(() => null),
        api.getSystemConfig().catch(() => null),
        api.getIngestionStatus().catch(() => null),
        api.getBackupStatus().catch(() => null),
      ]);
      setHealth(healthData);
      setSystemStatus(statusData);
      setSystemConfig(configData);
      setIngestionStatus(ingestionData);
      setBackupStatus(backupData);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = async () => {
    if (toggling || !ingestionStatus) return;
    setToggling(true);
    try {
      const result = await api.toggleIngestion();
      // Trust the toggle response as the source of truth — no re-fetch needed.
      setIngestionStatus((prev: any) =>
        prev ? { ...prev, is_running: result.is_running } : prev
      );
    } catch (err: any) {
      setError(`Toggle failed: ${err.message}`);
    } finally {
      setToggling(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="system-health">
        <div className="health-header">
          <h1>System Health</h1>
          <SkeletonBlock width="120px" height="2.25rem" radius={6} />
        </div>
        <div className="health-grid">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="health-card">
              <SkeletonBlock width="50%" height="1.1rem" />
              <div className="skeleton-rows">
                {Array.from({ length: 3 }).map((_, j) => (
                  <SkeletonBlock key={j} width={j === 2 ? "60%" : "100%"} height="0.8rem" />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

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
          {systemConfig ? (
            <div className="health-details">
              <p>
                <strong>Market Data Provider:</strong>{' '}
                <span className="provider-name">{systemConfig.market_data_primary_provider}</span>
                <span className="config-source-badge" title="Read live from .env — no restart required">
                  LIVE
                </span>
              </p>
              {systemConfig.market_data_fallback_providers &&
                systemConfig.market_data_fallback_providers.length > 0 && (
                <p>
                  <strong>Fallback Provider(s):</strong>{' '}
                  <span className="provider-name">
                    {systemConfig.market_data_fallback_providers.join(', ')}
                  </span>
                </p>
              )}
              {systemStatus && (
                <>
                  <p><strong>Debug Mode:</strong> {systemStatus.debug ? 'Yes' : 'No'}</p>
                  <p><strong>AI Enabled:</strong> {systemStatus.ai_enabled ? 'Yes' : 'No'}</p>
                  <p><strong>Version:</strong> {systemStatus.version}</p>
                </>
              )}
            </div>
          ) : systemStatus ? (
            <div className="health-details">
              <p><strong>Market Data Provider:</strong> {systemStatus.market_data_provider}</p>
              {systemStatus.market_data_fallback_providers &&
                systemStatus.market_data_fallback_providers.length > 0 && (
                <p><strong>Fallback Provider(s):</strong> {systemStatus.market_data_fallback_providers.join(', ')}</p>
              )}
              <p><strong>Debug Mode:</strong> {systemStatus.debug ? 'Yes' : 'No'}</p>
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
          <h2>Database Backup &amp; WAL</h2>
          {backupStatus ? (
            <div className="health-details">
              <p>
                <strong>Journal Mode:</strong>{' '}
                <span className={`status-badge ${backupStatus.journal_mode === 'wal' ? 'status-ok' : 'status-warning'}`}>
                  {backupStatus.journal_mode.toUpperCase()}
                </span>
              </p>
              <p>
                <strong>WAL Checkpoint:</strong>{' '}
                <span className={`status-badge ${backupStatus.wal_checkpoint_busy ? 'status-warning' : 'status-ok'}`}>
                  {backupStatus.wal_checkpoint_busy ? 'Busy' : 'Idle'}
                </span>
                <span className="health-meta">
                  {' '}— {backupStatus.wal_checkpoint_frames} frames, end page {backupStatus.wal_checkpoint_end}
                </span>
              </p>
              <p>
                <strong>WAL File:</strong>{' '}
                {formatBytes(backupStatus.wal_size_bytes)}
              </p>
              <p>
                <strong>SHM File:</strong>{' '}
                {formatBytes(backupStatus.shm_size_bytes)}
              </p>
              <p>
                <strong>Litestream:</strong>{' '}
                <span className={`status-badge ${backupStatus.litestream_reachable ? 'status-ok' : 'status-error'}`}>
                  {backupStatus.litestream_reachable ? 'Streaming' : 'Not Reachable'}
                </span>
                {backupStatus.litestream_reachable && backupStatus.litestream_generation != null && (
                  <span className="health-meta">
                    {' '}— gen {backupStatus.litestream_generation}
                    {backupStatus.litestream_dbs ? ` (${backupStatus.litestream_dbs.length} db)` : ''}
                  </span>
                )}
              </p>
            </div>
          ) : (
            <p className="info-text">Backup status unavailable.</p>
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
              <div className="test-row">
                <span>Backup Status:</span>
                <span className={backupStatus ? '✓' : '✗'}>{backupStatus ? '✓' : '✗'}</span>
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
