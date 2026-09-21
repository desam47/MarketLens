import React, { memo } from 'react';
import { StrategyData } from '../services/api';
import { formatETDateTime } from './chartMath';

interface StrategyCardProps {
  strategy: StrategyData | null;
  error?: string | null;
  onRetry?: () => void;
}

const strategyIcons: Record<string, string> = {
  trend_following: '📈',
  mean_reversion: '🔄',
  breakout: '💥',
  momentum: '🚀',
  volatility: '⚡',
  scalping: '⚡',
  swing: '🌊',
  position: '🎯',
};

const strategyColors: Record<string, string> = {
  trend_following: '#10b981',
  mean_reversion: '#f59e0b',
  breakout: '#ef4444',
  momentum: '#3b82f6',
  volatility: '#8b5cf6',
  scalping: '#ec4899',
  swing: '#06b6d4',
  position: '#84cc16',
};

export const StrategyCard = memo(function StrategyCard({ strategy, error, onRetry }: StrategyCardProps) {
  if (error) {
    return (
      <div className="card strategy-card card-error">
        <h2>Strategy Recommendation</h2>
        <p className="empty-state">⚠ Failed to load: {error}</p>
        {onRetry && <button className="btn btn-small data-state-retry" onClick={onRetry}>Retry</button>}
      </div>
    );
  }
  if (!strategy) {
    return (
      <div className="card strategy-card">
        <h2>Strategy Recommendation</h2>
        <p className="empty-state">No strategy data available</p>
      </div>
    );
  }

  const icon = strategyIcons[strategy.strategy_type] || '📊';
  const color = strategyColors[strategy.strategy_type] || '#6b7280';
  const confidencePct = (strategy.confidence * 100).toFixed(0);

  return (
    <div className="card strategy-card" style={{ borderTopColor: color }}>
      <h2>Strategy Recommendation</h2>
      <div className="strategy-main">
        <span className="strategy-icon" style={{ backgroundColor: color }}>
          {icon}
        </span>
        <div className="strategy-name">
          <h3 style={{ color }}>{strategy.strategy_type.replace(/_/g, ' ').toUpperCase()}</h3>
          <span className="strategy-tf">Timeframe: {strategy.timeframe}</span>
        </div>
      </div>
      <div className="strategy-confidence">
        <span className="metric-label">Confidence</span>
        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{ width: `${confidencePct}%`, backgroundColor: color }}
          />
        </div>
        <span className="metric-value">{confidencePct}%</span>
      </div>
      {strategy.parameters && Object.keys(strategy.parameters).length > 0 && (
        <div className="strategy-params">
          <h4>Parameters</h4>
          <ul>
            {Object.entries(strategy.parameters).map(([key, value]) => (
              <li key={key}>
                <span className="param-key">{key.replace(/_/g, ' ')}:</span>
                <span className="param-value">{String(value)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {strategy.timestamp && (
        <div className="timestamp">
          Updated: {formatETDateTime(strategy.timestamp)}
        </div>
      )}
    </div>
  );
});
