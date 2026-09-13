import React, { useEffect, useState, useCallback } from 'react';
import api, { TopMoverResult } from '../services/api';
import { ErrorBanner } from './ErrorBanner';
import { formatETDateTime } from './chartMath';

interface TopMoversCardProps {
  onSelectSymbol?: (symbol: string) => void;
}

// Signal names emitted by backend.scanner._generate_signals (see
// backend/scanner/scanner.py). The dashboard is read-only — the backend
// already filtered by ranking category, so this is just a defensive
// re-classification in case the API returns a wrong-direction symbol.
const bullishSignals = new Set([
  'MACD_BULLISH', 'RSI_OVERSOLD', 'MULTI_TIMEFRAME_BULLISH',
  'TREND_BULLISH', 'VOLUME_EXPANSION', 'BREAKOUT',
  'DAILY_BULLISH', 'MTF_BULLISH', 'STRONG_TREND', 'TREND_STRENGTHENS',
  'FULL_ALIGNMENT', 'BULLISH_DIVERGENCE', 'HIGH_VOLUME',
]);

const bearishSignals = new Set([
  'MACD_BEARISH', 'RSI_OVERBOUGHT', 'MULTI_TIMEFRAME_BEARISH',
  'TREND_BEARISH', 'BREAKDOWN', 'TREND_WEAKENS',
  'DAILY_BEARISH', 'MTF_BEARISH', 'WEAK_TREND', 'TIMEFRAME_CONFLICT',
  'BEARISH_DIVERGENCE',
]);

function isBullish(r: TopMoverResult): boolean {
  // The backend's directional ranking is authoritative. The total_score
  // it returns is now a *signed* weighted average (positive = bullish,
  // negative = bearish). A defensive check: if the scanner emitted any
  // bearish signal and the score is not clearly positive, treat as
  // bearish; if it emitted any bullish signal and the score is not
  // clearly negative, treat as bullish. If signals are empty or
  // unrecognised, fall back to the signed total_score.
  const bullCount = r.signals.filter(s => bullishSignals.has(s)).length;
  const bearCount = r.signals.filter(s => bearishSignals.has(s)).length;
  if (bullCount > bearCount) return true;
  if (bearCount > bullCount) return false;
  return r.total_score > 0;
}

function scoreBadge(score: number): { label: string; color: string } {
  // Colors follow the app-wide trend convention (#22c55e bullish /
  // #ef4444 bearish, doc 4.1.12). This previously used near-miss hexes
  // (#10b981 / #dc2626) for the |score| > 50 band only, so the same panel
  // rendered two different greens depending on magnitude.
  const sign = score > 0 ? '+' : '';
  if (score > 0) return { label: sign + score.toFixed(0), color: '#22c55e' };
  if (score < 0) return { label: score.toFixed(0), color: '#ef4444' };
  return { label: '0', color: '#9ca3af' };
}

function MoverPanel({
  title,
  movers,
  onSelectSymbol,
  variant,
}: {
  title: string;
  movers: TopMoverResult[];
  onSelectSymbol?: (symbol: string) => void;
  variant: 'bullish' | 'bearish';
}) {
  if (movers.length === 0) {
    return (
      <div className={`top-movers-panel top-movers-${variant}`}>
        <h3 className="top-movers-panel-title">
          {variant === 'bullish' ? '🐂' : '🐻'} {title}
        </h3>
        <p className="empty-state">
          No {variant} signals{onSelectSymbol ? ' — add symbols to a watchlist' : ''}
        </p>
      </div>
    );
  }
  return (
    <div className={`top-movers-panel top-movers-${variant}`}>
      <h3 className="top-movers-panel-title">
        {variant === 'bullish' ? '🐂' : '🐻'} {title}
      </h3>
      <div className="top-movers-scroll">
        {movers.map(m => {
          const badge = scoreBadge(m.total_score);
          return (
            <div
              key={m.symbol}
              className={`mover-pill ${variant}`}
              onClick={() => onSelectSymbol?.(m.symbol)}
              role={onSelectSymbol ? 'button' : undefined}
              tabIndex={onSelectSymbol ? 0 : undefined}
              onKeyDown={(e) => {
                if (onSelectSymbol && (e.key === 'Enter' || e.key === ' ')) {
                  e.preventDefault();
                  onSelectSymbol(m.symbol);
                }
              }}
            >
              <span className="mover-symbol">{m.symbol}</span>
              <span className="mover-score" style={{ color: badge.color }}>
                {badge.label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function TopMoversCard({ onSelectSymbol }: TopMoversCardProps) {
  const [bullish, setBullish] = useState<TopMoverResult[]>([]);
  const [bearish, setBearish] = useState<TopMoverResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchMovers = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

    // Use safeCall for independent failure handling.
    const safeCall = async <T,>(fn: () => Promise<T>): Promise<{ data: T | null; error: string | null }> => {
      try {
        const data = await fn();
        return { data, error: null };
      } catch (e: any) {
        return { data: null, error: e?.message || 'Request failed' };
      }
    };

    const [bullResult, bearResult] = await Promise.all([
      safeCall(() => api.getTopMovers('bullish', 10)),
      safeCall(() => api.getTopMovers('bearish', 10)),
    ]);

    // Backend already filters by ranking category, but post-filter as a
    // safety net in case the ranking includes a wrong-direction symbol
    // due to score magnitude.
    // Defensive: the API may return an object (e.g. 404 error body) if
    // a route is misconfigured — guard against that so the dashboard
    // still renders instead of throwing.
    const asArray = <T,>(v: unknown): T[] => Array.isArray(v) ? (v as T[]) : [];
    const bullData = asArray<TopMoverResult>(bullResult.data).filter(isBullish);
    const bearData = asArray<TopMoverResult>(bearResult.data).filter(r => !isBullish(r));

    setBullish(bullData);
    setBearish(bearData);
    if (bullResult.error && bearResult.error) {
      setError(`Top movers unavailable: ${bullResult.error}`);
    }
    setLastUpdated(new Date());
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    fetchMovers();
  }, [fetchMovers]);

  return (
    <div className="card top-movers-card">
      <div className="card-header-row">
        <h2>Top Movers</h2>
        <div className="top-movers-header-right">
          {lastUpdated && (
            <span className="last-updated-inline">
              {formatETDateTime(lastUpdated.toISOString())}
            </span>
          )}
          <button
            className={`btn btn-small ${refreshing ? 'btn-loading' : ''}`}
            onClick={() => fetchMovers(true)}
            disabled={refreshing || loading}
          >
            {refreshing ? '⟳' : '↻'} Refresh
          </button>
        </div>
      </div>
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {loading ? (
        <p className="empty-state">Loading top movers…</p>
      ) : (
        <div className="top-movers-grid">
          <MoverPanel
            title="Top Bullish"
            movers={bullish}
            onSelectSymbol={onSelectSymbol}
            variant="bullish"
          />
          <MoverPanel
            title="Top Bearish"
            movers={bearish}
            onSelectSymbol={onSelectSymbol}
            variant="bearish"
          />
        </div>
      )}
    </div>
  );
}

export default TopMoversCard;
