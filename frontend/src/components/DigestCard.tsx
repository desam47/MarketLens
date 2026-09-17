/**
 * DigestCard — Version 4 AI feature 2: daily/session AI digest.
 *
 * Self-contained card (fetches its own data) — same template as
 * NLSearchBar/AlertsCard/AIAnalysisPanel: a useState trio
 * (data/loading/error), a useCallback fetch fn, render order
 * error -> empty -> loading -> data.
 *
 * Digest generation itself is schedule-only (backend.ai.digest_service
 * fires it at the configured premarket/close times) — this card only
 * reads the latest one, plus a "Regenerate" button for on-demand
 * testing/refresh via POST /api/ai/digest/generate.
 */
import React, { useCallback, useEffect, useState } from 'react';
import api, { AIDigest } from '../services/api';
import { formatETDateTime } from './chartMath';

type Session = 'premarket' | 'close';

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
  const movers = digest?.payload?.movers;
  const rsiExtremes = digest?.payload?.rsi_extremes || [];
  const mtf = digest?.payload?.mtf_alignment_counts;

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
              className={`digest-tab ${session === 'close' ? 'active' : ''}`}
              onClick={() => setSession('close')}
            >
              Close
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
        </div>
      )}

      {loading && !digest && (
        <p className="info-text">Loading digest…</p>
      )}

      {!loading && !digest && !error && (
        <p className="empty-state">
          No {session} digest yet — it generates automatically at the scheduled time,
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
          </div>

          {digest.narrative && (
            <p className="digest-narrative">{digest.narrative}</p>
          )}

          {movers && (movers.top_bullish.length > 0 || movers.top_bearish.length > 0) && (
            <div className="digest-movers">
              <div className="digest-movers-col">
                <h4>🐂 Top Bullish</h4>
                {movers.top_bullish.length === 0 ? (
                  <p className="info-text">None</p>
                ) : (
                  movers.top_bullish.map(m => (
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
                {movers.top_bearish.length === 0 ? (
                  <p className="info-text">None</p>
                ) : (
                  movers.top_bearish.map(m => (
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
                  {r.symbol} RSI {r.rsi.toFixed(0)} ({r.signal})
                </span>
              ))}
              {mtf && (mtf.bullish > 0 || mtf.bearish > 0) && (
                <span className="info-text">
                  MTF aligned: {mtf.bullish} bullish · {mtf.bearish} bearish
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default DigestCard;
