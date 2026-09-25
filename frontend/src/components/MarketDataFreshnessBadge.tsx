import React, { useEffect, useState } from 'react';
import { useStartupMode } from '../contexts/StartupModeContext';

type Freshness = 'fresh' | 'recent' | 'stale' | 'stuck' | 'unknown' | 'unavailable' | 'closed';
type BadgeState = 'live' | 'delayed' | 'cached' | 'stale' | 'reconnecting' | 'unavailable' | 'closed';
type MarketSessionType = 'premarket' | 'regular' | 'after_hours' | 'closed';

interface MarketDataFreshnessBadgeProps {
  dataStatus?: string | null;
  freshness?: Freshness | null;
  timestamp?: string | null;
  ageSeconds?: number | null;
  showAge?: boolean;
  connectionStatus?: 'connecting' | 'open' | 'closed' | 'reconnecting';
  staleAfterSeconds?: number;
  provider?: string | null;
  /** Exchange session (from useMarketSession()). When 'closed' — evenings, weekends,
   * holidays — an otherwise age-driven 'delayed'/'stale' reading is relabeled 'Closed'
   * instead of counting up an alarming, ever-growing "Xh ago". */
  marketSession?: MarketSessionType | null;
}

const labels: Record<BadgeState, string> = {
  live: 'Live',
  delayed: 'Delayed',
  cached: 'Cached',
  stale: 'Stale',
  reconnecting: 'Reconnecting',
  unavailable: 'Unavailable',
  closed: 'Closed',
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
    case 'closed':
      return 'closed';
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
  provider,
  marketSession,
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
  // Only flag an actual dropout ('reconnecting') as a problem.
  // The initial 'connecting' state is normal on page load — showing
  // "Reconnecting" there alarms the user before anything has gone wrong.
  const connectionState: BadgeState | null = connectionStatus === 'reconnecting'
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
  const rawState = startupMode === 'api' && sourceState !== 'unavailable'
    ? (sourceState === 'stale' ? 'stale' : 'cached')
    : staleByAge ? 'stale' : connectionState ?? sourceState;
  // The market being closed (evening, weekend, holiday) is expected, not a data
  // problem — don't let an age-driven 'delayed'/'stale'/'cached' reading read as
  // an incident. Genuine problems (reconnecting/unavailable) still surface as-is,
  // and API-paused mode keeps its own distinct label.
  const isClosedOverride = marketSession === 'closed'
    && startupMode !== 'api'
    && (rawState === 'delayed' || rawState === 'stale' || rawState === 'cached');
  const state: BadgeState = isClosedOverride ? 'closed' : rawState;
  const label = startupMode === 'api' ? `Paused · ${labels[state]}` : labels[state];
  const age = showAge ? formatAge(computedAge, timestamp) : null;
  const providerLabel = provider ? provider.toUpperCase() : null;
  const title = startupMode === 'api'
    ? 'STARTUP_MODE=api is active, so live market-data updates are paused.'
    : state === 'closed'
    ? 'Market is closed. Price reflects the last available trade/quote.'
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
      {label}{providerLabel ? ` · ${providerLabel}` : ''}{age ? ` · ${age}` : ''}
    </span>
  );
}
