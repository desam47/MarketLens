import React, { useState, useEffect } from 'react';
import api, { FundamentalsItem } from '../services/api';

interface FundamentalsPanelProps {
  symbol: string;
}

function fmtMoney(v: number | null, prefix = '$'): string {
  if (v == null) return '—';
  if (Math.abs(v) >= 1e12) return `${prefix}${(v / 1e12).toFixed(2)}T`;
  if (Math.abs(v) >= 1e9)  return `${prefix}${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e6)  return `${prefix}${(v / 1e6).toFixed(2)}M`;
  return `${prefix}${v.toFixed(2)}`;
}

function fmtPct(v: number | null, decimals = 2): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(decimals)}%`;
}

function fmtNum(v: number | null, decimals = 2): string {
  if (v == null) return '—';
  return v.toFixed(decimals);
}

function recColor(rec: string | null): string {
  if (!rec) return '#6b7280';
  if (rec.includes('buy'))  return '#10b981';
  if (rec.includes('sell')) return '#ef4444';
  return '#f59e0b';  // hold
}

function StatRow({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div className="fundamentals-row">
      <span className="fundamentals-label">{label}</span>
      <span className="fundamentals-value" style={valueColor ? { color: valueColor } : undefined}>{value}</span>
    </div>
  );
}

export function FundamentalsPanel({ symbol }: FundamentalsPanelProps) {
  const [data, setData] = useState<FundamentalsItem | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    setProvider(null);
    api.getFundamentals(symbol)
      .then(res => {
        if (cancelled) return;
        if (res.provider === 'disabled') {
          setError('Fundamentals disabled — set AUX_FUNDAMENTALS_ENABLED=true.');
        } else if (res.provider === 'none') {
          setError('No fundamentals provider available');
        } else {
          setData(res.data);
          setProvider(res.provider);
        }
      })
      .catch((e: any) => {
        if (!cancelled) setError(e?.message || 'Failed to load fundamentals');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [symbol]);

  if (loading) return <div className="card analysis-card"><p className="empty-state">Loading fundamentals…</p></div>;
  if (error || !data) {
    return (
      <div className="card analysis-card">
        <h2>💼 Fundamentals</h2>
        <p className="empty-state" style={{ color: '#9ca3af' }}>{error || 'No data'}</p>
      </div>
    );
  }

  const range = (data.week_52_low && data.week_52_high)
    ? `${fmtMoney(data.week_52_low)} – ${fmtMoney(data.week_52_high)}`
    : '—';

  return (
    <div className="card analysis-card">
      <div className="card-header-row">
        <h2>💼 {data.company_name || symbol}</h2>
        {provider && <span className="provider-badge">{provider}</span>}
      </div>
      {data.sector && (
        <p className="fundamentals-subtitle">
          {data.sector}{data.industry ? ` · ${data.industry}` : ''}
        </p>
      )}

      <div className="fundamentals-grid">
        <div className="fundamentals-section">
          <h3>Valuation</h3>
          <StatRow label="Market Cap"   value={fmtMoney(data.market_cap)} />
          <StatRow label="P/E (TTM)"    value={fmtNum(data.pe_ratio)} />
          <StatRow label="Fwd P/E"      value={fmtNum(data.forward_pe)} />
          <StatRow label="PEG"          value={fmtNum(data.peg_ratio)} />
          <StatRow label="P/B"          value={fmtNum(data.price_to_book)} />
          <StatRow label="P/S"          value={fmtNum(data.price_to_sales)} />
        </div>

        <div className="fundamentals-section">
          <h3>Income</h3>
          <StatRow label="Revenue"      value={fmtMoney(data.revenue)} />
          <StatRow label="Net Income"   value={fmtMoney(data.net_income)} />
          <StatRow label="EPS (TTM)"    value={fmtNum(data.eps, 2)} />
          <StatRow label="EPS Growth"   value={fmtPct(data.eps_growth)} />
        </div>

        <div className="fundamentals-section">
          <h3>Balance Sheet</h3>
          <StatRow label="Total Debt"   value={fmtMoney(data.total_debt)} />
          <StatRow label="Total Cash"   value={fmtMoney(data.total_cash)} />
          <StatRow label="D/E"          value={fmtNum(data.debt_to_equity)} />
          <StatRow label="Current Ratio" value={fmtNum(data.current_ratio)} />
        </div>

        <div className="fundamentals-section">
          <h3>Ownership &amp; Dividends</h3>
          <StatRow label="Institutional" value={fmtPct(data.institutional_ownership)} />
          <StatRow label="Insider"      value={fmtPct(data.insider_ownership)} />
          <StatRow label="Short Float"  value={fmtPct(data.short_float)} />
          <StatRow label="Div Yield"    value={fmtPct(data.dividend_yield, 2)} />
          <StatRow label="Payout"       value={fmtPct(data.payout_ratio)} />
        </div>

        <div className="fundamentals-section">
          <h3>Price &amp; Analyst</h3>
          <StatRow label="52-Wk Range"  value={range} />
          <StatRow label="Beta"         value={fmtNum(data.beta)} />
          <StatRow label="Target"       value={fmtMoney(data.analyst_target)} />
          <StatRow
            label="Recommendation"
            value={data.recommendation || '—'}
            valueColor={recColor(data.recommendation)}
          />
        </div>
      </div>
    </div>
  );
}
