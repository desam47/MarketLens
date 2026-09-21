import React, { useEffect, useState } from 'react';
import { useStartupMode } from '../contexts/StartupModeContext';

type Freshness = 'fresh' | 'recent' | 'stale' | 'stuck' | 'unknown' | 'unavailable';
type BadgeState = 'live' | 'delayed' | 'cached' | 'stale' | 'reconnecting' | 'unavailable';

interface MarketDataFreshnessBadgeProps {
  dataStatus?: string | null;
  freshness?: Freshness | null;
  timestamp?: string | null;
  ageSeconds?: number | null;
  showAge?: boolean;
  connectionStatus?: 'connecting' | 'open' | 'closed' | 'reconnecting';
  staleAfterSeconds?: number;
}

const labels: Record<BadgeState, string> = {
  live: 'Live',
  delayed: 'Delayed',
  cached: 'Cached',
  stale: 'Stale',
  reconnecting: 'Reconnecting',
  unavailable: 'Unavailable',
};

function stateFromDataStatus(dataStatus?: string | null): BadgeState | null {
  switch (dataStatus?.toUpperCase()) {
    case 'LIVE':
      return 'live';
    case 'DELAYED':
      return 'delayed';
    case 'HISTORICAL':
      return 'cached';
    case 'STALE':
    case 'GAP':
    case 'INCOMPLETE':
    case 'DUPLICATE':
      return 'stale';
    case 'ERROR':
      return 'unavailable';
    default:
      return null;
  }
}

function stateFromFreshness(freshness?: Freshness | null): BadgeState {
  switch (freshness) {
    case 'fresh':
      return 'live';
    case 'recent':
      return 'delayed';
    case 'stale':
    case 'stuck':
      return 'stale';
    default:
      return 'unavailable';
  }
}

function formatAge(ageSeconds: number | null | undefined, timestamp?: string | null): string | null {
  const seconds = ageSeconds ?? (timestamp ? (Date.now() - Date.parse(timestamp)) / 1000 : null);
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const rounded = Math.max(0, Math.round(seconds));
  if (rounded < 60) return `${rounded}s ago`;
  if (rounded < 3600) return `${Math.round(rounded / 60)}m ago`;
  if (rounded < 86400) return `${Math.round(rounded / 3600)}h ago`;
  return `${Math.round(rounded / 86400)}d ago`;
}

export function MarketDataFreshnessBadge({
  dataStatus,
  freshness,
  timestamp,
  ageSeconds,
  showAge = false,
  connectionStatus,
  staleAfterSeconds = 15,
}: MarketDataFreshnessBadgeProps) {
  const startupMode = useStartupMode();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!timestamp || !showAge) return;
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(id);
  }, [timestamp, showAge]);
  const computedAge = ageSeconds ?? (timestamp ? (now - Date.parse(timestamp)) / 1000 : null);
  const sourceState = stateFromDataStatus(dataStatus) ?? stateFromFreshness(freshness);
  const connectionState: BadgeState | null = connectionStatus === 'reconnecting' || connectionStatus === 'connecting'
    ? 'reconnecting'
    : connectionStatus === 'closed'
    ? 'stale'
    : null;
  const staleByAge = dataStatus?.toUpperCase() === 'LIVE'
    && computedAge != null
    && Number.isFinite(computedAge)
    && computedAge > staleAfterSeconds;
  // API mode deliberately does not update providers. A status persisted from
  // a previous full run must never be presented as currently live.
  const state = startupMode === 'api' && sourceState !== 'unavailable'
    ? (sourceState === 'stale' ? 'stale' : 'cached')
    : staleByAge ? 'stale' : connectionState ?? sourceState;
  const label = startupMode === 'api' ? `Paused · ${labels[state]}` : labels[state];
  const age = showAge ? formatAge(computedAge, timestamp) : null;
  const title = startupMode === 'api'
    ? 'STARTUP_MODE=api is active, so live market-data updates are paused.'
    : connectionState
    ? `Realtime connection: ${connectionStatus}`
    : staleByAge
    ? `No live quote received for ${Math.round(computedAge ?? 0)} seconds.`
    : dataStatus
    ? `Market-data status: ${dataStatus}`
    : freshness
    ? `Derived market-data freshness: ${freshness}`
    : 'Market-data freshness is unavailable.';

  return (
    <span className={`market-data-freshness market-data-freshness-${state}`} title={title}>
      <span className="market-data-freshness-dot" />
      {label}{age ? ` · ${age}` : ''}
    </span>
  );
}
