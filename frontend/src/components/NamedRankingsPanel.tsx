/**
 * NamedRankingsPanel — Phase 10 named rankings display.
 *
 * Shows the 7 ranking categories (Strongest Bullish, Strongest Bearish, etc.)
 * and their top-N entries. Clicking a category expands it to show its ranked
 * list; clicking a symbol row fires onSelectSymbol for navigation.
 */
import React, { useCallback, useEffect, useState } from 'react';
import api, { NamedRanking, RankingCategoryMeta, RankingEntry } from '../services/api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function scoreColor(score: number): string {
  if (score >= 30) return 'rank-score-pos';
  if (score <= -30) return 'rank-score-neg';
  return 'rank-score-neutral';
}

function fmtScore(score: number): string {
  return (score >= 0 ? '+' : '') + score.toFixed(2);
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface NamedRankingsPanelProps {
  symbols: string[];
  topN?: number;
  onSelectSymbol: (symbol: string) => void;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function CategoryHeader({
  meta,
  isExpanded,
  onToggle,
}: {
  meta: RankingCategoryMeta;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rank-category-header" onClick={onToggle}>
      <div className="rank-category-info">
        <span className="rank-category-name">{meta.label}</span>
        <span className="rank-category-desc">{meta.description}</span>
      </div>
      <span className="rank-category-chevron">{isExpanded ? '▲' : '▼'}</span>
    </div>
  );
}

function RankingEntryRow({
  entry,
  rank,
  onSelectSymbol,
}: {
  entry: RankingEntry;
  rank: number;
  onSelectSymbol: (symbol: string) => void;
}) {
  return (
    <div className="rank-entry-row" onClick={() => onSelectSymbol(entry.symbol)}>
      <span className="rank-entry-rank">#{rank}</span>
      <span className="rank-entry-symbol">{entry.symbol}</span>
      <span className={`rank-entry-score ${scoreColor(entry.score)}`}>
        {fmtScore(entry.score)}
      </span>
    </div>
  );
}

function RankingBody({
  ranking,
  onSelectSymbol,
}: {
  ranking: NamedRanking;
  onSelectSymbol: (symbol: string) => void;
}) {
  if (ranking.entries.length === 0) {
    return (
      <div className="rank-empty">
        No symbols matched the filter for this category.
      </div>
    );
  }
  return (
    <div className="rank-body">
      <div className="rank-table-header">
        <span className="rank-col-rank">#</span>
        <span className="rank-col-symbol">Symbol</span>
        <span className="rank-col-score">Score</span>
      </div>
      {ranking.entries.map(entry => (
        <RankingEntryRow
          key={entry.symbol}
          entry={entry}
          rank={entry.rank}
          onSelectSymbol={onSelectSymbol}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// NamedRankingsPanel
// ---------------------------------------------------------------------------

export function NamedRankingsPanel({ symbols, topN = 10, onSelectSymbol }: NamedRankingsPanelProps) {
  const [categories, setCategories] = useState<RankingCategoryMeta[]>([]);
  const [rankings, setRankings] = useState<NamedRanking[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [topNInput, setTopNInput] = useState(String(topN));

  // Load ranking categories on mount
  useEffect(() => {
    api.getRankingCategories()
      .then(setCategories)
      .catch(() => {/* non-fatal — categories are static */});
  }, []);

  // Fetch rankings
  const fetchRankings = useCallback(async (n: number) => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await api.getRankings({ filters: [], match: 'AND' }, n, symbols);
      setRankings(result);
    } catch (e: any) {
      setError(e.message || 'Failed to load rankings');
    } finally {
      setIsLoading(false);
    }
  }, [symbols]);

  // Initial fetch + re-fetch when symbols or topN change
  useEffect(() => {
    fetchRankings(topN);
  }, [fetchRankings, topN]);

  const toggleCategory = useCallback((name: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const handleRefresh = useCallback(() => {
    fetchRankings(parseInt(topNInput, 10) || topN);
  }, [fetchRankings, topNInput, topN]);

  const handleTopNChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setTopNInput(e.target.value);
  }, []);

  // Build a map of name → ranking for quick lookup
  const rankingMap = Object.fromEntries(rankings.map(r => [r.name, r]));

  return (
    <div className="rankings-panel">
      {/* Header bar */}
      <div className="rankings-header">
        <span className="rankings-title">Rankings</span>
        <div className="rankings-controls">
          <label className="rankings-topn-label">
            Top
            <input
              type="number"
              className="rankings-topn-input"
              value={topNInput}
              min={1}
              max={50}
              onChange={handleTopNChange}
              onClick={e => e.stopPropagation()}
            />
          </label>
          <button
            className="btn btn-sm btn-secondary"
            onClick={e => { e.stopPropagation(); handleRefresh(); }}
            disabled={isLoading}
          >
            {isLoading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Error */}
      {error && <div className="rankings-error">{error}</div>}

      {/* Category list */}
      <div className="rankings-body">
        {isLoading && rankings.length === 0 ? (
          <div className="rankings-loading">Loading rankings…</div>
        ) : (
          categories.map(meta => {
            const ranking = rankingMap[meta.name];
            const isExpanded = expanded.has(meta.name);
            return (
              <div key={meta.name} className="rank-category">
                <CategoryHeader
                  meta={meta}
                  isExpanded={isExpanded}
                  onToggle={() => toggleCategory(meta.name)}
                />
                {isExpanded && ranking && (
                  <RankingBody
                    ranking={ranking}
                    onSelectSymbol={onSelectSymbol}
                  />
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
