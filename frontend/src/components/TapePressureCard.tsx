import React, { memo } from 'react';
import { TapeSnapshot } from '../services/api';

interface TapePressureCardProps {
  tape: TapeSnapshot | null;
  disabled?: boolean;   // 503 from the API — tape streaming off
  error?: string | null;
}

const PRESSURE_META: Record<string, { label: string; color: string }> = {
  heavy_buy: { label: 'Heavy buying', color: '#10b981' },
  buy: { label: 'Net buying', color: '#4ade80' },
  neutral: { label: 'Balanced', color: '#9ca3af' },
  sell: { label: 'Net selling', color: '#f87171' },
  heavy_sell: { label: 'Heavy selling', color: '#ef4444' },
};

const TREND_META: Record<string, { label: string; color: string }> = {
  strengthening_buy: { label: 'Buying strengthening', color: '#10b981' },
  strengthening_sell: { label: 'Selling strengthening', color: '#ef4444' },
  reversing_buy: { label: 'Reversing toward buy', color: '#4ade80' },
  reversing_sell: { label: 'Reversing toward sell', color: '#f87171' },
  balanced: { label: 'Flow balanced', color: '#9ca3af' },
};

function fmtVol(n: number): string {
  const a = Math.abs(n);
  if (a >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (a >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

export const TapePressureCard = memo(function TapePressureCard({
  tape, disabled, error,
}: TapePressureCardProps) {
  if (disabled) {
    return (
      <div className="card tape-card">
        <h2>Tape Pressure</h2>
        <p className="empty-state">Tape streaming is off (set <code>TAPE_ENABLED</code>).</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="card tape-card card-error">
        <h2>Tape Pressure</h2>
        <p className="empty-state">⚠ {error}</p>
      </div>
    );
  }
  if (!tape || !tape.trade_count) {
    return (
      <div className="card tape-card">
        <h2>Tape Pressure</h2>
        <p className="empty-state">No prints in the last {tape?.window_s ?? 60}s.</p>
      </div>
    );
  }

  const meta = PRESSURE_META[tape.pressure] ?? PRESSURE_META.neutral;
  const buyPct = tape.buy_ratio != null ? Math.round(tape.buy_ratio * 100) : 50;
  const accelUp = (tape.tape_accel ?? 1) > 1.25;
  const accelDown = (tape.tape_accel ?? 1) < 0.8;
  const trend = TREND_META[tape.pressure_trend] ?? TREND_META.balanced;
  const uptickPct = tape.uptick_ratio != null ? Math.round(tape.uptick_ratio * 100) : null;

  return (
    <div className="card tape-card">
      <h2>Tape Pressure <span className="tape-window">· last {tape.window_s}s</span></h2>

      <div className="tape-pressure-row">
        <span className="tape-pressure-dot" style={{ background: meta.color }} />
        <span className="tape-pressure-label" style={{ color: meta.color }}>{meta.label}</span>
        <span className="tape-signed" style={{ color: tape.signed_volume >= 0 ? '#10b981' : '#ef4444' }}>
          {tape.signed_volume >= 0 ? '+' : ''}{fmtVol(tape.signed_volume)} net
        </span>
      </div>

      <div className="tape-ratio-bar" title={`${buyPct}% buy / ${100 - buyPct}% sell`}>
        <div className="tape-ratio-buy" style={{ width: `${buyPct}%` }} />
        <div className="tape-ratio-sell" style={{ width: `${100 - buyPct}%` }} />
      </div>
      <div className="tape-ratio-legend">
        <span>{fmtVol(tape.buy_volume)} buy</span>
        <span>{fmtVol(tape.sell_volume)} sell</span>
      </div>

      <div className="tape-stats">
        <div><span className="tape-stat-k">Speed</span><span className="tape-stat-v">
          {tape.tape_speed.toFixed(1)}/s{accelUp ? ' ▲' : accelDown ? ' ▼' : ''}
        </span></div>
        <div><span className="tape-stat-k">VWAP</span><span className="tape-stat-v">
          {tape.vwap != null ? `$${tape.vwap.toFixed(2)}` : '—'}
        </span></div>
        <div><span className="tape-stat-k">Prints</span><span className="tape-stat-v">{tape.trade_count}</span></div>
        <div><span className="tape-stat-k">Velocity</span><span className="tape-stat-v">{tape.trade_velocity.toFixed(1)}/s</span></div>
        <div><span className="tape-stat-k">Uptick / downtick</span><span className="tape-stat-v">
          {uptickPct != null ? `${uptickPct}% / ${100 - uptickPct}%` : '—'}
        </span></div>
        <div><span className="tape-stat-k">Blocks (5m)</span><span className="tape-stat-v">
          {tape.block_count_5m > 0
            ? <span className="tape-block-badge">{tape.block_count_5m}</span>
            : '0'}
        </span></div>
      </div>

      <p className="tape-flow-trend" style={{ color: trend.color }}>
        ● {trend.label}
        {tape.recent_buy_ratio != null && ` · ${Math.round(tape.recent_buy_ratio * 100)}% buy recently`}
      </p>

      {tape.last_block && (
        <p className="tape-last-block">
          Last block: {fmtVol(tape.last_block.size)} @ ${tape.last_block.price.toFixed(2)}
          {' '}({tape.last_block.side}, {tape.last_block.age_s}s ago)
        </p>
      )}

      {tape.recent_prints?.length > 0 && (
        <div className="tape-recent-prints">
          <div className="tape-recent-heading"><span>Recent prints</span><span>Price · size</span></div>
          {tape.recent_prints.slice(0, 6).map((print, index) => (
            <div className={`tape-print tape-print-${print.side}${print.is_block ? ' tape-print-block' : ''}`} key={`${print.timestamp}-${index}`}>
              <span>{print.side === 'buy' ? '▲ Buy' : '▼ Sell'}{print.is_block ? ' · Block' : ''}</span>
              <span>${print.price.toFixed(2)} · {fmtVol(print.size)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
