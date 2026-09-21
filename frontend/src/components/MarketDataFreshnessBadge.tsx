import React from 'react';
import { useStartupMode } from '../contexts/StartupModeContext';

type Freshness = 'fresh' | 'recent' | 'stale' | 'stuck' | 'unknown' | 'unavailable';
type BadgeState = 'live' | 'delayed' | 'cached' | 'stale' | 'unavailable';

interface MarketDataFreshnessBadgeProps {
  dataStatus?: string | null;
  freshness?: Freshness | null;
  timestamp?: string | null;
  ageSeconds?: number | null;
  showAge?: boolean;
}

const labels: Record<BadgeState, string> = {
  live: 'Live',
  delayed: 'Delayed',
  cached: 'Cached',
  stale: 'Stale',
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
}: MarketDataFreshnessBadgeProps) {
  const startupMode = useStartupMode();
  const sourceState = stateFromDataStatus(dataStatus) ?? stateFromFreshness(freshness);
  // API mode deliberately does not update providers. A status persisted from
  // a previous full run must never be presented as currently live.
  const state = startupMode === 'api' && sourceState !== 'unavailable'
    ? (sourceState === 'stale' ? 'stale' : 'cached')
    : sourceState;
  const label = startupMode === 'api' ? `Paused · ${labels[state]}` : labels[state];
  const age = showAge ? formatAge(ageSeconds, timestamp) : null;
  const title = startupMode === 'api'
    ? 'STARTUP_MODE=api is active, so live market-data updates are paused.'
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
