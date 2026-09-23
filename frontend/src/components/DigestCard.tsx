/**
 * DigestCard — Version 4 AI feature 2: daily/session AI digest.
 *
 * Self-contained card (fetches its own data) — same template as
 * NLSearchBar/AlertsCard/AIAnalysisPanel: a useState trio
 * (data/loading/error), a useCallback fetch fn, render order
 * error -> empty -> loading -> data.
 *
 * Digest generation itself is schedule-only (backend.ai.digest_service
 * fires it at the configured premarket, midday, post-market, and weekly
 * times) — this card only
 * reads the latest one, plus a "Regenerate" button for on-demand
 * testing/refresh via POST /api/ai/digest/generate.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import api, { AIDigest, DigestSession } from '../services/api';
import { formatETDateTime } from './chartMath';

type Session = DigestSession;
const JOURNAL_STORAGE_KEY = 'marketlens.trade.journal';

interface LocalJournalEntry {
  symbol: string;
  status: 'planned' | 'open' | 'closed';
  side: 'long' | 'short';
  entryDate: string;
  exitDate: string | null;
  quantity: number;
  entryPrice: number;
  exitPrice: number | null;
  stopPrice: number | null;
  targetPrice: number | null;
  reviewNotes: string;
  signalContext?: { trendState: string | null } | null;
}

interface JournalReview {
  entries: number;
  closed: number;
  wins: number;
  losses: number;
  winRate: number | null;
  netPnl: number;
  plannedWithStop: number;
  plannedWithTarget: number;
  strongest: Array<{ symbol: string; pnl: number }>;
  weakest: Array<{ symbol: string; pnl: number }>;
  recurringMistakes: string[];
  periodStart: string;
  periodEnd: string;
}

function localWeeklyJournalReview(periodStart?: string, periodEnd?: string): JournalReview | null {
  try {
    const raw = window.localStorage.getItem(JOURNAL_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return null;
    const entries = parsed.filter((entry): entry is LocalJournalEntry => (
      Boolean(entry) && typeof entry === 'object'
      && typeof (entry as LocalJournalEntry).symbol === 'string'
      && typeof (entry as LocalJournalEntry).entryDate === 'string'
      && Number.isFinite((entry as LocalJournalEntry).quantity)
      && Number.isFinite((entry as LocalJournalEntry).entryPrice)
    ));
    // Journal windows follow MarketLens' America/New_York convention even if
    // the browser itself is running in another timezone.
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date());
    const year = Number(parts.find(part => part.type === 'year')?.value);
    const month = Number(parts.find(part => part.type === 'month')?.value);
    const date = Number(parts.find(part => part.type === 'day')?.value);
    const today = new Date(Date.UTC(year, month - 1, date));
    const monday = new Date(today);
    const day = monday.getUTCDay();
    monday.setUTCDate(monday.getUTCDate() - (day === 0 ? 6 : day - 1));
    const start = periodStart?.slice(0, 10) || monday.toISOString().slice(0, 10);
    const end = periodEnd?.slice(0, 10) || today.toISOString().slice(0, 10);
    const week = entries.filter(entry => (
      (entry.entryDate >= start && entry.entryDate <= end)
      || (entry.exitDate != null && entry.exitDate >= start && entry.exitDate <= end)
    ));
    const closed = week.filter(entry => entry.status === 'closed' && entry.exitPrice != null);
    const pnlFor = (entry: LocalJournalEntry) => (
      ((entry.exitPrice as number) - entry.entryPrice) * entry.quantity * (entry.side === 'short' ? -1 : 1)
    );
    const pnls = closed.map(pnlFor);
    const wins = pnls.filter(value => value > 0).length;
    const bySymbol = new Map<string, number>();
    closed.forEach(entry => bySymbol.set(entry.symbol, (bySymbol.get(entry.symbol) || 0) + pnlFor(entry)));
    const ranked = Array.from(bySymbol.entries()).map(([symbol, pnl]) => ({ symbol, pnl })).sort((a, b) => b.pnl - a.pnl);
    const mistakeKeywords = ['chase', 'late', 'oversize', 'over-sized', 'stop', 'revenge', 'fomo', 'early exit', 'sizing'];
    const mistakeCounts = new Map<string, number>();
    week.forEach(entry => {
      const notes = (entry.reviewNotes || '').toLowerCase();
      mistakeKeywords.forEach(keyword => { if (notes.includes(keyword)) mistakeCounts.set(keyword, (mistakeCounts.get(keyword) || 0) + 1); });
    });
    const recurringMistakes = Array.from(mistakeCounts.entries())
      .filter(([, count]) => count > 1)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([keyword, count]) => `${keyword} (${count})`);
    return {
      entries: week.length, closed: closed.length, wins, losses: pnls.length - wins,
      winRate: closed.length ? (wins / closed.length) * 100 : null,
      netPnl: pnls.reduce((sum, value) => sum + value, 0),
      plannedWithStop: week.filter(entry => entry.stopPrice != null).length,
      plannedWithTarget: week.filter(entry => entry.targetPrice != null).length,
      strongest: ranked.slice(0, 3), weakest: ranked.slice(-3).reverse(),
      recurringMistakes, periodStart: start, periodEnd: end,
    };
  } catch {
    return null;
  }
}

function formatMoney(value: number): string {
  return value.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
}

const sessionLabels: Record<Session, string> = {
  premarket: 'Premarket', midday: 'Midday', close: 'Post-market', weekly: 'Weekly',
};

// Same lowercase regime keys/colors as RegimeCard.tsx — kept in sync
// deliberately rather than importing across files for one small map.
const regimeColors: Record<string, string> = {
  risk_on: '#10b981',
  risk_off: '#ef4444',
  neutral: '#f59e0b',
  transition: '#a855f7',
  unknown: '#9ca3af',
};

function changeColor(changePct: number | null | undefined): string {
  if (changePct == null) return '#9ca3af';
  if (changePct > 0) return '#10b981';
  if (changePct < 0) return '#ef4444';
  return '#9ca3af';
}

// Older, already-persisted digests were generated before change_pct
// replaced the old momentum/RSI score field — their stored payload
// still has that shape, so change_pct is undefined for them. Render
// "—" instead of crashing on `.toFixed()` of undefined.
function formatChangePct(changePct: number | null | undefined): string {
  if (changePct == null) return '—';
  return `${changePct > 0 ? '+' : ''}${changePct.toFixed(2)}%`;
}

// Same rationale as formatChangePct — an older/partial digest payload
// can have a missing rsi_extremes[].rsi. Render "—" instead of crashing
// on `.toFixed()` of undefined.
function formatRsi(rsi: number | null | undefined): string {
  return rsi == null ? '—' : rsi.toFixed(0);
}

export function DigestCard() {
  const [session, setSession] = useState<Session>('close');
  const [digest, setDigest] = useState<AIDigest | null>(null);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDigest = useCallback(async (s: Session) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.getLatestDigest(s);
      setDigest(result);
    } catch (e: any) {
      // A 404 ("no digest generated yet") is expected, not an error —
      // the scheduler hasn't fired for this session today yet.
      if (e?.message?.includes('404')) {
        setDigest(null);
      } else {
        setError(e?.message || 'Failed to load digest');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDigest(session);
  }, [session, fetchDigest]);

  const handleRegenerate = async () => {
    setRegenerating(true);
    setError(null);
    try {
      const result = await api.generateDigest(session);
      setDigest(result);
    } catch (e: any) {
      setError(e?.message || 'Failed to generate digest');
    } finally {
      setRegenerating(false);
    }
  };

  const regime = digest?.market_regime;
  const regimeColor = regime ? regimeColors[regime] || regimeColors.unknown : regimeColors.unknown;
  // Default each sub-array independently rather than the whole `movers`
  // object — a partial payload with only one of the two arrays present
  // must not crash on the missing one.
  const topBullish = digest?.payload?.movers?.top_bullish ?? [];
  const topBearish = digest?.payload?.movers?.top_bearish ?? [];
  const rsiExtremes = digest?.payload?.rsi_extremes || [];
  const mtf = digest?.payload?.mtf_alignment_counts;
  const summary = digest?.payload?.summary;
  const journalReview = useMemo(
    () => (session === 'weekly' ? localWeeklyJournalReview(summary?.period_start, summary?.period_end) : null),
    [session, summary?.period_start, summary?.period_end],
  );

  return (
    <div className="card digest-card">
      <div className="digest-header">
        <h2>📰 AI Digest</h2>
        <div className="digest-header-actions">
          <div className="digest-session-tabs">
            <button
              type="button"
              className={`digest-tab ${session === 'premarket' ? 'active' : ''}`}
              onClick={() => setSession('premarket')}
            >
              Premarket
            </button>
            <button
              type="button"
              className={`digest-tab ${session === 'midday' ? 'active' : ''}`}
              onClick={() => setSession('midday')}
            >
              Midday
            </button>
            <button
              type="button"
              className={`digest-tab ${session === 'close' ? 'active' : ''}`}
              onClick={() => setSession('close')}
            >
              Post-market
            </button>
            <button
              type="button"
              className={`digest-tab ${session === 'weekly' ? 'active' : ''}`}
              onClick={() => setSession('weekly')}
            >
              Weekly
            </button>
          </div>
          <button
            type="button"
            className={`btn btn-secondary ${regenerating ? 'btn-loading' : ''}`}
            onClick={handleRegenerate}
            disabled={regenerating}
            title="Generate a fresh digest now, without waiting for the scheduled time"
          >
            {regenerating ? '⟳' : '↻ Regenerate'}
          </button>
        </div>
      </div>

      {error && (
        <div className="digest-error">
          <span>⚠️ {error}</span>
          <button className="btn btn-small data-state-retry" onClick={() => void fetchDigest(session)}>Retry</button>
        </div>
      )}

      {loading && !digest && (
        <p className="info-text">Loading digest…</p>
      )}

      {!loading && !digest && !error && (
        <p className="empty-state">
          No {sessionLabels[session].toLowerCase()} digest yet — it generates automatically at the scheduled time,
          or click Regenerate to create one now.
        </p>
      )}

      {digest && (
        <div className="digest-body">
          <div className="digest-meta">
            {regime && (
              <span className="digest-regime-badge" style={{ backgroundColor: regimeColor }}>
                {regime.replace(/_/g, ' ').toUpperCase()}
              </span>
            )}
            <span className="info-text digest-timestamp">
              {formatETDateTime(digest.generated_at)}
            </span>
            {summary && (
              <span className="info-text digest-timestamp">
                Window: {formatETDateTime(summary.period_start)} → {formatETDateTime(summary.period_end)}
              </span>
            )}
          </div>

          {digest.narrative && (
            <p className="digest-narrative">{digest.narrative}</p>
          )}

          {(topBullish.length > 0 || topBearish.length > 0) && (
            <div className="digest-movers">
              <div className="digest-movers-col">
                <h4>🐂 Top Bullish</h4>
                {topBullish.length === 0 ? (
                  <p className="info-text">None</p>
                ) : (
                  topBullish.map(m => (
                    <div key={m.symbol} className="digest-mover-row">
                      <span className="digest-mover-symbol">{m.symbol}</span>
                      <span className="digest-mover-score" style={{ color: changeColor(m.change_pct) }}>
                        {formatChangePct(m.change_pct)}
                      </span>
                      {m.blurb && <p className="digest-mover-blurb">{m.blurb}</p>}
                    </div>
                  ))
                )}
              </div>
              <div className="digest-movers-col">
                <h4>🐻 Top Bearish</h4>
                {topBearish.length === 0 ? (
                  <p className="info-text">None</p>
                ) : (
                  topBearish.map(m => (
                    <div key={m.symbol} className="digest-mover-row">
                      <span className="digest-mover-symbol">{m.symbol}</span>
                      <span className="digest-mover-score" style={{ color: changeColor(m.change_pct) }}>
                        {formatChangePct(m.change_pct)}
                      </span>
                      {m.blurb && <p className="digest-mover-blurb">{m.blurb}</p>}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {(rsiExtremes.length > 0 || mtf) && (
            <div className="digest-footer-stats">
              {rsiExtremes.slice(0, 6).map(r => (
                <span key={r.symbol} className="signal-chip">
                  {r.symbol} RSI {formatRsi(r.rsi)} ({r.signal})
                </span>
              ))}
              {mtf && (mtf.bullish > 0 || mtf.bearish > 0) && (
                <span className="info-text">
                  MTF aligned: {mtf.bullish} bullish · {mtf.bearish} bearish
                </span>
              )}
            </div>
          )}

          {session === 'weekly' && (
            <section className="digest-weekly-review" aria-label="Weekly journal review">
              <h4>📝 Journal review</h4>
              {!journalReview || journalReview.entries === 0 ? (
                <p className="info-text">No entries from this week in this browser's local Trade Journal.</p>
              ) : (
                <>
                  <div className="digest-footer-stats">
                    <span className="signal-chip">{journalReview.entries} entries · {journalReview.closed} closed</span>
                    <span className="signal-chip">Net P&amp;L {formatMoney(journalReview.netPnl)}</span>
                    <span className="signal-chip">Win rate {journalReview.winRate == null ? '—' : `${journalReview.winRate.toFixed(1)}%`}</span>
                    <span className="signal-chip">Plan coverage: {journalReview.plannedWithStop} stops · {journalReview.plannedWithTarget} targets</span>
                  </div>
                  {(journalReview.strongest.length > 0 || journalReview.weakest.length > 0) && (
                    <p className="info-text">
                      Strongest: {journalReview.strongest.map(item => `${item.symbol} ${formatMoney(item.pnl)}`).join(', ') || '—'} ·
                      Weakest: {journalReview.weakest.map(item => `${item.symbol} ${formatMoney(item.pnl)}`).join(', ') || '—'}
                    </p>
                  )}
                  {journalReview.recurringMistakes.length > 0 && (
                    <p className="info-text">Recurring review themes: {journalReview.recurringMistakes.join(', ')}</p>
                  )}
                  <p className="info-text">Source: this browser's local Trade Journal · not broker-synced.</p>
                </>
              )}
            </section>
          )}
        </div>
      )}
    </div>
  );
}

export default DigestCard;
