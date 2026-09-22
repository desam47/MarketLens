import React, { useState } from 'react';
import api, { TickReplayEvent } from '../services/api';

export function TickReplayPanel() {
  const [symbol, setSymbol] = useState('SPY');
  const [events, setEvents] = useState<TickReplayEvent[]>([]);
  const [cursor, setCursor] = useState(0);
  const [loading, setLoading] = useState(false);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const load = async () => { setLoading(true); try { const r = await api.getTickReplay(symbol.trim().toUpperCase(), 2000, start ? new Date(start).toISOString() : undefined, end ? new Date(end).toISOString() : undefined); setEvents(r.events); setCursor(0); } finally { setLoading(false); } };
  const event = events[cursor];
  return <div className="card historical-replay-card">
    <div className="replay-heading"><div><h2>Live Tick Replay</h2><p className="label">Locally retained Webull trades and BBO updates; playback makes no provider request.</p></div><span className="replay-counter">{events.length ? `${cursor + 1} / ${events.length}` : 'Load a symbol'}</span></div>
    <div className="replay-controls"><label><span>Symbol</span><input value={symbol} maxLength={5} onChange={e => setSymbol(e.target.value.toUpperCase())} /></label><label><span>From</span><input type="datetime-local" value={start} onChange={e => setStart(e.target.value)} /></label><label><span>To</span><input type="datetime-local" value={end} onChange={e => setEnd(e.target.value)} /></label><button className="btn btn-secondary" onClick={() => void load()} disabled={loading}>{loading ? 'Loading…' : 'Load ticks'}</button></div>
    {event && <><div className="replay-current-bar"><div className="replay-date">{event.timestamp || '—'} · {event.event_type}</div><div className="replay-ohlc-grid"><span><small>Price</small><strong>{event.price?.toFixed(2) ?? '—'}</strong></span><span><small>Bid / Ask</small><strong>{event.bid?.toFixed(2) ?? '—'} / {event.ask?.toFixed(2) ?? '—'}</strong></span><span><small>Spread</small><strong>{event.spread_bps?.toFixed(1) ?? '—'} bps</strong></span><span><small>Size</small><strong>{event.bid_size ?? '—'} / {event.ask_size ?? '—'}</strong></span></div></div><input className="replay-progress" type="range" min="0" max={events.length - 1} value={cursor} onChange={e => setCursor(Number(e.target.value))} /></>}
  </div>;
}
