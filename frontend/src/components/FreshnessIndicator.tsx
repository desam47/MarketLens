import React from 'react';
import { RegimeData } from '../services/api';
import { MarketDataFreshnessBadge } from './MarketDataFreshnessBadge';

interface FreshnessIndicatorProps {
  regime: RegimeData | null;
}

export function FreshnessIndicator({ regime }: FreshnessIndicatorProps) {
  return (
    <MarketDataFreshnessBadge
      freshness={regime?.freshness}
      timestamp={regime?.timestamp}
      ageSeconds={regime?.data_age_seconds}
      showAge
    />
  );
}
