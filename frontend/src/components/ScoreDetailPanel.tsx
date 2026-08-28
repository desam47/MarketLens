import React from 'react';

interface ScoreDetailPanelProps {
  totalScore: number;
  scores: Record<string, number>;
  signals?: string[];
  symbol: string;
}

// Estimate confidence from score magnitude (0-100), same heuristic
// used in WatchlistTable to keep values consistent across the UI.
function estimateConfidence(score: number): number {
  const abs = Math.abs(score);
  if (abs >= 70) return 90;
  if (abs >= 50) return 75;
  if (abs >= 30) return 60;
  if (abs >= 15) return 45;
  return 30;
}

const SCORE_LABELS: Record<string, string> = {
  trend_strength: 'Trend Strength',
  adx: 'ADX',
  momentum: 'Momentum',
  macd: 'MACD',
  volatility: 'Volatility',
  volume: 'Volume',
  rsi: 'RSI',
};

function fmt(n: number, decimals = 1): string {
  return n.toFixed(decimals);
}

function pickTopScores(scores: Record<string, number>, n = 4): [string, number][] {
  return Object.entries(scores)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n);
}

export function ScoreDetailPanel({
  totalScore,
  scores,
  signals,
  symbol,
}: ScoreDetailPanelProps) {
  const confidence = estimateConfidence(totalScore);
  const scoreColor = totalScore > 0 ? '#10b981' : totalScore < 0 ? '#ef4444' : '#9ca3af';
  const confColor = confidence >= 70 ? '#10b981' : confidence >= 50 ? '#22c55e' : confidence >= 30 ? '#f59e0b' : '#9ca3af';
  const topScores = pickTopScores(scores);
  const scoreSign = totalScore > 0 ? '+' : '';

  return (
    <div className="card analysis-card score-detail-card">
      <div className="card-header-row">
        <h2>Score &amp; Confidence</h2>
        <span className="symbol-tag">{symbol}</span>
      </div>

      <div className="score-detail-row">
        <div className="score-detail-cell">
          <div className="score-detail-label">Composite Score</div>
          <div className="score-detail-value" style={{ color: scoreColor }}>
            {scoreSign}{fmt(totalScore)}
          </div>
          <div className="score-detail-bar">
            <div
              className="score-detail-fill"
              style={{
                width: `${Math.min(Math.abs(totalScore), 100)}%`,
                backgroundColor: scoreColor,
              }}
            />
          </div>
        </div>

        <div className="score-detail-cell">
          <div className="score-detail-label">Confidence</div>
          <div className="score-detail-value" style={{ color: confColor }}>
            {confidence}%
          </div>
          <div className="score-detail-bar">
            <div
              className="score-detail-fill"
              style={{
                width: `${confidence}%`,
                backgroundColor: confColor,
              }}
            />
          </div>
        </div>
      </div>

      <div className="score-breakdown">
        <div className="score-breakdown-title">Score Breakdown</div>
        {topScores.length === 0 ? (
          <p className="empty-state">No dimension scores available</p>
        ) : (
          <div className="score-breakdown-list">
            {topScores.map(([key, value]) => {
              const label = SCORE_LABELS[key] ?? key;
              const pct = Math.min(Math.max(value, 0), 100);
              return (
                <div key={key} className="score-breakdown-item">
                  <span className="score-breakdown-label">{label}</span>
                  <div className="score-breakdown-bar">
                    <div
                      className="score-breakdown-fill"
                      style={{
                        width: `${pct}%`,
                        backgroundColor: pct >= 60 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#6b7280',
                      }}
                    />
                  </div>
                  <span className="score-breakdown-value">{fmt(value, 0)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {signals && signals.length > 0 && (
        <div className="signals-list">
          <div className="score-breakdown-title">Signals</div>
          <div className="signals-pills">
            {signals.map(s => (
              <span key={s} className="signal-pill">
                {s.replace(/_/g, ' ').toLowerCase()}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default ScoreDetailPanel;
