import React from 'react';
import { MarketDataFreshnessBadge } from './MarketDataFreshnessBadge';

interface MarketDataUpdateStatusProps {
  timestamp?: string | null;
  provider?: string | null;
  dataStatus?: string | null;
  freshness?: 'fresh' | 'recent' | 'stale' | 'stuck' | 'unknown' | 'unavailable' | 'closed' | null;
  connectionStatus?: 'connecting' | 'open' | 'closed' | 'reconnecting';
  marketSession?: 'premarket' | 'regular' | 'after_hours' | 'closed' | null;
  className?: string;
}

function formatTimestamp(timestamp?: string | null): string {
  if (!timestamp) return 'Not available';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(date);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value ?? '';
  return `${value('year')}-${value('month')}-${value('day')} ${value('hour')}:${value('minute')}:${value('second')}`;
}

export function MarketDataUpdateStatus({
  timestamp,
  provider,
  dataStatus,
  freshness,
  connectionStatus,
  marketSession,
  className = '',
}: MarketDataUpdateStatusProps) {
  return (
    <span className={`market-data-update-status ${className}`.trim()}>
      <MarketDataFreshnessBadge
        dataStatus={dataStatus}
        freshness={freshness}
        timestamp={timestamp}
        showAge
        connectionStatus={connectionStatus}
        provider={provider}
        marketSession={marketSession}
      />
      <span className="market-data-update-time">Updated {formatTimestamp(timestamp)}</span>
    </span>
  );
}

export default MarketDataUpdateStatus;
