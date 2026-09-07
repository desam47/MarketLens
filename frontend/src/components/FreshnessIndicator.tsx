import React from 'react';
import { RegimeData } from '../services/api';
import { parseET } from './chartMath';

interface FreshnessIndicatorProps {
  regime: RegimeData | null;
}

const freshnessLabels: Record<RegimeData['freshness'], { text: string; color: string }> = {
  fresh:   { text: 'Live',     color: '#10b981' },  // green  — engine saw a tick in the last 60s
  recent:  { text: 'Recent',   color: '#f59e0b' },  // yellow — current within the same bar
  stale:   { text: 'Stale',    color: '#f97316' },  // orange — engine hasn't updated in a while
  stuck:   { text: 'Stuck',    color: '#ef4444' },  // red    — engine state is from a prior session
  unknown: { text: 'Unknown',  color: '#9ca3af' },  // gray   — no signal yet
};

function formatAge(ageSeconds: number | null | undefined): string {
  if (ageSeconds === null || ageSeconds === undefined) return 'never';
  if (ageSeconds < 60) return `${Math.round(ageSeconds)}s ago`;
  if (ageSeconds < 3600) return `${Math.round(ageSeconds / 60)}m ago`;
  if (ageSeconds < 86400) return `${Math.round(ageSeconds / 3600)}h ago`;
  return `${Math.round(ageSeconds / 86400)}d ago`;
}

export function FreshnessIndicator({ regime }: FreshnessIndicatorProps) {
  if (!regime) {
    return (
      <span className="freshness-pill" style={{ backgroundColor: freshnessLabels.unknown.color }}>
        <span className="freshness-dot" />
        No data
      </span>
    );
  }
  const { freshness, data_age_seconds } = regime;
  const { text, color } = freshnessLabels[freshness] ?? freshnessLabels.unknown;
  return (
    <span
      className="freshness-pill"
      style={{ backgroundColor: color }}
      title={
        regime.timestamp
          ? `Engine last saw a tick at ${parseET(regime.timestamp).toLocaleString()}`
          : 'Engine has not generated a signal yet'
      }
    >
      <span className="freshness-dot" />
      {text} · {formatAge(data_age_seconds)}
    </span>
  );
}
