import React, { useState, useEffect, useCallback, memo } from 'react';
import api, { HealthData, IngestionStatus, SystemStatus, SystemConfig, BackupStatusData } from '../services/api';
import { SkeletonBlock } from '../components/SkeletonBlock';
import { ErrorBanner } from '../components/ErrorBanner';

/** Human-readable byte formatter for WAL/SHM file sizes. */
function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

// ---------------------------------------------------------------------------
// Per-card components — memoized so unrelated state changes don't re-render them
// ---------------------------------------------------------------------------

const ApiServiceCard = memo(function ApiServiceCard({
  health,
  loading,
  error,
}: {
  health: HealthData | null;
  loading: boolean;
  error: string | null;
}) {
  return (
    <div className={`health-card${loading && !health ? ' card-loading-skeleton' : ''}`}>
      <h2>API Service</h2>
      {loading && !health ? (
        <>
          <SkeletonBlock width="40%" height="1.4rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="80%" height="0.8rem" />
        </>
      ) : error ? (
        <p className="error-text">⚠ {error}</p>
      ) : health ? (
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
  );
});

const SystemConfigCard = memo(function SystemConfigCard({
  systemConfig,
  systemStatus,
  loading,
  error,
}: {
  systemConfig: SystemConfig | null;
  systemStatus: SystemStatus | null;
  loading: boolean;
  error: string | null;
}) {
  return (
    <div className={`health-card${loading && !systemConfig && !systemStatus ? ' card-loading-skeleton' : ''}`}>
      <h2>System Configuration</h2>
      {loading && !systemConfig && !systemStatus ? (
        <>
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="70%" height="0.8rem" />
        </>
      ) : error ? (
        <p className="error-text">⚠ {error}</p>
      ) : systemConfig ? (
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
  );
});

const IngestionCard = memo(function IngestionCard({
  ingestionStatus,
  loading,
  error,
  toggling,
  onToggle,
}: {
  ingestionStatus: IngestionStatus | null;
  loading: boolean;
  error: string | null;
  toggling: boolean;
  onToggle: () => void;
}) {
  return (
    <div className={`health-card${loading && !ingestionStatus ? ' card-loading-skeleton' : ''}`}>
      <h2>Data Ingestion</h2>
      {loading && !ingestionStatus ? (
        <>
          <SkeletonBlock width="50%" height="1.4rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="80%" height="2rem" />
        </>
      ) : error ? (
        <p className="error-text">⚠ {error}</p>
      ) : ingestionStatus ? (
        <div className="health-details">
          <p>
            <strong>Status:</strong>
            <span className={`status-badge ${ingestionStatus.is_running ? 'status-ok' : 'status-warning'}`}>
              {ingestionStatus.is_running ? 'Running' : 'Stopped'}
            </span>
          </p>
          <p><strong>Tracking:</strong> {ingestionStatus.watchlists?.join(', ') || 'None'}</p>
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
                onChange={onToggle}
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
  );
});

const BackupCard = memo(function BackupCard({
  backupStatus,
  loading,
  error,
}: {
  backupStatus: BackupStatusData | null;
  loading: boolean;
  error: string | null;
}) {
  return (
    <div className={`health-card${loading && !backupStatus ? ' card-loading-skeleton' : ''}`}>
      <h2>Database Backup &amp; WAL</h2>
      {loading && !backupStatus ? (
        <>
          <SkeletonBlock width="60%" height="1.4rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="80%" height="0.8rem" />
        </>
      ) : error ? (
        <p className="error-text">⚠ {error}</p>
      ) : backupStatus ? (
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
  );
});

const ConnectionTestCard = memo(function ConnectionTestCard({
  health,
  systemStatus,
  ingestionStatus,
  backupStatus,
  loading,
}: {
  health: HealthData | null;
  systemStatus: SystemStatus | null;
  ingestionStatus: IngestionStatus | null;
  backupStatus: BackupStatusData | null;
  loading: boolean;
}) {
  return (
    <div className={`health-card${loading ? ' card-loading-skeleton' : ''}`}>
      <h2>Connection Test</h2>
      {loading ? (
        <>
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
        </>
      ) : (
        <div className="connection-status">
          <div className="test-results">
            <div className="test-row">
              <span>Health Endpoint:</span>
              <span className={health ? 'status-ok' : 'status-error'}>{health ? '✓' : '✗'}</span>
            </div>
            <div className="test-row">
              <span>System Status:</span>
              <span className={systemStatus ? 'status-ok' : 'status-error'}>{systemStatus ? '✓' : '✗'}</span>
            </div>
            <div className="test-row">
              <span>Data Ingestion:</span>
              <span className={ingestionStatus ? 'status-ok' : 'status-error'}>{ingestionStatus ? '✓' : '✗'}</span>
            </div>
            <div className="test-row">
              <span>Backup Status:</span>
              <span className={backupStatus ? 'status-ok' : 'status-error'}>{backupStatus ? '✓' : '✗'}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export function SystemHealth() {
  // Per-card state pairs
  const [health, setHealth] = useState<HealthData | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [healthError, setHealthError] = useState<string | null>(null);

  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [systemConfig, setSystemConfig] = useState<SystemConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(true);
  const [configError, setConfigError] = useState<string | null>(null);

  const [ingestionStatus, setIngestionStatus] = useState<IngestionStatus | null>(null);
  const [ingestionLoading, setIngestionLoading] = useState(true);
  const [ingestionError, setIngestionError] = useState<string | null>(null);

  const [backupStatus, setBackupStatus] = useState<BackupStatusData | null>(null);
  const [backupLoading, setBackupLoading] = useState(true);
  const [backupError, setBackupError] = useState<string | null>(null);

  const [globalError, setGlobalError] = useState<string | null>(null);
  const [toggling, setToggling] = useState(false);
  const [restarting, setRestarting] = useState(false);

  // Independent fetch callbacks
  const fetchHealth = useCallback(async () => {
    setHealthLoading(true);
    setHealthError(null);
    try {
      const data = await api.getHealth();
      setHealth(data);
    } catch (err: any) {
      setHealthError(err?.message || 'Failed to load health');
    } finally {
      setHealthLoading(false);
    }
  }, []);

  const fetchConfig = useCallback(async () => {
    setConfigLoading(true);
    setConfigError(null);
    try {
      const [configData, statusData] = await Promise.all([
        api.getSystemConfig().catch(() => null),
        api.getSystemStatus().catch(() => null),
      ]);
      setSystemConfig(configData);
      setSystemStatus(statusData);
    } catch (err: any) {
      setConfigError(err?.message || 'Failed to load config');
    } finally {
      setConfigLoading(false);
    }
  }, []);

  const fetchIngestion = useCallback(async () => {
    setIngestionLoading(true);
    setIngestionError(null);
    try {
      const data = await api.getIngestionStatus();
      setIngestionStatus(data);
    } catch (err: any) {
      setIngestionError(err?.message || 'Failed to load ingestion status');
    } finally {
      setIngestionLoading(false);
    }
  }, []);

  const fetchBackup = useCallback(async () => {
    setBackupLoading(true);
    setBackupError(null);
    try {
      const data = await api.getBackupStatus();
      setBackupStatus(data);
    } catch (err: any) {
      setBackupError(err?.message || 'Failed to load backup status');
    } finally {
      setBackupLoading(false);
    }
  }, []);

  const fetchAll = useCallback(() => {
    fetchHealth();
    fetchConfig();
    fetchIngestion();
    fetchBackup();
  }, [fetchHealth, fetchConfig, fetchIngestion, fetchBackup]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleToggle = useCallback(async () => {
    if (toggling || !ingestionStatus) return;
    setToggling(true);
    try {
      const result = await api.toggleIngestion();
      // Trust the toggle response as the source of truth — no re-fetch needed.
      setIngestionStatus((prev: any) =>
        prev ? { ...prev, is_running: result.is_running } : prev
      );
    } catch (err: any) {
      setGlobalError(`Toggle failed: ${err.message}`);
    } finally {
      setToggling(false);
    }
  }, [toggling, ingestionStatus]);

  const handleRestart = useCallback(async () => {
    if (restarting) return;
    if (!window.confirm('Restart the backend and frontend now? The page will be unresponsive for a few seconds.')) {
      return;
    }
    setRestarting(true);
    setGlobalError(null);
    try {
      // The backend process serving this request kills itself a moment
      // after responding — a successful response here just confirms the
      // restart was triggered, not that it finished. Reload after a
      // fixed delay long enough for uvicorn + craco to come back up
      // rather than polling a server we just told to go away.
      await api.restartServices();
      setTimeout(() => window.location.reload(), 8000);
    } catch (err: any) {
      // The trigger request itself normally succeeds (the backend
      // sleeps briefly before killing anything — see restart_dev.sh) —
      // but reload regardless of whether this particular fetch errored,
      // since a network blip here doesn't mean the restart wasn't
      // triggered.
      setTimeout(() => window.location.reload(), 8000);
    }
  }, [restarting]);

  const anyLoading = healthLoading || configLoading || ingestionLoading || backupLoading;

  return (
    <div className="system-health">
      <div className="health-header">
        <h1>System Health</h1>
        <div className="health-header-actions">
          <button className="btn" onClick={fetchAll} disabled={anyLoading || restarting}>
            {anyLoading ? '⟳ Refreshing…' : '↻ Refresh'}
          </button>
          <button
            className="btn btn-danger"
            onClick={handleRestart}
            disabled={restarting}
            title="Restart the backend and frontend dev servers"
          >
            {restarting ? '⟳ Restarting…' : '⟲ Restart'}
          </button>
        </div>
      </div>

      {restarting && (
        <p className="info-text">
          Restarting backend and frontend — this page will reload automatically in a few seconds…
        </p>
      )}

      {globalError && <ErrorBanner message={globalError} onDismiss={() => setGlobalError(null)} />}

      <div className="health-grid">
        <ApiServiceCard health={health} loading={healthLoading} error={healthError} />

        <SystemConfigCard
          systemConfig={systemConfig}
          systemStatus={systemStatus}
          loading={configLoading}
          error={configError}
        />

        <IngestionCard
          ingestionStatus={ingestionStatus}
          loading={ingestionLoading}
          error={ingestionError}
          toggling={toggling}
          onToggle={handleToggle}
        />

        <BackupCard backupStatus={backupStatus} loading={backupLoading} error={backupError} />

        <ConnectionTestCard
          health={health}
          systemStatus={systemStatus}
          ingestionStatus={ingestionStatus}
          backupStatus={backupStatus}
          loading={anyLoading}
        />
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
