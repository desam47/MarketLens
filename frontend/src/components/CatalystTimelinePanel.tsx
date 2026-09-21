import React, { useEffect, useMemo, useState } from 'react';
import api, {
  FundamentalsItem,
  FundamentalsResponse,
  NewsItem,
  NewsResponse,
  OptionsChain,
  OptionsResponse,
  ScanResult,
} from '../services/api';
import { formatETDateTime } from './chartMath';

interface CatalystTimelinePanelProps {
  symbol: string;
  scanResult?: ScanResult | null;
}

interface CatalystEvent {
  id: string;
  kind: 'price' | 'news' | 'fundamentals' | 'options';
  timestamp: string;
  title: string;
  detail: string;
  source: string;
  tone: 'bullish' | 'bearish' | 'neutral';
  url?: string | null;
  impact: 'high' | 'medium' | 'low';
}

interface CatalystState {
  news: NewsResponse | null;
  fundamentals: FundamentalsResponse | null;
  options: OptionsResponse | null;
  errors: string[];
}

const toneColors: Record<string, string> = {
  bullish: '#10b981',
  bearish: '#ef4444',
  neutral: '#f59e0b',
};

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

function recommendationTone(recommendation: string | null | undefined): 'bullish' | 'bearish' | 'neutral' {
  const value = recommendation?.toLowerCase() || '';
  if (value.includes('buy')) return 'bullish';
  if (value.includes('sell')) return 'bearish';
  return 'neutral';
}

function optionsTone(chain: OptionsChain | null): 'bullish' | 'bearish' | 'neutral' {
  if (!chain) return 'neutral';
  if (chain.unusual_activity === 'unusual' || chain.unusual_activity === 'high') {
    return (chain.put_call_ratio ?? 1) >= 1.2 ? 'bearish' : 'bullish';
  }
  return 'neutral';
}

function fundamentalsDetail(data: FundamentalsItem): string {
  const parts = [
    data.recommendation ? `Analyst consensus: ${data.recommendation.replace(/_/g, ' ')}` : null,
    data.analyst_target != null ? `Target ${money(data.analyst_target)}` : null,
    data.insider_ownership != null ? `Insider ownership ${pct(data.insider_ownership)}` : null,
    data.market_cap != null ? `Market cap ${money(data.market_cap)}` : null,
  ];
  return parts.filter(Boolean).join(' · ') || 'Fundamental snapshot available.';
}

function optionsDetail(response: OptionsResponse, chain: OptionsChain | null): string {
  if (!chain) return 'No current options chain returned.';
  const parts = [
    `Expiry ${chain.expiration}`,
    chain.put_call_ratio != null ? `P/C ${chain.put_call_ratio.toFixed(2)}` : null,
    response.near_term_iv != null ? `Near-term IV ${pct(response.near_term_iv)}` : null,
    `Activity ${chain.unusual_activity}`,
  ];
  return parts.filter(Boolean).join(' · ');
}

function buildEvents(
  scanResult: ScanResult | null | undefined,
  state: CatalystState,
): CatalystEvent[] {
  const events: CatalystEvent[] = [];
  if (scanResult) {
    const score = scanResult.total_score;
    const signalText = scanResult.signals.length ? scanResult.signals.slice(0, 3).join(', ').replace(/_/g, ' ') : 'No named scanner signal';
    events.push({
      id: `price:${scanResult.timestamp}`,
      kind: 'price',
      timestamp: scanResult.timestamp,
      title: `Technical reaction ${score >= 0 ? 'bullish' : 'bearish'}`,
      detail: `${scanResult.change_pct == null ? 'Change unavailable' : `${scanResult.change_pct >= 0 ? '+' : ''}${scanResult.change_pct.toFixed(2)}%`} · ${signalText}`,
      source: scanResult.quote?.provider || 'Market scanner',
      tone: score > 5 ? 'bullish' : score < -5 ? 'bearish' : 'neutral',
      impact: Math.abs(score) >= 40 ? 'high' : 'medium',
    });
  }

  state.news?.items.forEach((item: NewsItem, index) => {
    events.push({
      id: `news:${item.timestamp}:${index}`,
      kind: 'news',
      timestamp: item.timestamp,
      title: item.headline,
      detail: `${item.source} · ${(item.relevance * 100).toFixed(0)}% relevance`,
      source: item.source,
      tone: 'neutral',
      impact: item.relevance >= 0.8 ? 'high' : item.relevance >= 0.5 ? 'medium' : 'low',
      url: item.url,
    });
  });

  const fundamentals = state.fundamentals?.data;
  if (fundamentals && state.fundamentals) {
    const tone = recommendationTone(fundamentals.recommendation);
    events.push({
      id: `fundamentals:${state.fundamentals.timestamp}`,
      kind: 'fundamentals',
      timestamp: state.fundamentals.timestamp,
      title: `Fundamental snapshot${fundamentals.sector ? ` · ${fundamentals.sector}` : ''}`,
      detail: fundamentalsDetail(fundamentals),
      source: state.fundamentals.provider,
      tone,
      impact: tone === 'neutral' ? 'low' : 'medium',
    });
  }

  const options = state.options;
  const chain = options?.chains?.[0] || null;
  if (options && chain) {
    const tone = optionsTone(chain);
    events.push({
      id: `options:${options.timestamp}`,
      kind: 'options',
      timestamp: options.timestamp,
      title: `Options activity · ${chain.unusual_activity}`,
      detail: optionsDetail(options, chain),
      source: options.provider,
      tone,
      impact: chain.unusual_activity === 'unusual' || chain.unusual_activity === 'high' ? 'high' : 'medium',
    });
  }

  return events.sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp)).slice(0, 12);
}

export function CatalystTimelinePanel({ symbol, scanResult }: CatalystTimelinePanelProps) {
  const [state, setState] = useState<CatalystState>({ news: null, fundamentals: null, options: null, errors: [] });
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setUpdatedAt(null);
    setState({ news: null, fundamentals: null, options: null, errors: [] });
    Promise.allSettled([
      api.getNews(symbol, 10),
      api.getFundamentals(symbol),
      api.getOptions(symbol),
    ]).then(results => {
      if (cancelled) return;
      const [news, fundamentals, options] = results;
      const errors: string[] = [];
      const newsData = news.status === 'fulfilled' && news.value.provider !== 'disabled' && news.value.provider !== 'none' ? news.value : null;
      const fundamentalsData = fundamentals.status === 'fulfilled' && fundamentals.value.provider !== 'disabled' && fundamentals.value.provider !== 'none' ? fundamentals.value : null;
      const optionsData = options.status === 'fulfilled' && options.value.provider !== 'disabled' && options.value.provider !== 'none' ? options.value : null;
      if (news.status === 'rejected') errors.push('News unavailable');
      if (fundamentals.status === 'rejected') errors.push('Fundamentals unavailable');
      if (options.status === 'rejected') errors.push('Options unavailable');
      setState({ news: newsData, fundamentals: fundamentalsData, options: optionsData, errors });
      setUpdatedAt(new Date().toISOString());
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [symbol]);

  const events = useMemo(() => buildEvents(scanResult, state), [scanResult, state]);

  return (
    <div className="card analysis-card catalyst-timeline-card">
      <div className="card-header-row">
        <div>
          <h2>⚡ Catalyst Snapshot</h2>
          <p className="panel-caveat">News, fundamentals, options, and price reaction in one time-ordered view.</p>
        </div>
        <span className="symbol-tag">{symbol}</span>
      </div>
      {loading ? <p className="empty-state">Loading catalyst sources…</p> : events.length === 0 ? (
        <p className="empty-state">No catalyst data is available from the configured providers.</p>
      ) : (
        <div className="catalyst-timeline" aria-label={`${symbol} catalyst timeline`}>
          {events.map(event => {
            const content = (
              <>
                <div className="catalyst-event-topline">
                  <span className="catalyst-event-kind">{event.kind}</span>
                  <span className={`catalyst-event-impact impact-${event.impact}`}>{event.impact} impact</span>
                  <time>{formatETDateTime(event.timestamp)}</time>
                </div>
                <strong>{event.title}</strong>
                <p>{event.detail}</p>
                <small>{event.source}</small>
              </>
            );
            return event.url ? (
              <a className="catalyst-event" href={event.url} target="_blank" rel="noopener noreferrer" key={event.id} style={{ borderLeftColor: toneColors[event.tone] }}>
                {content}
              </a>
            ) : (
              <div className="catalyst-event" key={event.id} style={{ borderLeftColor: toneColors[event.tone] }}>
                {content}
              </div>
            );
          })}
        </div>
      )}
      {state.errors.length > 0 && <p className="panel-caveat">{state.errors.join(' · ')}</p>}
      {!loading && <p className="panel-caveat">Provider timestamps may be delayed; options and fundamentals are snapshots, not live quotes.</p>}
      {updatedAt && <p className="panel-caveat">Panel refreshed {formatETDateTime(updatedAt)}</p>}
    </div>
  );
}

export default CatalystTimelinePanel;
