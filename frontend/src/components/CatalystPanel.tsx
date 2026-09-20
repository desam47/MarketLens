import React, { useEffect, useState } from 'react';
import api, { FundamentalsItem, NewsItem, OptionsChain } from '../services/api';
import { formatETDateTime } from './chartMath';

interface CatalystPanelProps {
  symbol: string;
}

interface CatalystState {
  news: NewsItem[];
  fundamentals: FundamentalsItem | null;
  options: OptionsChain | null;
  providers: string[];
  errors: string[];
}

function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`;
}

function money(value: number | null | undefined): string {
  if (value == null) return '—';
  if (Math.abs(value) >= 1e12) return `$${(value / 1e12).toFixed(1)}T`;
  if (Math.abs(value) >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  if (Math.abs(value) >= 1e6) return `$${(value / 1e6).toFixed(1)}M`;
  return `$${value.toFixed(0)}`;
}

function recommendationColor(value: string | null): string {
  if (!value) return 'var(--text-muted)';
  if (value.toLowerCase().includes('buy')) return 'var(--success)';
  if (value.toLowerCase().includes('sell')) return 'var(--danger)';
  return 'var(--warning)';
}

export function CatalystPanel({ symbol }: CatalystPanelProps) {
  const [state, setState] = useState<CatalystState>({
    news: [], fundamentals: null, options: null, providers: [], errors: [],
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.allSettled([
      api.getNews(symbol, 5),
      api.getFundamentals(symbol),
      api.getOptions(symbol),
    ]).then(results => {
      if (cancelled) return;
      const [news, fundamentals, options] = results;
      const errors: string[] = [];
      const providers: string[] = [];
      const newsData = news.status === 'fulfilled' && news.value.provider !== 'disabled' ? news.value : null;
      const fundamentalsData = fundamentals.status === 'fulfilled' && fundamentals.value.provider !== 'disabled' ? fundamentals.value : null;
      const optionsData = options.status === 'fulfilled' && options.value.provider !== 'disabled' ? options.value : null;
      if (newsData?.provider) providers.push(`News: ${newsData.provider}`);
      if (fundamentalsData?.provider) providers.push(`Fundamentals: ${fundamentalsData.provider}`);
      if (optionsData?.provider) providers.push(`Options: ${optionsData.provider}`);
      if (news.status === 'rejected') errors.push('News unavailable');
      if (fundamentals.status === 'rejected') errors.push('Fundamentals unavailable');
      if (options.status === 'rejected') errors.push('Options unavailable');
      setState({
        news: newsData?.items || [],
        fundamentals: fundamentalsData?.data || null,
        options: optionsData?.chains?.[0] || null,
        providers,
        errors,
      });
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [symbol]);

  if (loading) return <div className="card analysis-card"><p className="empty-state">Loading catalysts…</p></div>;

  const { fundamentals, options } = state;
  return (
    <div className="card analysis-card catalyst-card">
      <div className="card-header-row">
        <div>
          <h2>⚡ Catalyst Snapshot</h2>
          <p className="panel-caveat">Provider-backed context that may explain the next move.</p>
        </div>
        <span className="provider-badge">{state.providers.length} sources</span>
      </div>

      <div className="catalyst-grid">
        <div className="catalyst-column">
          <h3>Latest news</h3>
          {state.news.length === 0 ? <p className="empty-state">No recent news</p> : state.news.slice(0, 3).map((item, index) => (
            <a className="catalyst-news-item" href={item.url || undefined} target="_blank" rel="noopener noreferrer" key={`${item.timestamp}-${index}`}>
              <span>{item.headline}</span>
              <small>{item.source} · {formatETDateTime(item.timestamp)}</small>
            </a>
          ))}
        </div>

        <div className="catalyst-column">
          <h3>Fundamentals</h3>
          {fundamentals ? (
            <div className="catalyst-stats">
              <span>Recommendation <b style={{ color: recommendationColor(fundamentals.recommendation) }}>{fundamentals.recommendation || '—'}</b></span>
              <span>Market cap <b>{money(fundamentals.market_cap)}</b></span>
              <span>P/E <b>{fundamentals.pe_ratio?.toFixed(1) || '—'}</b></span>
              <span>Analyst target <b>{money(fundamentals.analyst_target)}</b></span>
              <span>Insider ownership <b>{pct(fundamentals.insider_ownership)}</b></span>
            </div>
          ) : <p className="empty-state">No fundamentals available</p>}
        </div>

        <div className="catalyst-column">
          <h3>Options activity</h3>
          {options ? (
            <div className="catalyst-stats">
              <span>Expiration <b>{options.expiration}</b></span>
              <span>Put / call <b>{options.put_call_ratio?.toFixed(2) || '—'}</b></span>
              <span>Call volume <b>{options.total_call_volume?.toLocaleString() || '—'}</b></span>
              <span>Put volume <b>{options.total_put_volume?.toLocaleString() || '—'}</b></span>
              <span>Activity <b className={`activity-${options.unusual_activity}`}>{options.unusual_activity}</b></span>
            </div>
          ) : <p className="empty-state">No options data available</p>}
        </div>
      </div>
      {(state.errors.length > 0 || state.providers.length > 0) && (
        <p className="panel-caveat">{state.errors.join(' · ')}{state.errors.length > 0 && state.providers.length > 0 ? ' · ' : ''}{state.providers.join(' · ')}</p>
      )}
    </div>
  );
}

export default CatalystPanel;
