import React, { useState, useEffect, useMemo, useRef } from 'react';
import api, { OptionsChain } from '../services/api';
import { formatETDateTime } from './chartMath';

interface OptionsPanelProps {
  symbol: string;
  underlyingPrice?: number | null;
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

function total(values: Array<number | null | undefined>): number {
  return values.reduce<number>((sum, value) => sum + (value ?? 0), 0);
}

function daysToExpiration(expiration: string): number {
  const expiry = Date.parse(`${expiration}T16:00:00`);
  if (!Number.isFinite(expiry)) return 0;
  return Math.max(1, Math.ceil((expiry - Date.now()) / 86400000));
}

export function OptionsPanel({ symbol, underlyingPrice = null }: OptionsPanelProps) {
  const [chains, setChains] = useState<OptionsChain[]>([]);
  const [expirations, setExpirations] = useState<string[]>([]);
  const [nearTermIv, setNearTermIv] = useState<number | null>(null);
  const [ivRank, setIvRank] = useState<number | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const [selectedExp, setSelectedExp] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    setUpdatedAt(null);
    setChains([]);
    setExpirations([]);
    setNearTermIv(null);
    setIvRank(null);
    setProvider(null);
    setSelectedExp(null);
    api.getOptions(symbol)
      .then(res => {
        if (cancelled || requestId !== requestIdRef.current) return;
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
          setUpdatedAt(res.timestamp);
        }
      })
      .catch((e: any) => {
        if (!cancelled && requestId === requestIdRef.current) {
          setError(e?.message || 'Failed to load options');
        }
      })
      .finally(() => {
        if (!cancelled && requestId === requestIdRef.current) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [symbol]);

  const handleExpirationChange = (expiration: string) => {
    setSelectedExp(expiration);
    if (chains.some(chain => chain.expiration === expiration)) return;

    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    api.getOptions(symbol, expiration)
      .then(response => {
        if (requestId !== requestIdRef.current) return;
        if (response.provider === 'disabled') {
          setError('Options disabled — set AUX_OPTIONS_ENABLED=true.');
          return;
        }
        const selectedChain = response.chains.find(chain => chain.expiration === expiration);
        if (!selectedChain) {
          setError('No options chain returned for the selected expiration.');
          return;
        }
        setChains(previous => [
          ...previous.filter(chain => chain.expiration !== expiration),
          selectedChain,
        ].sort((a, b) => a.expiration.localeCompare(b.expiration)));
        setExpirations(response.expirations);
        setNearTermIv(response.near_term_iv);
        setIvRank(response.iv_rank);
        setProvider(response.provider);
        setUpdatedAt(response.timestamp);
      })
      .catch((e: any) => {
        if (requestId === requestIdRef.current) {
          setError(e?.message || 'Failed to load the selected options expiration');
        }
      })
      .finally(() => {
        if (requestId === requestIdRef.current) setLoading(false);
      });
  };

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

  // Show the same number of strikes below and above the underlying price.
  // This keeps ITM and OTM coverage balanced for both calls and puts.
  const allStrikes = [...activeChain.calls, ...activeChain.puts]
    .map(c => c.strike)
    .filter(s => s > 0)
    .filter((strike, index, strikes) => strikes.indexOf(strike) === index)
    .sort((a, b) => a - b);
  const referencePrice = underlyingPrice ?? (allStrikes.length
    ? allStrikes[Math.floor(allStrikes.length / 2)]
    : 0);
  const lowerStrikes = allStrikes.filter(strike => strike < referencePrice);
  const upperStrikes = allStrikes.filter(strike => strike > referencePrice);
  const strikesPerSide = Math.min(7, lowerStrikes.length, upperStrikes.length);
  const visibleStrikes = strikesPerSide > 0
    ? [
      ...lowerStrikes.slice(-strikesPerSide),
      ...upperStrikes.slice(0, strikesPerSide),
    ]
    : allStrikes
      .slice()
      .sort((a, b) => Math.abs(a - referencePrice) - Math.abs(b - referencePrice))
      .slice(0, 10)
      .sort((a, b) => a - b);
  const visibleStrikeSet = new Set(visibleStrikes);
  const atmStrike = underlyingPrice == null || allStrikes.length === 0
    ? null
    : allStrikes.reduce((closest, strike) => (
      Math.abs(strike - underlyingPrice) < Math.abs(closest - underlyingPrice) ? strike : closest
    ));

  const calls = activeChain.calls
    .filter(c => visibleStrikeSet.has(c.strike));
  const puts = activeChain.puts
    .filter(c => visibleStrikeSet.has(c.strike));
  const callsByStrike = new Map(calls.map(contract => [contract.strike, contract]));
  const putsByStrike = new Map(puts.map(contract => [contract.strike, contract]));
  const strikes = Array.from(new Set([...calls.map(contract => contract.strike), ...puts.map(contract => contract.strike)]))
    .sort((a, b) => a - b);

  const selectedIv = activeChain.avg_iv_call != null && activeChain.avg_iv_put != null
    ? (activeChain.avg_iv_call + activeChain.avg_iv_put) / 2
    : activeChain.avg_iv_call ?? activeChain.avg_iv_put ?? nearTermIv;
  const expectedMove = underlyingPrice != null && selectedIv != null
    ? underlyingPrice * selectedIv * Math.sqrt(daysToExpiration(activeChain.expiration) / 365)
    : null;
  const callVolume = total(activeChain.calls.map(contract => contract.volume));
  const putVolume = total(activeChain.puts.map(contract => contract.volume));
  const callOpenInterest = total(activeChain.calls.map(contract => contract.open_interest));
  const putOpenInterest = total(activeChain.puts.map(contract => contract.open_interest));
  const activityFlags: string[] = [];
  if (activeChain.unusual_activity !== 'normal') activityFlags.push(`${activeChain.unusual_activity} provider activity`);
  if (callVolume > 0 && putVolume / callVolume >= 1.5) activityFlags.push('Put volume elevated');
  if (putVolume > 0 && callVolume / putVolume >= 1.5) activityFlags.push('Call volume elevated');
  if (callOpenInterest > 0 && putOpenInterest / callOpenInterest >= 1.5) activityFlags.push('Put open interest elevated');
  if (putOpenInterest > 0 && callOpenInterest / putOpenInterest >= 1.5) activityFlags.push('Call open interest elevated');

  return (
    <div className="card analysis-card">
      <div className="card-header-row">
        <h2>📊 Options Snapshot</h2>
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
        <div className="options-stat">
          <span className="options-stat-label">Expected Move</span>
          <span className="options-stat-value">{expectedMove == null ? '—' : `±$${expectedMove.toFixed(2)}`}</span>
        </div>
      </div>

      <p className="options-caveat">Expected move is an IV-based estimate for the selected expiry. Options quotes may be delayed.</p>
      {updatedAt && <p className="panel-caveat">Snapshot updated {formatETDateTime(updatedAt)}</p>}
      <div className="options-flags" aria-label="Unusual options activity">
        <span className="options-flags-label">Flags</span>
        {activityFlags.length > 0 ? activityFlags.map(flag => (
          <span className="options-flag" key={flag}>{flag}</span>
        )) : <span className="options-flag options-flag-normal">No unusual flow detected</span>}
      </div>

      <div className="options-controls">
        <label htmlFor="exp-select" style={{ fontSize: 12, color: '#9ca3af' }}>Expiration:</label>
        <select
          id="exp-select"
          className="timeframe-select"
          value={activeChain.expiration}
          onChange={e => handleExpirationChange(e.target.value)}
        >
          {expirations.map(exp => (
            <option key={exp} value={exp}>{exp}</option>
          ))}
        </select>
      </div>

      <div className="options-chains">
        <div className="options-chain-legend" aria-label="Options money-ness legend">
          <span className="options-legend-itm">ITM</span>
          <span className="options-legend-otm">OTM</span>
          {atmStrike != null && <span>ATM ≈ ${fmtNum(atmStrike)}</span>}
        </div>
        <table className="options-table">
          <thead>
            <tr>
              <th colSpan={4} style={{ color: '#10b981' }}>Calls</th>
              <th style={{ borderLeft: '1px solid #374151', borderRight: '1px solid #374151' }}>Strike</th>
              <th colSpan={4} style={{ color: '#ef4444' }}>Puts</th>
            </tr>
            <tr>
              <th>Bid</th><th>Ask</th><th>Vol</th><th>OI</th>
              <th style={{ borderLeft: '1px solid #374151', borderRight: '1px solid #374151' }}>$</th>
              <th>Bid</th><th>Ask</th><th>Vol</th><th>OI</th>
            </tr>
          </thead>
          <tbody>
            {strikes.map(strike => {
              const c = callsByStrike.get(strike);
              const p = putsByStrike.get(strike);
              return (
                <tr key={strike}>
                  {c ? (
                    <>
                      <td className={c.in_the_money ? 'options-contract-itm' : 'options-contract-otm'}>{fmtNum(c.bid)}</td>
                      <td className={c.in_the_money ? 'options-contract-itm' : 'options-contract-otm'}>{fmtNum(c.ask)}</td>
                      <td className={c.in_the_money ? 'options-contract-itm' : 'options-contract-otm'}>{fmtInt(c.volume)}</td>
                      <td className={c.in_the_money ? 'options-contract-itm' : 'options-contract-otm'}>{fmtInt(c.open_interest)}</td>
                    </>
                  ) : (
                    <><td colSpan={4}>—</td></>
                  )}
                  <td data-testid="option-strike" className={strike === atmStrike ? 'options-strike-atm' : undefined} style={{ borderLeft: '1px solid #374151', borderRight: '1px solid #374151', textAlign: 'center' }}>
                    {fmtNum(strike)}{strike === atmStrike && <small>ATM</small>}
                  </td>
                  {p ? (
                    <>
                      <td className={p.in_the_money ? 'options-contract-itm' : 'options-contract-otm'}>{fmtNum(p.bid)}</td>
                      <td className={p.in_the_money ? 'options-contract-itm' : 'options-contract-otm'}>{fmtNum(p.ask)}</td>
                      <td className={p.in_the_money ? 'options-contract-itm' : 'options-contract-otm'}>{fmtInt(p.volume)}</td>
                      <td className={p.in_the_money ? 'options-contract-itm' : 'options-contract-otm'}>{fmtInt(p.open_interest)}</td>
                    </>
                  ) : (
                    <><td colSpan={4}>—</td></>
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
