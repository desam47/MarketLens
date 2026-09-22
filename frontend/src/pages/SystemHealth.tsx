import React, { useState, useEffect, useCallback, memo } from 'react';
import api, {
  AuxiliaryProviderStatus,
  AuxiliaryProviderStatuses,
  BackupStatusData,
  HealthData,
  IngestionStatus,
  SystemConfig,
  SystemPerformance,
  SystemStatus,
} from '../services/api';
import { SkeletonBlock } from '../components/SkeletonBlock';
import { ErrorBanner } from '../components/ErrorBanner';

/** Human-readable byte formatter for WAL/SHM file sizes. */
function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatTimestamp(timestamp: string | null | undefined): string {
  if (!timestamp) return 'Not available';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return formatVersionStyleTimestamp(timestamp);
}

/** Match the compact version stamp convention while keeping all UI times ET. */
function formatVersionStyleTimestamp(timestamp: string | null | undefined): string {
  if (!timestamp) return 'Never';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return String(timestamp);
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value ?? '';
  return `${value('year')}-${value('month')}-${value('day')} ${value('hour')}:${value('minute')}:${value('second')}`;
}

function freshnessStatus(seconds: number | null | undefined): { label: string; className: string } {
  if (seconds == null || !Number.isFinite(seconds)) return { label: 'Unknown', className: 'status-warning' };
  if (seconds <= 60) return { label: 'Fresh', className: 'status-ok' };
  if (seconds <= 300) return { label: 'Delayed', className: 'status-warning' };
  return { label: 'Stale', className: 'status-error' };
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
              <p>
                <strong>Startup Mode:</strong>{' '}
                <span className={`status-badge ${systemStatus.startup_mode === 'api' ? 'status-warning' : 'status-ok'}`}>
                  {systemStatus.startup_mode.toUpperCase()}
                </span>
              </p>
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
          <p>
            <strong>Startup Mode:</strong>{' '}
            <span className={`status-badge ${systemStatus.startup_mode === 'api' ? 'status-warning' : 'status-ok'}`}>
              {systemStatus.startup_mode.toUpperCase()}
            </span>
          </p>
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
  apiMode,
  onToggle,
}: {
  ingestionStatus: IngestionStatus | null;
  loading: boolean;
  error: string | null;
  toggling: boolean;
  apiMode: boolean;
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
              <p><strong>Last Quote Update:</strong> {formatTimestamp(lastUpdate)}</p>
            );
          })()}
          <div className="toggle-row">
            <label className="toggle-switch">
              <input
                type="checkbox"
                checked={!!ingestionStatus.is_running}
                disabled={toggling || apiMode}
                onChange={onToggle}
              />
              <span className="toggle-slider" />
            </label>
            <span className="toggle-label">
              {apiMode
                ? 'API mode — live ingestion is disabled. Set STARTUP_MODE=full and restart.'
                : toggling
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

type LastUpdateRow = {
  symbol: string;
  dataType: string;
  timestamp: string;
  provider: string;
};

function updateAge(timestamp: string): { label: string; className: string } {
  const age = (Date.now() - new Date(timestamp).getTime()) / 1000;
  return freshnessStatus(Number.isFinite(age) ? Math.max(0, age) : null);
}

const LastSuccessfulUpdateCard = memo(function LastSuccessfulUpdateCard({
  ingestionStatus,
  loading,
}: {
  ingestionStatus: IngestionStatus | null;
  loading: boolean;
}) {
  const rows: LastUpdateRow[] = [];
  Object.entries(ingestionStatus?.last_quote_updates || {}).forEach(([symbol, timestamp]) => {
    if (timestamp) rows.push({ symbol, dataType: 'Quote', timestamp, provider: ingestionStatus?.last_quote_providers?.[symbol] || 'Unknown' });
  });
  Object.entries(ingestionStatus?.last_bar_updates || {}).forEach(([symbol, timeframes]) => {
    Object.entries(timeframes).forEach(([timeframe, timestamp]) => {
      if (timestamp) rows.push({ symbol, dataType: `${timeframe} bar`, timestamp, provider: ingestionStatus?.last_bar_providers?.[symbol]?.[timeframe] || 'Unknown' });
    });
  });
  Object.entries(ingestionStatus?.last_status_updates || {}).forEach(([symbol, timestamp]) => {
    if (timestamp) rows.push({ symbol, dataType: 'Market status', timestamp, provider: ingestionStatus?.last_status_providers?.[symbol] || 'Unknown' });
  });
  rows.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

  return (
    <div className={`health-card last-update-card${loading && !ingestionStatus ? ' card-loading-skeleton' : ''}`}>
      <h2>Last Successful Update</h2>
      {loading && !ingestionStatus ? (
        <><SkeletonBlock width="100%" height="0.8rem" /><SkeletonBlock width="90%" height="0.8rem" /><SkeletonBlock width="80%" height="0.8rem" /></>
      ) : rows.length === 0 ? (
        <p className="info-text">No successful market-data updates have been recorded yet.</p>
      ) : (
        <div className="last-update-list">
          <div className="last-update-row last-update-header"><span>Symbol</span><span>Data</span><span>Provider</span><span>Updated</span><span>Status</span></div>
          {rows.slice(0, 20).map(row => {
            const status = updateAge(row.timestamp);
            return <div className="last-update-row" key={`${row.symbol}-${row.dataType}`}>
              <strong>{row.symbol}</strong><span>{row.dataType}</span><span>{row.provider}</span><span title={formatTimestamp(row.timestamp)}>{formatTimestamp(row.timestamp)}</span><span className={`status-badge ${status.className}`}>{status.label}</span>
            </div>;
          })}
        </div>
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

const RuntimeDataCard = memo(function RuntimeDataCard({
  performance,
  loading,
  error,
}: {
  performance: SystemPerformance | null;
  loading: boolean;
  error: string | null;
}) {
  const cache = performance?.cache?.cache;
  const redis = performance?.cache?.redis;
  const freshness = freshnessStatus(performance?.ingestion.tf_update_latency_seconds);
  const redisStatus = redis?.status === 'running'
    ? { label: 'Running', className: 'status-ok' }
    : redis?.status === 'disabled'
      ? { label: 'Disabled', className: 'status-warning' }
      : redis?.status === 'unavailable'
        ? { label: 'Unavailable', className: 'status-error' }
        : { label: 'Unknown', className: 'status-warning' };

  return (
    <div className={`health-card${loading && !performance ? ' card-loading-skeleton' : ''}`}>
      <h2>Data Freshness &amp; Cache</h2>
      {loading && !performance ? (
        <>
          <SkeletonBlock width="90%" height="0.8rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="75%" height="0.8rem" />
        </>
      ) : error && !performance ? (
        <p className="error-text">⚠ {error}</p>
      ) : performance ? (
        <div className="health-details">
          <p>
            <strong>Pipeline:</strong>
            <span className={`status-badge ${freshness.className}`}>{freshness.label}</span>
          </p>
          <p><strong>Latest Bar:</strong> <span className="health-value">{formatTimestamp(performance.ingestion.last_bar_time)}</span></p>
          <p><strong>Pipeline Delay:</strong> <span className="health-value">{performance.ingestion.tf_update_latency_seconds == null ? 'Not available' : `${Math.round(performance.ingestion.tf_update_latency_seconds)}s`}</span></p>
          <p><strong>Redis:</strong> <span className={`status-badge ${redisStatus.className}`}>{redisStatus.label}</span>{redis?.error && <small className="health-meta"> — {redis.error}</small>}</p>
          {cache ? (
            <>
              <p><strong>Quote Cache Hit Rate:</strong> <span className="health-value">{cache.quote_hit_rate.toFixed(1)}%</span></p>
              <p><strong>Bar Cache Hit Rate:</strong> <span className="health-value">{cache.bar_hit_rate.toFixed(1)}%</span></p>
            </>
          ) : (
            <p><strong>Cache:</strong> <span className="health-value">No cache metrics reported</span></p>
          )}
        </div>
      ) : (
        <p className="info-text">Runtime metrics unavailable.</p>
      )}
    </div>
  );
});

function ProviderRow({ provider, category }: { provider: AuxiliaryProviderStatus | { name: string; is_healthy: boolean; lastError?: string | null; breaker?: string; websocket?: string }; category?: string }) {
  const isAuxiliary = 'provider_name' in provider;
  const name = isAuxiliary ? provider.provider_name : provider.name;
  const lastError = isAuxiliary ? provider.last_error : provider.lastError;
  const detail = isAuxiliary
    ? provider.last_success ? `Last success ${formatTimestamp(provider.last_success)}` : 'No successful request yet'
    : [provider.breaker && `Breaker ${provider.breaker}`, provider.websocket && `Stream ${provider.websocket}`].filter(Boolean).join(' · ');

  return (
    <div className="health-provider-row">
      <div>
        <span className="provider-name">{name}</span>
        {category && <span className="provider-category">{category}</span>}
        {(detail || lastError) && <small title={lastError || detail}>{lastError ? `Error: ${lastError}` : detail}</small>}
      </div>
      <span className={`status-badge ${provider.is_healthy ? 'status-ok' : 'status-error'}`}>
        {provider.is_healthy ? 'Healthy' : 'Unavailable'}
      </span>
    </div>
  );
}

const ProviderHealthCard = memo(function ProviderHealthCard({
  performance,
  auxiliaryProviders,
  loading,
  error,
}: {
  performance: SystemPerformance | null;
  auxiliaryProviders: AuxiliaryProviderStatuses | null;
  loading: boolean;
  error: string | null;
}) {
  const marketProviders = Object.entries(performance?.providers || {}).map(([name, status]) => ({
    name,
    is_healthy: status.is_healthy,
    lastError: status.last_error,
    breaker: status.circuit_breaker_state,
    websocket: status.ws_status,
  }));
  const auxiliary = auxiliaryProviders
    ? [
      ...auxiliaryProviders.news,
      ...auxiliaryProviders.fundamentals,
      ...auxiliaryProviders.options,
    ]
    : [];

  return (
    <div className={`health-card health-provider-card${loading && !performance && !auxiliaryProviders ? ' card-loading-skeleton' : ''}`}>
      <h2>Provider Availability</h2>
      {loading && !performance && !auxiliaryProviders ? (
        <>
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="100%" height="0.8rem" />
          <SkeletonBlock width="80%" height="0.8rem" />
        </>
      ) : error && marketProviders.length === 0 && auxiliary.length === 0 ? (
        <p className="error-text">⚠ {error}</p>
      ) : marketProviders.length || auxiliary.length ? (
        <div className="health-provider-list">
          {marketProviders.map(provider => <ProviderRow key={`market-${provider.name}`} provider={provider} category="market data" />)}
          {auxiliary.map(provider => <ProviderRow key={`aux-${provider.provider_type}-${provider.provider_name}`} provider={provider} category={provider.provider_type} />)}
        </div>
      ) : (
        <p className="info-text">No provider status has been reported yet.</p>
      )}
      {performance?.provider_observability && (
        <div className="provider-observability">
          <h3>Feature Entitlements</h3>
          <div className="provider-entitlement-grid">
            {Object.entries(performance.provider_observability.entitlements).map(([feature, entitlement]) => (
              <div className="provider-entitlement" key={feature}>
                <strong>{feature.replace(/_/g, ' ')}</strong>
                <span className={`status-badge ${entitlement.status === 'configured' || entitlement.status === 'verified' ? 'status-ok' : entitlement.status === 'disabled' ? 'status-error' : 'status-warning'}`}>
                  {entitlement.status.replace(/_/g, ' ')}
                </span>
                <small>{entitlement.provider || entitlement.providers?.join(' → ') || '—'} · {entitlement.verification.replace(/_/g, ' ')}</small>
                {entitlement.verification_note && <small title={entitlement.verification_note}>{entitlement.verification_note}</small>}
                {entitlement.timeframe_sources && (
                  <small className="provider-timeframe-sources">
                    {Object.entries(entitlement.timeframe_sources).map(([timeframe, source]) => `${timeframe}: ${source.observed.length ? source.observed.join(', ') : source.primary}`).join(' · ')}
                  </small>
                )}
              </div>
            ))}
          </div>
          <h3>Recent Provider Activity</h3>
          {performance.provider_observability.events.length > 0 ? (
            <div className="provider-event-list">
              {performance.provider_observability.events.slice(0, 8).map((event, index) => (
                <div className="provider-event-row" key={`${event.timestamp}-${index}`}>
                  <span>{event.provider} · {event.method}</span>
                  <span className={`status-badge ${event.outcome === 'success' ? 'status-ok' : event.outcome === 'fallback' ? 'status-warning' : 'status-error'}`}>{event.outcome}</span>
                  <small>{formatTimestamp(event.timestamp)}</small>
                </div>
              ))}
            </div>
          ) : <p className="info-text">No provider activity recorded yet.</p>}
        </div>
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

  const [performance, setPerformance] = useState<SystemPerformance | null>(null);
  const [auxiliaryProviders, setAuxiliaryProviders] = useState<AuxiliaryProviderStatuses | null>(null);
  const [observabilityLoading, setObservabilityLoading] = useState(true);
  const [observabilityError, setObservabilityError] = useState<string | null>(null);

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

  const fetchObservability = useCallback(async () => {
    setObservabilityLoading(true);
    setObservabilityError(null);
    const [performanceResult, providersResult] = await Promise.allSettled([
      api.getSystemPerformance(),
      api.getAuxiliaryProviderStatuses(),
    ]);

    if (performanceResult.status === 'fulfilled') setPerformance(performanceResult.value);
    else setPerformance(null);
    if (providersResult.status === 'fulfilled') setAuxiliaryProviders(providersResult.value);
    else setAuxiliaryProviders(null);

    if (performanceResult.status === 'rejected' && providersResult.status === 'rejected') {
      setObservabilityError('Failed to load runtime and provider status');
    }
    setObservabilityLoading(false);
  }, []);

  const fetchAll = useCallback(() => {
    fetchHealth();
    fetchConfig();
    fetchIngestion();
    fetchBackup();
    fetchObservability();
  }, [fetchHealth, fetchConfig, fetchIngestion, fetchBackup, fetchObservability]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleToggle = useCallback(async () => {
    if (toggling || !ingestionStatus || systemStatus?.startup_mode === 'api') return;
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
  }, [toggling, ingestionStatus, systemStatus?.startup_mode]);

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

  const anyLoading = healthLoading || configLoading || ingestionLoading || backupLoading || observabilityLoading;

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
          apiMode={systemStatus?.startup_mode === 'api'}
          onToggle={handleToggle}
        />

        <BackupCard backupStatus={backupStatus} loading={backupLoading} error={backupError} />

        <RuntimeDataCard performance={performance} loading={observabilityLoading} error={observabilityError} />

        <ProviderHealthCard
          performance={performance}
          auxiliaryProviders={auxiliaryProviders}
          loading={observabilityLoading}
          error={observabilityError}
        />

        <LastSuccessfulUpdateCard ingestionStatus={ingestionStatus} loading={ingestionLoading} />

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
