import React from 'react';

interface ScoreDetailPanelProps {
  totalScore: number;
  scores: Record<string, number>;
  signals?: string[];
  symbol: string;
}

// Estimate confidence from score magnitude (0-100).
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

// Ranked by magnitude, not raw value — several dimensions (momentum, rsi,
// macd) are signed, negative meaning bearish, and a raw-value sort would
// push the strongest bearish dimensions to the bottom and out of the
// top-N, hiding exactly the signal a bearish setup most needs to surface.
function pickTopScores(scores: Record<string, number>, n = 4): [string, number][] {
  return Object.entries(scores)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
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
              // Bar width is magnitude (so a strong bearish reading draws a
              // full bar, not an empty one); color carries direction.
              const pct = Math.min(Math.abs(value), 100);
              const barColor = value > 0 ? '#10b981' : value < 0 ? '#ef4444' : '#6b7280';
              const sign = value > 0 ? '+' : '';
              return (
                <div key={key} className="score-breakdown-item">
                  <span className="score-breakdown-label">{label}</span>
                  <div className="score-breakdown-bar">
                    <div
                      className="score-breakdown-fill"
                      style={{
                        width: `${pct}%`,
                        backgroundColor: barColor,
                      }}
                    />
                  </div>
                  <span className="score-breakdown-value">{sign}{fmt(value, 0)}</span>
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
