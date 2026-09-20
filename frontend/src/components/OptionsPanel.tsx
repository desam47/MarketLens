import React, { useState, useEffect, useMemo } from 'react';
import api, { OptionsChain } from '../services/api';

interface OptionsPanelProps {
  symbol: string;
}

const unusualColor: Record<string, string> = {
  unusual: '#dc2626',
  high: '#f97316',
  elevated: '#f59e0b',
  normal: '#6b7280',
};

function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v == null) return '—';
  return v.toFixed(decimals);
}

function fmtPct(v: number | null | undefined, decimals = 1): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(decimals)}%`;
}

function fmtInt(v: number | null | undefined): string {
  if (v == null) return '—';
  return v.toLocaleString();
}

function nearestExpiration(chains: OptionsChain[]): OptionsChain | null {
  if (!chains.length) return null;
  return [...chains].sort((a, b) => a.expiration.localeCompare(b.expiration))[0];
}

export function OptionsPanel({ symbol }: OptionsPanelProps) {
  const [chains, setChains] = useState<OptionsChain[]>([]);
  const [expirations, setExpirations] = useState<string[]>([]);
  const [nearTermIv, setNearTermIv] = useState<number | null>(null);
  const [ivRank, setIvRank] = useState<number | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const [selectedExp, setSelectedExp] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setChains([]);
    setExpirations([]);
    setNearTermIv(null);
    setIvRank(null);
    setProvider(null);
    setSelectedExp(null);
    api.getOptions(symbol)
      .then(res => {
        if (cancelled) return;
        if (res.provider === 'disabled') {
          setError('Options disabled — set AUX_OPTIONS_ENABLED=true.');
        } else if (res.provider === 'none' || !res.chains?.length) {
          setError(res.provider === 'none' ? 'No options provider available' : 'No options chains returned');
        } else {
          setChains(res.chains);
          setExpirations(res.expirations);
          setNearTermIv(res.near_term_iv);
          setIvRank(res.iv_rank);
          setProvider(res.provider);
        }
      })
      .catch((e: any) => {
        if (!cancelled) setError(e?.message || 'Failed to load options');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [symbol]);

  const activeChain = useMemo(() => {
    if (!chains.length) return null;
    if (selectedExp) return chains.find(c => c.expiration === selectedExp) || null;
    return nearestExpiration(chains);
  }, [chains, selectedExp]);

  if (loading) return <div className="card analysis-card"><p className="empty-state">Loading options…</p></div>;
  if (error) {
    return (
      <div className="card analysis-card">
        <h2>📊 Options</h2>
        <p className="empty-state" style={{ color: '#9ca3af' }}>{error}</p>
      </div>
    );
  }

  if (!activeChain) return null;

  // Center the chain around the strike nearest to (last *1.0) — fallback to median
  const allStrikes = [...activeChain.calls, ...activeChain.puts]
    .map(c => c.strike)
    .filter(s => s > 0);
  const center = allStrikes.length
    ? allStrikes.sort((a, b) => a - b)[Math.floor(allStrikes.length / 2)]
    : 0;
  const window = center * 0.1 || 5;

  const calls = activeChain.calls
    .filter(c => Math.abs(c.strike - center) <= window)
    .sort((a, b) => b.strike - a.strike);
  const puts = activeChain.puts
    .filter(c => Math.abs(c.strike - center) <= window)
    .sort((a, b) => a.strike - b.strike);

  return (
    <div className="card analysis-card">
      <div className="card-header-row">
        <h2>📊 Options Chain</h2>
        {provider && <span className="provider-badge">{provider}</span>}
      </div>

      <div className="options-summary">
        <div className="options-stat">
          <span className="options-stat-label">Near-term IV</span>
          <span className="options-stat-value">{fmtPct(nearTermIv)}</span>
        </div>
        <div className="options-stat">
          <span className="options-stat-label">IV Rank</span>
          <span className="options-stat-value">{ivRank != null ? ivRank.toFixed(1) : '—'}</span>
        </div>
        <div className="options-stat">
          <span className="options-stat-label">P/C Ratio</span>
          <span className="options-stat-value">{fmtNum(activeChain.put_call_ratio, 3)}</span>
        </div>
        <div className="options-stat">
          <span className="options-stat-label">Activity</span>
          <span
            className="options-stat-value"
            style={{ color: unusualColor[activeChain.unusual_activity] || '#6b7280' }}
          >
            {activeChain.unusual_activity}
          </span>
        </div>
      </div>

      <div className="options-controls">
        <label htmlFor="exp-select" style={{ fontSize: 12, color: '#9ca3af' }}>Expiration:</label>
        <select
          id="exp-select"
          className="timeframe-select"
          value={activeChain.expiration}
          onChange={e => setSelectedExp(e.target.value)}
        >
          {expirations.map(exp => (
            <option key={exp} value={exp}>{exp}</option>
          ))}
        </select>
      </div>

      <div className="options-chains">
        <table className="options-table">
          <thead>
            <tr>
              <th colSpan={4} style={{ color: '#10b981' }}>Calls</th>
              <th style={{ borderLeft: '1px solid #374151', borderRight: '1px solid #374151' }}>Strike</th>
              <th colSpan={3} style={{ color: '#ef4444' }}>Puts</th>
            </tr>
            <tr>
              <th>Bid</th><th>Ask</th><th>Vol</th><th>OI</th>
              <th style={{ borderLeft: '1px solid #374151', borderRight: '1px solid #374151' }}>$</th>
              <th>Bid</th><th>Ask</th><th>Vol</th>
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: Math.max(calls.length, puts.length) }, (_, i) => {
              const c = calls[i];
              const p = puts[i];
              return (
                <tr key={i}>
                  {c ? (
                    <>
                      <td>{fmtNum(c.bid)}</td>
                      <td>{fmtNum(c.ask)}</td>
                      <td>{fmtInt(c.volume)}</td>
                      <td>{fmtInt(c.open_interest)}</td>
                    </>
                  ) : (
                    <><td colSpan={4}>—</td></>
                  )}
                  <td style={{ borderLeft: '1px solid #374151', borderRight: '1px solid #374151', textAlign: 'center' }}>
                    {c?.strike ?? p?.strike ?? '—'}
                  </td>
                  {p ? (
                    <>
                      <td>{fmtNum(p.bid)}</td>
                      <td>{fmtNum(p.ask)}</td>
                      <td>{fmtInt(p.volume)}</td>
                    </>
                  ) : (
                    <><td colSpan={3}>—</td></>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
