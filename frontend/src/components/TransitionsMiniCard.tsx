import React, { useEffect, useState, useCallback } from 'react';
import api, { Transition, TransitionsResult } from '../services/api';
import { parseET } from './chartMath';

interface TransitionsMiniCardProps {
  symbol: string;
  onSelectSymbol?: (symbol: string) => void;
  limit?: number;
}

const transitionColors: Record<string, string> = {
  bullish_reversal: '#10b981',
  bullish_acceleration: '#22c55e',
  bullish_weakening: '#84cc16',
  bearish_reversal: '#ef4444',
  bearish_acceleration: '#dc2626',
  bearish_weakening: '#f97316',
};

const directionBadge: Record<string, string> = {
  bullish: '🐂',
  bearish: '🐻',
  neutral: '➡',
};

function formatScore(score: number): string {
  const sign = score > 0 ? '+' : '';
  return sign + score.toFixed(1);
}

export function TransitionsMiniCard({
  symbol,
  onSelectSymbol,
  limit = 5,
}: TransitionsMiniCardProps) {
  const [transitions, setTransitions] = useState<Transition[]>([]);
  const [latestScore, setLatestScore] = useState(0);
  const [latestTimestamp, setLatestTimestamp] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTransitions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data: TransitionsResult = await api.getTransitions(symbol, '1d');
      setTransitions(data.transitions || []);
      setLatestScore(data.latest_score ?? 0);
      setLatestTimestamp(data.latest_timestamp ?? null);
    } catch (e: any) {
      setError(e?.message || 'Failed to load transitions');
      setTransitions([]);
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    fetchTransitions();
  }, [fetchTransitions]);

  const recent = transitions.slice(-limit).reverse();
  const scoreColor = latestScore > 0 ? '#10b981' : latestScore < 0 ? '#ef4444' : '#9ca3af';

  return (
    <div className="card transitions-mini-card">
      <div className="card-header-row">
        <h2>Trend Transitions</h2>
        <span
          className="transitions-mini-symbol"
          onClick={() => onSelectSymbol?.(symbol)}
          role={onSelectSymbol ? 'button' : undefined}
        >
          {symbol}
        </span>
      </div>

      <div className="score-bar">
        <span className="score-label">Score</span>
        <div className="progress-bar" style={{ flex: 1 }}>
          <div
            className="progress-fill"
            style={{
              width: `${Math.min(Math.abs(latestScore), 100)}%`,
              backgroundColor: scoreColor,
            }}
          />
        </div>
        <span className="score-value" style={{ color: scoreColor }}>
          {formatScore(latestScore)}
        </span>
      </div>

      {error && <p className="empty-state">⚠ {error}</p>}

      {loading ? (
        <p className="empty-state">Loading transitions…</p>
      ) : recent.length === 0 ? (
        <p className="empty-state">No recent transitions for {symbol}</p>
      ) : (
        <div className="transitions-mini-list">
          {recent.map((t, i) => {
            const color = transitionColors[t.type] || '#9ca3af';
            return (
              <div
                key={i}
                className="transition-mini-item"
                style={{ borderLeftColor: color }}
              >
                <span className="transition-mini-type" style={{ color }}>
                  {directionBadge[t.direction] || '—'} {t.type.replace(/_/g, ' ')}
                </span>
                <span className="transition-mini-delta">
                  {formatScore(t.previous_score)} → {formatScore(t.current_score)}
                </span>
                <span className="transition-mini-time">
                  {t.timestamp ? parseET(t.timestamp).toLocaleDateString() : '—'}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {latestTimestamp && !loading && recent.length > 0 && (
        <div className="transitions-mini-footer">
          Updated: {parseET(latestTimestamp).toLocaleString()}
        </div>
      )}
    </div>
  );
}

export default TransitionsMiniCard;
