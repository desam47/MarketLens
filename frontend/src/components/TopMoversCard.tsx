import React, { useEffect, useState, useCallback, useMemo } from 'react';
import api, { LiveQuoteUpdateData, RealtimeConnectionStatus, RealtimeEvent, TopMoverResult } from '../services/api';
import { ErrorBanner } from './ErrorBanner';
import { formatETDateTime } from './chartMath';

interface TopMoversCardProps {
  onSelectSymbol?: (symbol: string) => void;
  // Gates the visibility-regain refetch below — mirrors the "Auto (30s)"
  // toggle the five core Dashboard sections already respect, so turning
  // that off also stops this card's background catch-up fetch.
  autoRefresh?: boolean;
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

function liveChangePct(
  mover: TopMoverResult,
  liveQuotes: Record<string, LiveQuoteUpdateData>,
): number | null {
  const live = liveQuotes[mover.symbol.toUpperCase()];
  const livePrice = live?.price ?? mover.quote?.price ?? null;
  // The scan's change is the captured price minus its prior-close baseline.
  // Reusing that baseline lets the stream update ranking without another API call.
  const baseline = mover.quote?.price != null && mover.change != null
    ? mover.quote.price - mover.change
    : null;
  return livePrice != null && baseline != null && baseline !== 0
    ? ((livePrice - baseline) / baseline) * 100
    : mover.change_pct;
}

function MoverPanel({
  title,
  movers,
  onSelectSymbol,
  variant,
  liveQuotes,
  quoteConnectionStatus,
}: {
  title: string;
  movers: TopMoverResult[];
  onSelectSymbol?: (symbol: string) => void;
  variant: 'bullish' | 'bearish';
  liveQuotes: Record<string, LiveQuoteUpdateData>;
  quoteConnectionStatus: RealtimeConnectionStatus;
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
          const live = quoteConnectionStatus === 'open' ? liveQuotes[m.symbol.toUpperCase()] : undefined;
          const badge = changeBadge(liveChangePct(m, liveQuotes));
          return (
            <div
              key={m.symbol}
              className={`mover-pill ${variant}`}
              onClick={() => onSelectSymbol?.(m.symbol)}
              role={onSelectSymbol ? 'button' : undefined}
              aria-label={onSelectSymbol ? `View ${m.symbol}, ${badge.label} change` : undefined}
              title={live ? `${m.symbol} live quote · ${live.provider}` : `${m.symbol} scan snapshot`}
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
              {live && <span className="mover-live-indicator">LIVE</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function TopMoversCard({ onSelectSymbol, autoRefresh = true }: TopMoversCardProps) {
  const [bullish, setBullish] = useState<TopMoverResult[]>([]);
  const [bearish, setBearish] = useState<TopMoverResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [liveQuotes, setLiveQuotes] = useState<Record<string, LiveQuoteUpdateData>>({});
  const [quoteConnectionStatus, setQuoteConnectionStatus] = useState<RealtimeConnectionStatus>('closed');

  const rankedMovers = useMemo(() => {
    type RankedMover = { mover: TopMoverResult; original: 'bullish' | 'bearish'; change: number | null; index: number };
    const source: RankedMover[] = [
      ...bullish.map((mover, index) => ({ mover, original: 'bullish' as const, change: liveChangePct(mover, liveQuotes), index })),
      ...bearish.map((mover, index) => ({ mover, original: 'bearish' as const, change: liveChangePct(mover, liveQuotes), index })),
    ];
    const compare = (a: RankedMover, b: RankedMover) => {
      const aChange = a.change ?? Number.NEGATIVE_INFINITY;
      const bChange = b.change ?? Number.NEGATIVE_INFINITY;
      return bChange - aChange || a.index - b.index;
    };
    const nextBullish = source
      .filter(item => item.change == null
        ? item.original === 'bullish'
        : item.change >= 0)
      .sort(compare)
      .map(item => item.mover);
    const nextBearish = source
      .filter(item => item.change == null
        ? item.original === 'bearish'
        : item.change < 0)
      .sort((a, b) => (a.change ?? Number.POSITIVE_INFINITY) - (b.change ?? Number.POSITIVE_INFINITY) || a.index - b.index)
      .map(item => item.mover);
    return { bullish: nextBullish, bearish: nextBearish };
  }, [bullish, bearish, liveQuotes]);

  const liveSymbols = useMemo(
    () => Array.from(new Set([...bullish, ...bearish].map(m => m.symbol.toUpperCase()))).sort(),
    [bullish, bearish],
  );
  const liveSymbolsKey = liveSymbols.join(',');

  useEffect(() => {
    if (!liveSymbols.length) return;
    const subscriber = api.createRealtimeSubscriber?.();
    if (!subscriber) return;
    const unsubscribe = subscriber.onEvent((event: RealtimeEvent) => {
      if (event.type !== 'quote_update') return;
      setLiveQuotes(previous => ({ ...previous, [event.symbol.toUpperCase()]: event.data }));
    });
    const unsubscribeStatus = subscriber.onStatus(setQuoteConnectionStatus);
    liveSymbols.forEach(symbol => subscriber.subscribeQuote(symbol));
    return () => {
      unsubscribe();
      unsubscribeStatus();
      liveSymbols.forEach(symbol => subscriber.unsubscribeQuote(symbol));
      subscriber.disconnect();
    };
  // `liveSymbolsKey` is the stable representation of the ranked symbols.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveSymbolsKey]);

  const fetchMovers = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

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
    //
    // A single combined call replaced two getTopMovers() calls (bullish,
    // bearish) that each scanned the same watchlist independently —
    // getTopMoversCombined scans it once on the backend.
    const asArray = <T,>(v: unknown): T[] => Array.isArray(v) ? (v as T[]) : [];
    try {
      const data = await api.getTopMoversCombined(20);
      // Defensive: the API may return an unexpected shape (e.g. a 404
      // error body) if a route is misconfigured — guard against that so
      // the dashboard still renders instead of throwing.
      setBullish(asArray<TopMoverResult>(data?.bullish));
      setBearish(asArray<TopMoverResult>(data?.bearish));
    } catch (e: any) {
      setBullish([]);
      setBearish([]);
      setError(`Top movers unavailable: ${e?.message || 'Request failed'}`);
    }

    setLastUpdated(new Date());
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    fetchMovers();
  }, [fetchMovers]);

  // Browsers throttle setInterval heavily in backgrounded tabs, so a tab
  // left open sits on an increasingly stale mover list until it's
  // refocused — same root cause as the Regime freshness-pill bug fixed
  // 2026-09-16. Refetch quietly (the "refreshing" spinner, not the
  // full loading text) on tab-focus-regain.
  useEffect(() => {
    if (!autoRefresh) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') fetchMovers(true);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [autoRefresh, fetchMovers]);

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
            movers={rankedMovers.bullish}
            onSelectSymbol={onSelectSymbol}
            variant="bullish"
            liveQuotes={liveQuotes}
            quoteConnectionStatus={quoteConnectionStatus}
          />
          <MoverPanel
            title="Top Bearish"
            movers={rankedMovers.bearish}
            onSelectSymbol={onSelectSymbol}
            variant="bearish"
            liveQuotes={liveQuotes}
            quoteConnectionStatus={quoteConnectionStatus}
          />
        </div>
      )}
    </div>
  );
}

export default TopMoversCard;
