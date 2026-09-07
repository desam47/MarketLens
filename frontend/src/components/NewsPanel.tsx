import React, { useState, useEffect } from 'react';
import api, { NewsItem } from '../services/api';
import { parseET } from './chartMath';

interface NewsPanelProps {
  symbol: string;
}

const unusualColors: Record<string, string> = {
  yahoo_finance: '#7c3aed',
};

function formatTs(ts: string | null | undefined): string {
  if (!ts) return '—';
  try {
    return parseET(ts).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return ts;
  }
}

function RelevanceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? '#10b981' : pct >= 50 ? '#f59e0b' : '#6b7280';
  return (
    <div title={`${pct}% related`} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ flex: 1, height: 4, background: '#1f2937', borderRadius: 2 }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 2, transition: 'width .2s' }} />
      </div>
      <span style={{ fontSize: 11, color: '#9ca3af', minWidth: 28 }}>{pct}%</span>
    </div>
  );
}

export function NewsPanel({ symbol }: NewsPanelProps) {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [provider, setProvider] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.getNews(symbol, 20)
      .then(res => {
        if (res.provider === 'disabled') {
          setError('News disabled — set AUX_NEWS_ENABLED=true to enable.');
          setItems([]);
        } else {
          setItems(res.items || []);
          setProvider(res.provider);
        }
      })
      .catch((e: any) => setError(e?.message || 'Failed to load news'))
      .finally(() => setLoading(false));
  }, [symbol]);

  if (loading) return <div className="card analysis-card"><p className="empty-state">Loading news…</p></div>;

  if (error || provider === 'disabled') {
    return (
      <div className="card analysis-card">
        <h2>📰 News</h2>
        <p className="empty-state" style={{ color: '#9ca3af' }}>
          {error || 'News provider unavailable'}
        </p>
      </div>
    );
  }

  return (
    <div className="card analysis-card">
      <div className="card-header-row">
        <h2>📰 News</h2>
        {provider && <span className="provider-badge" style={{ color: unusualColors[provider] || '#6b7280' }}>{provider}</span>}
      </div>
      {items.length === 0 ? (
        <p className="empty-state">No recent news</p>
      ) : (
        <div className="news-list">
          {items.map((item, i) => (
            <div key={i} className="news-item">
              <div className="news-header-row">
                <span className="news-source">{item.source}</span>
                <span className="news-time">{formatTs(item.timestamp)}</span>
              </div>
              <button
                style={{ background: 'none', border: 'none', padding: 0, textAlign: 'left', cursor: 'pointer', font: 'inherit', color: 'inherit' }}
                className="news-headline"
                onClick={e => { e.preventDefault(); }}
                title={item.headline}
              >
                {item.headline.length > 120 ? item.headline.slice(0, 117) + '…' : item.headline}
              </button>
              <RelevanceBar value={item.relevance} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
