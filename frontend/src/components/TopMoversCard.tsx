import React, { useEffect, useState, useCallback } from 'react';
import api, { TopMoverResult } from '../services/api';
import { ErrorBanner } from './ErrorBanner';
import { formatETDateTime } from './chartMath';

interface TopMoversCardProps {
  onSelectSymbol?: (symbol: string) => void;
}

function changeBadge(changePct: number | null): { label: string; color: string } {
  // Colors follow the app-wide trend convention (#22c55e bullish /
  // #ef4444 bearish, doc 4.1.12).
  if (changePct == null) return { label: '—', color: '#9ca3af' };
  const sign = changePct > 0 ? '+' : '';
  if (changePct > 0) return { label: `${sign}${changePct.toFixed(2)}%`, color: '#22c55e' };
  if (changePct < 0) return { label: `${changePct.toFixed(2)}%`, color: '#ef4444' };
  return { label: '0.00%', color: '#9ca3af' };
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
          const badge = changeBadge(m.change_pct);
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
      safeCall(() => api.getTopMovers('bullish', 20)),
      safeCall(() => api.getTopMovers('bearish', 20)),
    ]);

    // The backend ranks strongest_bullish/strongest_bearish by live
    // change_pct now (real price direction, not the momentum/RSI
    // composite score) — it's authoritative, trust it as-is. This used
    // to re-filter both lists through isBullish() (a signals/total_score
    // heuristic) as a "safety net," but that heuristic still reflects
    // the old score-based direction, so it actively fought the new
    // change_pct-based backend ranking: a symbol like a crashing penny
    // stock with bullish-looking signals (oversold RSI, a lagging
    // MACD_BULLISH) would get silently dropped from the bearish list
    // (isBullish() said true) without ever qualifying for the bullish
    // list either (its change_pct ranked it last there) — vanishing
    // from Top Movers entirely. Found live 2026-09-16 (CTNT).
    // Defensive: the API may return an object (e.g. 404 error body) if
    // a route is misconfigured — guard against that so the dashboard
    // still renders instead of throwing.
    const asArray = <T,>(v: unknown): T[] => Array.isArray(v) ? (v as T[]) : [];
    const bullData = asArray<TopMoverResult>(bullResult.data);
    const bearData = asArray<TopMoverResult>(bearResult.data);

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
