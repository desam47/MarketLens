/** A grounded Watchlist briefing rendered from the Scanner's typed snapshot. */
import React from 'react';
import {
  CalendarEvent,
  WatchlistIntelligence as WatchlistIntelligenceData,
  WatchlistIntelligenceEntry,
} from '../services/api';
import { formatETDateTime } from './chartMath';

interface WatchlistIntelligenceProps {
  data: WatchlistIntelligenceData | null;
  events: CalendarEvent[];
  loading?: boolean;
  error?: string | null;
  onSelectSymbol: (symbol: string) => void;
}

function sessionLabel(scope: string): string {
  if (scope === 'all') return 'All sessions';
  if (scope === 'none') return 'No session selected';
  return scope.split(',').map(value => ({
    premarket: 'Premarket', regular: 'Regular', after_hours: 'After-hours',
  }[value] || value)).join(' + ');
}

function metricText(entry: WatchlistIntelligenceEntry): string | null {
  if (entry.metric == null) return null;
  if (entry.metric_label === 'change %' || entry.metric_label === '20-bar breakout %' || entry.metric_label?.startsWith('relative strength')) {
    return `${entry.metric > 0 ? '+' : ''}${entry.metric.toFixed(2)}%`;
  }
  if (entry.metric_label === 'volume / trailing average') return `${entry.metric.toFixed(2)}×`;
  if (entry.metric_label === 'confirmed timeframes') return `${entry.metric.toFixed(0)} TF`;
  return entry.metric.toFixed(2);
}

function InsightList({
  title,
  entries,
  empty,
  onSelectSymbol,
}: {
  title: string;
  entries: WatchlistIntelligenceEntry[];
  empty: string;
  onSelectSymbol: (symbol: string) => void;
}) {
  return (
    <section className="watchlist-intelligence-section">
      <h3>{title}</h3>
      {entries.length === 0 ? <p className="watchlist-intelligence-empty">{empty}</p> : (
        <ul className="watchlist-intelligence-list">
          {entries.map(entry => {
            const metric = metricText(entry);
            const isNegative = (entry.metric ?? 0) < 0;
            return (
              <li key={`${title}-${entry.symbol}`}>
                <button type="button" onClick={() => onSelectSymbol(entry.symbol)}>
                  <strong>{entry.symbol}</strong>
                  {metric && <span className={isNegative ? 'negative' : 'positive'}>{metric}</span>}
                  {entry.metric_label && <small>{entry.metric_label}</small>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function WatchlistIntelligence({
  data,
  events,
  loading = false,
  error = null,
  onSelectSymbol,
}: WatchlistIntelligenceProps) {
  if (loading && !data) {
    return <section className="watchlist-intelligence card" aria-label="Watchlist intelligence"><p>Building grounded watchlist briefing…</p></section>;
  }
  if (error && !data) {
    return <section className="watchlist-intelligence card" aria-label="Watchlist intelligence"><p className="watchlist-intelligence-error">Watchlist intelligence unavailable: {error}</p></section>;
  }
  if (!data) return null;

  const upcomingEvents = events
    .filter(event => event.event_type === 'earnings' || event.event_type === 'ex_dividend' || event.event_type === 'dividend')
    .sort((left, right) => left.date.localeCompare(right.date) || left.symbol.localeCompare(right.symbol))
    .slice(0, 6);

  return (
    <section className="watchlist-intelligence card" aria-label="Watchlist intelligence">
      <header className="watchlist-intelligence-header">
        <div>
          <h2>Watchlist Intelligence</h2>
          <p>Grounded scanner snapshot · {sessionLabel(data.session_scope)}</p>
        </div>
        <div className={`watchlist-intelligence-status ${data.data_status}`}>
          <strong>{data.data_status === 'ready' ? 'Ready' : data.data_status === 'warming' ? 'Warming' : 'Partial'}</strong>
          <span>{data.analyzed_symbols}/{data.watchlist_size} analyzed</span>
        </div>
      </header>
      <p className="watchlist-intelligence-basis">Price basis: {data.price_basis}. Updated {formatETDateTime(data.generated_at)}.</p>
      {error && <p className="watchlist-intelligence-error">Latest refresh failed: {error}</p>}
      {data.warnings.length > 0 && (
        <ul className="watchlist-intelligence-warnings">
          {data.warnings.map(warning => <li key={warning}>{warning}</li>)}
        </ul>
      )}

      <div className="watchlist-intelligence-grid movers">
        <InsightList title="Top Bullish" entries={data.top_bullish} empty="No positive price moves in this scope." onSelectSymbol={onSelectSymbol} />
        <InsightList title="Top Bearish" entries={data.top_bearish} empty="No negative price moves in this scope." onSelectSymbol={onSelectSymbol} />
      </div>
      <div className="watchlist-intelligence-grid signals">
        <InsightList title="Breakout Signals" entries={data.breakouts} empty="No current 20-bar breakout signals." onSelectSymbol={onSelectSymbol} />
        <InsightList title="Deteriorating Setups" entries={data.deteriorating} empty="No current deteriorating setups." onSelectSymbol={onSelectSymbol} />
        <InsightList title="Volume Spikes" entries={data.volume_spikes} empty="No volume ratios at or above 1.5×." onSelectSymbol={onSelectSymbol} />
        <InsightList title="Relative Strength" entries={data.relative_strength} empty="No benchmark-relative readings are available." onSelectSymbol={onSelectSymbol} />
        <InsightList title="Multi-Timeframe Alignment" entries={data.mtf_alignment} empty="No symbols have three confirmed timeframes aligned." onSelectSymbol={onSelectSymbol} />
      </div>

      <div className="watchlist-intelligence-lower">
        <section className="watchlist-intelligence-section sector">
          <h3>Watchlist Sector Momentum</h3>
          {data.sector_rotation.length === 0 ? <p className="watchlist-intelligence-empty">No mapped sector price data is available in this scope.</p> : (
            <ul className="watchlist-sector-list">
              {data.sector_rotation.map(sector => (
                <li key={sector.sector} title={sector.symbols.join(', ')}>
                  <span><strong>{sector.sector}</strong><small>{sector.symbols.length} mapped symbol{sector.symbols.length === 1 ? '' : 's'} · {sector.advancing} up / {sector.declining} down</small></span>
                  <b className={sector.average_change_pct < 0 ? 'negative' : 'positive'}>{sector.average_change_pct > 0 ? '+' : ''}{sector.average_change_pct.toFixed(2)}%</b>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="watchlist-intelligence-section events">
          <h3>Earnings &amp; Events</h3>
          {upcomingEvents.length === 0 ? <p className="watchlist-intelligence-empty">No upcoming provider-estimated events are available.</p> : (
            <ul className="watchlist-events-list">
              {upcomingEvents.map(event => (
                <li key={`${event.symbol}-${event.event_type}-${event.date}`}>
                  <button type="button" onClick={() => onSelectSymbol(event.symbol)}><strong>{event.symbol}</strong> <span>{event.event_type.replace('_', ' ')}</span> <time>{event.date}</time></button>
                </li>
              ))}
            </ul>
          )}
          <p className="watchlist-intelligence-note">Dates are provider estimates. This overview does not infer unverified news catalysts.</p>
        </section>
      </div>
    </section>
  );
}

export default WatchlistIntelligence;
