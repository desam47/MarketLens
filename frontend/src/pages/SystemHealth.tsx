import React, { useState, useEffect, useCallback, memo } from 'react';
import api, {
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
import type { NavigationState } from '../utils/appNavigation';

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

function freshnessStatus(seconds: number | null | undefined): { label: string; className: string; pulseClass: string } {
  if (seconds == null || !Number.isFinite(seconds)) return { label: 'Unknown', className: 'status-warning', pulseClass: 'pulse-warning' };
  if (seconds <= 60) return { label: 'Fresh', className: 'status-ok', pulseClass: 'pulse-ok' };
  if (seconds <= 300) return { label: 'Delayed', className: 'status-warning', pulseClass: 'pulse-warning' };
  return { label: 'Stale', className: 'status-error', pulseClass: 'pulse-error' };
}

function updateAge(timestamp: string): { label: string; className: string } {
  const age = (Date.now() - new Date(timestamp).getTime()) / 1000;
  return freshnessStatus(Number.isFinite(age) ? Math.max(0, age) : null);
}

// ---------------------------------------------------------------------------
// Premium Components
// ---------------------------------------------------------------------------

const SystemStatusBanner = memo(function SystemStatusBanner({
  health,
  performance,
  ingestionStatus,
  backupStatus,
  loading
}: {
  health: HealthData | null;
  performance: SystemPerformance | null;
  ingestionStatus: IngestionStatus | null;
  backupStatus: BackupStatusData | null;
  loading: boolean;
}) {
  const freshness = freshnessStatus(performance?.ingestion.tf_update_latency_seconds);
  const isHealthy = health && ingestionStatus && backupStatus;

  return (
    <div className="health-banner">
      <div className="health-banner-metrics">
        <div className="health-banner-metric">
          <span className="label">System Status</span>
          <span className="value">
            {loading ? <SkeletonBlock width="80px" height="1.5rem" /> : (
              <>
                <span className={`status-pulse ${isHealthy ? 'pulse-ok' : 'pulse-error'}`}></span>
                {isHealthy ? 'Operational' : 'Degraded'}
              </>
            )}
          </span>
        </div>
        <div className="health-banner-metric">
          <span className="label">Data Freshness</span>
          <span className="value">
            {loading ? <SkeletonBlock width="80px" height="1.5rem" /> : (
              <>
                <span className={`status-pulse ${freshness.pulseClass}`} aria-hidden="true"></span>
                {/* Name the status as well as colouring it, so it doesn't rely on colour alone. */}
                {performance?.ingestion.tf_update_latency_seconds == null
                  ? freshness.label
                  : `${freshness.label} · ${Math.round(performance.ingestion.tf_update_latency_seconds)}s`}
              </>
            )}
          </span>
        </div>
        <div className="health-banner-metric">
          <span className="label">Ingestion Engine</span>
          <span className="value">
            {loading ? <SkeletonBlock width="80px" height="1.5rem" /> : (
              <>
                <span className={`status-pulse ${ingestionStatus?.is_running ? 'pulse-ok' : 'pulse-warning'}`}></span>
                {ingestionStatus?.is_running ? 'Running' : 'Stopped'}
              </>
            )}
          </span>
        </div>
      </div>
    </div>
  );
});

const ConnectionTestCard = memo(function ConnectionTestCard({ health, systemStatus, ingestionStatus, backupStatus, loading }: any) {
  return (
    <div className={`health-card${loading ? ' card-loading-skeleton' : ''}`}>
      <h2>Connection Diagnostics</h2>
      {loading ? (
        <><SkeletonBlock width="100%" height="0.8rem" /><SkeletonBlock width="100%" height="0.8rem" /></>
      ) : (
        <div className="connection-status">
          <div className="test-results">
            <div className="test-row"><span>Health Endpoint:</span><span className={health ? 'status-ok' : 'status-error'}>{health ? '✓ OK' : '✗ Failed'}</span></div>
            <div className="test-row"><span>System Status:</span><span className={systemStatus ? 'status-ok' : 'status-error'}>{systemStatus ? '✓ OK' : '✗ Failed'}</span></div>
            <div className="test-row"><span>Data Ingestion:</span><span className={ingestionStatus ? 'status-ok' : 'status-error'}>{ingestionStatus ? '✓ OK' : '✗ Failed'}</span></div>
            <div className="test-row"><span>Backup Status:</span><span className={backupStatus ? 'status-ok' : 'status-error'}>{backupStatus ? '✓ OK' : '✗ Failed'}</span></div>
          </div>
        </div>
      )}
    </div>
  );
});

const ApiServiceCard = memo(function ApiServiceCard({ health, loading, error }: any) {
  return (
    <div className={`health-card${loading && !health ? ' card-loading-skeleton' : ''}`}>
      <h2>API Service</h2>
      {loading && !health ? (
        <><SkeletonBlock width="40%" height="1.4rem" /><SkeletonBlock width="100%" height="0.8rem" /></>
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

const SystemConfigCard = memo(function SystemConfigCard({ systemConfig, systemStatus, loading, error }: any) {
  return (
    <div className={`health-card${loading && !systemConfig && !systemStatus ? ' card-loading-skeleton' : ''}`}>
      <h2>System Configuration</h2>
      {loading && !systemConfig && !systemStatus ? (
        <><SkeletonBlock width="100%" height="0.8rem" /><SkeletonBlock width="100%" height="0.8rem" /></>
      ) : error ? (
        <p className="error-text">⚠ {error}</p>
      ) : systemConfig ? (
        <div className="health-details">
          <p>
            <strong>Primary Provider:</strong> <span className="provider-name">{systemConfig.market_data_primary_provider}</span>
            <span className="config-source-badge" title="Read live from .env" style={{marginLeft: '0.5rem', fontSize: '0.7em', background: 'var(--ok)', color: '#000', padding: '2px 4px', borderRadius: '4px'}}>LIVE</span>
          </p>
          {systemConfig.market_data_fallback_providers && systemConfig.market_data_fallback_providers.length > 0 && (
            <p><strong>Fallback:</strong> {systemConfig.market_data_fallback_providers.join(', ')}</p>
          )}
          {systemStatus && (
            <>
              <p><strong>Startup Mode:</strong> <span className={`status-badge ${systemStatus.startup_mode === 'api' ? 'status-warning' : 'status-ok'}`}>{systemStatus.startup_mode.toUpperCase()}</span></p>
              <p><strong>Debug Mode:</strong> {systemStatus.debug ? 'Yes' : 'No'}</p>
              <p><strong>AI Enabled:</strong> {systemStatus.ai_enabled ? 'Yes' : 'No'}</p>
              <p><strong>Version:</strong> {systemStatus.version}</p>
            </>
          )}
        </div>
      ) : systemStatus ? (
         <div className="health-details">
           <p><strong>Market Data Provider:</strong> {systemStatus.market_data_provider}</p>
           {systemStatus.market_data_fallback_providers && systemStatus.market_data_fallback_providers.length > 0 && (
             <p><strong>Fallback Provider(s):</strong> {systemStatus.market_data_fallback_providers.join(', ')}</p>
           )}
           <p><strong>Startup Mode:</strong> <span className={`status-badge ${systemStatus.startup_mode === 'api' ? 'status-warning' : 'status-ok'}`}>{systemStatus.startup_mode.toUpperCase()}</span></p>
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

const IngestionCard = memo(function IngestionCard({ ingestionStatus, loading, error, toggling, apiMode, onToggle }: any) {
  return (
    <div className={`health-card${loading && !ingestionStatus ? ' card-loading-skeleton' : ''}`}>
      <h2>Data Ingestion Controls</h2>
      {loading && !ingestionStatus ? (
        <><SkeletonBlock width="50%" height="1.4rem" /><SkeletonBlock width="100%" height="0.8rem" /></>
      ) : error ? (
        <p className="error-text">⚠ {error}</p>
      ) : ingestionStatus ? (
        <div className="health-details">
          <p>
            <strong>Status:</strong>
            <span className={`status-badge ${ingestionStatus.is_running ? 'status-ok' : 'status-warning'}`} style={{marginLeft: '0.5rem'}}>
              {ingestionStatus.is_running ? 'Running' : 'Stopped'}
            </span>
          </p>
          <p><strong>Tracking Watchlists:</strong> {ingestionStatus.watchlists?.join(', ') || 'None'}</p>
          <p><strong>Timeframes:</strong> {ingestionStatus.timeframes?.join(', ') || 'None'}</p>
          <div className="toggle-row" style={{marginTop: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem'}}>
            <label className="toggle-switch">
              <input type="checkbox" checked={!!ingestionStatus.is_running} disabled={toggling || apiMode} onChange={onToggle} />
              <span className="toggle-slider" />
            </label>
            <span className="toggle-label" style={{fontSize: '0.85rem', color: 'var(--text-muted)'}}>
              {apiMode
                ? 'API mode — live ingestion is disabled.'
                : toggling
                ? 'Switching…'
                : ingestionStatus.is_running
                ? 'Ingestion ON — click to stop'
                : 'Ingestion OFF — click to start'}
            </span>
          </div>
        </div>
      ) : (
        <p className="info-text">Ingestion service not available.</p>
      )}
    </div>
  );
});

const BackupCard = memo(function BackupCard({ backupStatus, loading, error }: any) {
  return (
    <div className={`health-card${loading && !backupStatus ? ' card-loading-skeleton' : ''}`}>
      <h2>Database &amp; Storage</h2>
      {loading && !backupStatus ? (
        <><SkeletonBlock width="60%" height="1.4rem" /><SkeletonBlock width="100%" height="0.8rem" /></>
      ) : error ? (
        <p className="error-text">⚠ {error}</p>
      ) : backupStatus ? (
        <div className="health-details">
          <p><strong>Journal:</strong> <span className={`status-badge ${backupStatus.journal_mode === 'wal' ? 'status-ok' : 'status-warning'}`}>{backupStatus.journal_mode.toUpperCase()}</span></p>
          <p>
             <strong>WAL Checkpoint:</strong> <span className={`status-badge ${backupStatus.wal_checkpoint_busy ? 'status-warning' : 'status-ok'}`}>{backupStatus.wal_checkpoint_busy ? 'Busy' : 'Idle'}</span>
             <span className="health-meta"> — {backupStatus.wal_checkpoint_frames} frames, end pg {backupStatus.wal_checkpoint_end}</span>
          </p>
          <p><strong>WAL File:</strong> {formatBytes(backupStatus.wal_size_bytes)}</p>
          <p><strong>SHM File:</strong> {formatBytes(backupStatus.shm_size_bytes)}</p>
          <p>
             <strong>Litestream:</strong> <span className={`status-badge ${backupStatus.litestream_reachable ? 'status-ok' : 'status-error'}`}>{backupStatus.litestream_reachable ? 'Streaming' : 'Not Reachable'}</span>
             {backupStatus.litestream_reachable && backupStatus.litestream_generation != null && (
               <span className="health-meta"> — gen {backupStatus.litestream_generation} {backupStatus.litestream_dbs ? `(${backupStatus.litestream_dbs.length} db)` : ''}</span>
             )}
          </p>
        </div>
      ) : (
        <p className="info-text">Backup status unavailable.</p>
      )}
    </div>
  );
});

const RuntimeDataCard = memo(function RuntimeDataCard({ performance, loading, error }: any) {
  const cache = performance?.cache?.cache;
  const redis = performance?.cache?.redis;

  return (
    <div className={`health-card${loading && !performance ? ' card-loading-skeleton' : ''}`}>
      <h2>Runtime &amp; Cache</h2>
      {loading && !performance ? (
        <><SkeletonBlock width="90%" height="0.8rem" /><SkeletonBlock width="100%" height="0.8rem" /></>
      ) : error && !performance ? (
        <p className="error-text">⚠ {error}</p>
      ) : performance ? (
        <div className="health-details">
          <p><strong>Latest Bar:</strong> <span className="health-value">{formatTimestamp(performance.ingestion.last_bar_time)}</span></p>
          <p><strong>Redis:</strong> {redis?.status} {redis?.error && <small className="health-meta" style={{color: 'var(--error)'}}> — {redis.error}</small>}</p>
          {cache ? (
            <div className="circular-progress-container">
              <div className="circular-progress-wrapper">
                <div className="circular-progress" style={{'--progress': `${cache.quote_hit_rate}%`} as any}>
                  <div className="circular-progress-inner"><span className="pct">{cache.quote_hit_rate.toFixed(0)}%</span></div>
                </div>
                <span>Quotes</span>
              </div>
              <div className="circular-progress-wrapper">
                <div className="circular-progress" style={{'--progress': `${cache.bar_hit_rate}%`} as any}>
                  <div className="circular-progress-inner"><span className="pct">{cache.bar_hit_rate.toFixed(0)}%</span></div>
                </div>
                <span>Bars</span>
              </div>
            </div>
          ) : (
            <p>No cache metrics</p>
          )}
        </div>
      ) : (
        <p className="info-text">Runtime metrics unavailable.</p>
      )}
    </div>
  );
});

function ProviderRow({ provider, category }: any) {
  const isAuxiliary = 'provider_name' in provider;
  const name = isAuxiliary ? provider.provider_name : provider.name;
  const lastError = isAuxiliary ? provider.last_error : provider.lastError;
  const detail = isAuxiliary
    ? provider.last_success ? `Last success ${formatTimestamp(provider.last_success)}` : 'No successful request yet'
    : [provider.breaker && `Breaker ${provider.breaker}`, provider.websocket && `Stream ${provider.websocket}`].filter(Boolean).join(' · ');

  return (
    <div className="health-provider-row" style={{marginBottom: '0.5rem', display: 'flex', justifyContent: 'space-between'}}>
      <div style={{display: 'flex', flexDirection: 'column'}}>
        <div>
          <span className="provider-name" style={{fontWeight: 'bold'}}>{name}</span>
          {category && <span className="provider-category" style={{marginLeft: '0.5rem', fontSize: '0.8em', color: 'var(--text-muted)'}}>{category}</span>}
        </div>
        {(detail || lastError) && (
          <small style={{color: lastError ? 'var(--error)' : 'var(--text-muted)'}} title={lastError || detail}>
            {lastError ? `Error: ${lastError}` : detail}
          </small>
        )}
      </div>
      <span className={`status-badge ${provider.is_healthy ? 'status-ok' : 'status-error'}`}>
        {provider.is_healthy ? 'Healthy' : 'Unavailable'}
      </span>
    </div>
  );
}

const ProviderHealthCard = memo(function ProviderHealthCard({ performance, auxiliaryProviders, loading, error }: any) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const marketProviders = Object.entries(performance?.providers || {}).map(([name, status]: [string, any]) => ({
    name, is_healthy: status.is_healthy, lastError: status.last_error, breaker: status.circuit_breaker_state, websocket: status.ws_status
  }));
  const auxiliary = auxiliaryProviders ? [
    ...auxiliaryProviders.news.map((p: any) => ({...p, category: 'news'})),
    ...auxiliaryProviders.fundamentals.map((p: any) => ({...p, category: 'fundamentals'})),
    ...auxiliaryProviders.options.map((p: any) => ({...p, category: 'options'}))
  ] : [];

  return (
    <div className={`health-card health-provider-card${loading && !performance && !auxiliaryProviders ? ' card-loading-skeleton' : ''}`} style={{gridColumn: '1 / -1'}}>
      <h2>Provider Availability</h2>
      {loading && !performance && !auxiliaryProviders ? (
        <SkeletonBlock width="100%" height="0.8rem" />
      ) : error && marketProviders.length === 0 && auxiliary.length === 0 ? (
        <p className="error-text">⚠ {error}</p>
      ) : marketProviders.length || auxiliary.length ? (
        <>
          <p className="info-text" style={{marginBottom: '0.5rem', fontWeight: 500}}>
            {marketProviders.length + auxiliary.length} providers configured.
            {(marketProviders.some((p: any) => !p.is_healthy) || auxiliary.some((p: any) => !p.is_healthy)) ? ' ⚠ Some providers have issues.' : ' ✓ All feeds operational.'}
          </p>
          <button className="health-provider-advanced-toggle" style={{marginTop: 0, padding: 0}} onClick={() => setShowAdvanced(!showAdvanced)}>
            {showAdvanced ? 'Hide Advanced Diagnostics' : 'Show Advanced Diagnostics'}
          </button>
          {showAdvanced && (
             <div className="provider-observability" style={{marginTop: '1rem'}}>
               <h3>Individual Statuses</h3>
               <div className="health-provider-list" style={{marginBottom: '1.5rem', display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem'}}>
                 {marketProviders.map((p: any) => <ProviderRow key={`market-${p.name}`} provider={p} category="market data" />)}
                 {auxiliary.map((p: any) => <ProviderRow key={`aux-${p.provider_type}-${p.provider_name}`} provider={p} category={p.category} />)}
               </div>
               {performance?.provider_observability && (
                 <>
                   <h3>Feature Entitlements</h3>
               <div className="provider-entitlement-grid">
                 {Object.entries(performance.provider_observability.entitlements).map(([feature, entitlement]: [string, any]) => (
                   <div className="provider-entitlement" key={feature}>
                     <strong>{feature.replace(/_/g, ' ')}</strong>
                     <span className={`status-badge ${entitlement.status === 'configured' || entitlement.status === 'verified' ? 'status-ok' : entitlement.status === 'disabled' ? 'status-error' : 'status-warning'}`}>
                       {entitlement.status.replace(/_/g, ' ')}
                     </span>
                     <small>{entitlement.provider || entitlement.providers?.join(' → ') || '—'} · {entitlement.verification.replace(/_/g, ' ')}</small>
                     {entitlement.verification_note && <small title={entitlement.verification_note}>{entitlement.verification_note}</small>}
                     {entitlement.timeframe_sources && (
                       <small className="provider-timeframe-sources">
                         {Object.entries(entitlement.timeframe_sources).map(([timeframe, source]: [string, any]) => `${timeframe}: ${source.observed.length ? source.observed.join(', ') : source.primary}`).join(' · ')}
                       </small>
                     )}
                   </div>
                 ))}
               </div>
               <h3>Recent Provider Activity</h3>
               {performance.provider_observability.events.length > 0 ? (
                 <div className="provider-event-list">
                   {performance.provider_observability.events.slice(0, 8).map((event: any, index: number) => (
                     <div className="provider-event-row" key={`${event.timestamp}-${index}`}>
                       <span>{event.provider} · {event.method}</span>
                       <span className={`status-badge ${event.outcome === 'success' ? 'status-ok' : event.outcome === 'fallback' ? 'status-warning' : 'status-error'}`}>{event.outcome}</span>
                       <small>{formatTimestamp(event.timestamp)}</small>
                     </div>
                   ))}
                 </div>
               ) : <p className="info-text">No provider activity recorded yet.</p>}
                 </>
               )}
             </div>
          )}
        </>
      ) : (
        <p className="info-text">No provider status reported.</p>
      )}
    </div>
  );
});

const LastSuccessfulUpdateCard = memo(function LastSuccessfulUpdateCard({ ingestionStatus, loading }: any) {
  const rows: any[] = [];
  Object.entries(ingestionStatus?.last_quote_updates || {}).forEach(([symbol, timestamp]) => {
    if (timestamp) rows.push({ symbol, dataType: 'Quote', timestamp, provider: ingestionStatus?.last_quote_providers?.[symbol] || 'Unknown' });
  });
  Object.entries(ingestionStatus?.last_bar_updates || {}).forEach(([symbol, timeframes]: [string, any]) => {
    Object.entries(timeframes).forEach(([timeframe, timestamp]) => {
      if (timestamp) rows.push({ symbol, dataType: `${timeframe} bar`, timestamp, provider: ingestionStatus?.last_bar_providers?.[symbol]?.[timeframe] || 'Unknown' });
    });
  });
  Object.entries(ingestionStatus?.last_status_updates || {}).forEach(([symbol, timestamp]) => {
    if (timestamp) rows.push({ symbol, dataType: 'Market status', timestamp, provider: ingestionStatus?.last_status_providers?.[symbol] || 'Unknown' });
  });
  rows.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

  return (
    <div className={`health-card last-update-card${loading && !ingestionStatus ? ' card-loading-skeleton' : ''}`} style={{gridColumn: '1 / -1'}}>
      <h2>Last Successful Update Feed</h2>
      {loading && !ingestionStatus ? (
        <><SkeletonBlock width="100%" height="0.8rem" /><SkeletonBlock width="90%" height="0.8rem" /></>
      ) : rows.length === 0 ? (
        <p className="info-text">No successful market-data updates recorded yet.</p>
      ) : (
        <div className="last-update-list-container">
          <div className="last-update-list">
            <div className="last-update-row last-update-header" style={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr', fontWeight: 'bold', paddingBottom: '0.5rem', borderBottom: '1px solid var(--border)', marginBottom: '0.5rem'}}>
              <span>Symbol</span><span>Data</span><span>Provider</span><span>Updated</span><span>Status</span>
            </div>
            {rows.map(row => {
              const status = updateAge(row.timestamp);
              return (
                <div className="last-update-row" key={`${row.symbol}-${row.dataType}`} style={{display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1fr', alignItems: 'center', marginBottom: '0.25rem'}}>
                  <strong>{row.symbol}</strong><span>{row.dataType}</span><span>{row.provider}</span>
                  <span title={formatTimestamp(row.timestamp)}>{formatTimestamp(row.timestamp).split(' ')[1] || formatTimestamp(row.timestamp)}</span>
                  <span className={`status-badge ${status.className}`}>{status.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
});

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export function SystemHealth({ navigation }: { navigation?: NavigationState }) {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [systemConfig, setSystemConfig] = useState<SystemConfig | null>(null);
  const [ingestionStatus, setIngestionStatus] = useState<IngestionStatus | null>(null);
  const [backupStatus, setBackupStatus] = useState<BackupStatusData | null>(null);
  const [performance, setPerformance] = useState<SystemPerformance | null>(null);
  const [auxiliaryProviders, setAuxiliaryProviders] = useState<AuxiliaryProviderStatuses | null>(null);

  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [globalError, setGlobalError] = useState<string | null>(null);

  const [showRestartModal, setShowRestartModal] = useState(false);
  const [restarting, setRestarting] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setGlobalError(null);
    try {
      const [h, c, s, i, b, perf, aux] = await Promise.allSettled([
        api.getHealth(),
        api.getSystemConfig(),
        api.getSystemStatus(),
        api.getIngestionStatus(),
        api.getBackupStatus(),
        api.getSystemPerformance(),
        api.getAuxiliaryProviderStatuses()
      ]);

      if (h.status === 'fulfilled') setHealth(h.value);
      if (c.status === 'fulfilled') setSystemConfig(c.value);
      if (s.status === 'fulfilled') setSystemStatus(s.value);
      if (i.status === 'fulfilled') setIngestionStatus(i.value);
      if (b.status === 'fulfilled') setBackupStatus(b.value);
      if (perf.status === 'fulfilled') setPerformance(perf.value);
      if (aux.status === 'fulfilled') setAuxiliaryProviders(aux.value);
    } catch (err: any) {
      setGlobalError(err?.message || 'Failed to aggregate health data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleToggle = useCallback(async () => {
    if (toggling || !ingestionStatus || systemStatus?.startup_mode === 'api') return;
    setToggling(true);
    try {
      const result = await api.toggleIngestion();
      setIngestionStatus((prev: any) => prev ? { ...prev, is_running: result.is_running } : prev);
    } catch (err: any) {
      setGlobalError(`Toggle failed: ${err.message}`);
    } finally {
      setToggling(false);
    }
  }, [toggling, ingestionStatus, systemStatus?.startup_mode]);

  const handleRestartConfirm = async () => {
    setRestarting(true);
    setShowRestartModal(false);
    setGlobalError(null);
    try {
      await api.restartServices();
      // Poll until backend is back up
      const poll = setInterval(async () => {
        try {
          const res = await api.getHealth();
          if (res && res.status) {
            clearInterval(poll);
            window.location.reload();
          }
        } catch (e) {
          // Keep polling
        }
      }, 1500);
    } catch (err: any) {
      setGlobalError(`Restart failed: ${err.message}`);
      setRestarting(false);
    }
  };

  return (
    <div className="system-health">
      <div className="health-header">
        <h1>System Health</h1>
        <div className="health-header-actions">
          <button className="btn" onClick={fetchAll} disabled={loading || restarting}>
            {loading ? '⟳ Refreshing…' : '↻ Refresh'}
          </button>
          <button className="btn btn-danger" onClick={() => setShowRestartModal(true)} disabled={restarting}>
            ⟲ Restart
          </button>
        </div>
      </div>

      {globalError && <ErrorBanner message={globalError} onDismiss={() => setGlobalError(null)} />}

      <SystemStatusBanner
        health={health}
        performance={performance}
        ingestionStatus={ingestionStatus}
        backupStatus={backupStatus}
        loading={loading}
      />

      <div className="health-grid">
        <ConnectionTestCard health={health} systemStatus={systemStatus} ingestionStatus={ingestionStatus} backupStatus={backupStatus} loading={loading} />
        <ApiServiceCard health={health} loading={loading} />
        <SystemConfigCard systemConfig={systemConfig} systemStatus={systemStatus} loading={loading} />
        <IngestionCard ingestionStatus={ingestionStatus} loading={loading} toggling={toggling} apiMode={systemStatus?.startup_mode === 'api'} onToggle={handleToggle} />
        <RuntimeDataCard performance={performance} loading={loading} />
        <BackupCard backupStatus={backupStatus} loading={loading} />
        <ProviderHealthCard performance={performance} auxiliaryProviders={auxiliaryProviders} loading={loading} />
        <LastSuccessfulUpdateCard ingestionStatus={ingestionStatus} loading={loading} />
      </div>

      <div className="health-info" style={{marginTop: '3rem', paddingTop: '1.5rem', borderTop: '1px solid var(--border)'}}>
        <h3>Quick Tips</h3>
        <ul style={{color: 'var(--text-muted)'}}>
          <li>Ensure the API is running on port 5001</li>
          <li>Check that market data providers are configured in your `.env`</li>
          <li>Monitor ingestion status for data freshness</li>
        </ul>
      </div>

      {/* Custom Modals & Overlays */}
      {showRestartModal && (
        <div className="modal-overlay" onClick={() => setShowRestartModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2>Restart System?</h2>
            <p>This will restart the backend and frontend development servers. Analysis will be briefly unavailable.</p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setShowRestartModal(false)}>Cancel</button>
              <button className="btn btn-danger" onClick={handleRestartConfirm}>Restart Now</button>
            </div>
          </div>
        </div>
      )}

      {restarting && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="restarting-spinner"></div>
            <h2>Rebooting System</h2>
            <p>Waiting for services to come back online...</p>
          </div>
        </div>
      )}
    </div>
  );
}
