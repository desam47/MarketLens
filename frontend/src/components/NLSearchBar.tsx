/**
 * NLSearchBar — Natural-language stock search powered by Phase 17.
 *
 * Renders a search input and a results panel. The input accepts free-form
 * queries ("strongest bullish stocks", "RSI oversold with volume") and
 * translates them through the /api/nl-search endpoint.
 *
 * Placed on the Dashboard and/or Scanner page so users can surface
 * symbols without knowing the underlying filter syntax.
 */
import React, { useCallback, useRef, useState } from 'react';
import api, { NLSearchResultItem } from '../services/api';
import { fmtPrice } from './watchlistUtils';

const strPrice = fmtPrice;

interface NLSearchBarProps {
  onSelectSymbol?: (symbol: string) => void;
  placeholder?: string;
}

const EXAMPLE_QUERIES = [
  'strongest bullish stocks',
  'RSI oversold with volume',
  'bearish 1h trends',
  'strongest momentum',
  'stocks outperforming QQQ',
];

function scoreColor(score: number): string {
  if (score >= 30) return '#10b981';
  if (score <= -30) return '#ef4444';
  return '#9ca3af';
}

function signalChip(sig: string): string {
  // Human-readable labels for the scanner signal names
  const labels: Record<string, string> = {
    RSI_OVERSOLD: 'RSI<30',
    RSI_OVERBOUGHT: 'RSI>70',
    MACD_BULLISH: 'MACD ▲',
    MACD_BEARISH: 'MACD ▼',
    MULTI_TIMEFRAME_BULLISH: 'MTF ▲',
    MULTI_TIMEFRAME_BEARISH: 'MTF ▼',
    HIGH_VOLUME: 'High Vol',
  };
  return labels[sig] || sig.replace(/_/g, ' ');
}

function trendLabel(directions: Record<string, string>): string {
  const entries = Object.entries(directions);
  if (entries.length === 0) return '';
  return entries
    .map(([tf, dir]) => `${tf}:${dir === 'uptrend' ? '▲' : dir === 'downtrend' ? '▼' : '→'}`)
    .join(' ');
}

function ResultRow({
  item,
  onClick,
}: {
  item: NLSearchResultItem;
  onClick: () => void;
}) {
  const color = scoreColor(item.total_score);
  const sign = item.total_score > 0 ? '+' : '';
  return (
    <tr className="nl-result-row" onClick={onClick}>
      <td className="nl-symbol">{item.symbol}</td>
      <td className="nl-score" style={{ color }}>
        {sign}{item.total_score.toFixed(1)}
      </td>
      <td className="nl-trends">
        {trendLabel(item.trend_directions) || '—'}
      </td>
      <td className="nl-signals">
        {item.signals.length > 0
          ? item.signals.slice(0, 3).map(s => (
              <span key={s} className="signal-chip">{signalChip(s)}</span>
            ))
          : <span className="info-text">—</span>
        }
      </td>
      <td className="nl-rsi">
        {item.rsi != null ? item.rsi.toFixed(1) : '—'}
      </td>
      {item.price != null && (
        <td className="nl-price">${strPrice(item.price)}</td>
      )}
    </tr>
  );
}

export function NLSearchBar({ onSelectSymbol, placeholder = 'e.g. strongest bullish stocks…' }: NLSearchBarProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<NLSearchResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);
  const [meta, setMeta] = useState<{
    filter_description: string;
    parser_used: string;
    ai_translation_used: boolean;
    ai_explanation_used: boolean;
    explanation: string | null;
    reason: string | null;
  } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const search = useCallback(async (q: string) => {
    if (!q.trim()) return;
    if (abortRef.current) abortRef.current.abort();
    abortRef.current = new AbortController();

    setLoading(true);
    setError(null);
    setSearched(true);
    setResults([]);
    setMeta(null);

    try {
      // Scope is always "watchlist" — the "Market" option (removed
      // 2026-09-09) never actually searched a broader market universe.
      // There's no concept of one anywhere in this app: every scan,
      // everywhere, only ever touches watchlist symbols, so "Market"
      // just showed whatever happened to be in the scanner's cache —
      // indistinguishable from Watchlist in practice. Removed rather
      // than keep a dropdown option that didn't do anything real.
      const resp = await api.nlSearch({
        query: q.trim(),
        explain: true,
        top_n: 15,
      });
      setResults(resp.results);
      setMeta({
        filter_description: resp.filter_description,
        parser_used: resp.parser_used,
        ai_translation_used: resp.ai_translation_used,
        ai_explanation_used: resp.ai_explanation_used,
        explanation: resp.explanation,
        reason: resp.reason,
      });
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setError(e.message || 'Search failed');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    search(query);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setQuery('');
      setResults([]);
      setSearched(false);
      setMeta(null);
    }
  };

  const parserBadge = () => {
    if (!meta) return null;
    const { parser_used, ai_translation_used, ai_explanation_used } = meta;
    const badges: string[] = [];
    badges.push(parser_used === 'ai' ? '🤖 AI parser' : parser_used === 'rules' ? '⚙️ Rules' : '📋 Default');
    if (ai_translation_used) badges.push('🔄 AI translation');
    if (ai_explanation_used) badges.push('💡 AI explanation');
    return (
      <div className="nl-parser-badges">
        {badges.map(b => <span key={b} className="nl-badge">{b}</span>)}
      </div>
    );
  };

  return (
    <div className="card nl-search-card">
      <div className="nl-search-header">
        <h2>🔍 AI Stock Search</h2>
        <p className="info-text">Ask in plain English — find stocks instantly</p>
      </div>

      <form className="nl-search-form" onSubmit={handleSubmit}>
        <div className="nl-search-input-row">
          <input
            ref={inputRef}
            className="nl-search-input"
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            autoComplete="off"
            spellCheck={false}
          />
          <button
            type="submit"
            className={`btn btn-primary ${loading ? 'btn-loading' : ''}`}
            disabled={loading || !query.trim()}
          >
            {loading ? '⟳' : 'Search'}
          </button>
        </div>

        <div className="nl-example-queries">
          Try:{' '}
          {EXAMPLE_QUERIES.map(eq => (
            <button
              key={eq}
              type="button"
              className="nl-example-pill"
              onClick={() => {
                setQuery(eq);
                search(eq);
              }}
            >
              {eq}
            </button>
          ))}
        </div>
      </form>

      {error && (
        <div className="nl-error">
          <span>⚠️ {error}</span>
        </div>
      )}

      {meta?.reason && results.length === 0 && !loading && (
        <div className="nl-empty">
          <p>🔍 {meta.reason}</p>
          <p className="info-text">Try a different query or add symbols to your watchlist.</p>
        </div>
      )}

      {meta?.explanation && (
        <div className="nl-explanation">
          <span className="nl-explanation-label">💡 AI insight:</span>
          <span>{meta.explanation}</span>
        </div>
      )}

      {parserBadge()}

      {searched && results.length > 0 && (
        <div className="nl-results">
          <div className="nl-results-meta">
            <span>{results.length} result{results.length !== 1 ? 's' : ''}</span>
            <span className="info-text"> · {meta?.filter_description}</span>
          </div>
          <div className="nl-results-table-wrap">
            <table className="nl-results-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Score</th>
                  <th>Trends</th>
                  <th>Signals</th>
                  <th>RSI</th>
                  {results[0]?.price != null && <th>Price</th>}
                </tr>
              </thead>
              <tbody>
                {results.map(item => (
                  <ResultRow
                    key={item.symbol}
                    item={item}
                    onClick={() => onSelectSymbol?.(item.symbol)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default NLSearchBar;
