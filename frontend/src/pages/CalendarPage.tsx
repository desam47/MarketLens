import React, { useCallback, useEffect, useState } from 'react';
import api, { CalendarEvent, Watchlist, WatchlistCalendar } from '../services/api';
import { ErrorBanner } from '../components/ErrorBanner';
import type { NavigationState } from '../utils/appNavigation';

const EVENT_LABELS: Record<string, string> = { earnings: 'Earnings', ex_dividend: 'Ex-dividend', dividend: 'Dividend' };

export function CalendarPage({ navigation }: { navigation?: NavigationState }) {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const initialWatchlistId = typeof navigation?.filters?.calendar === 'object' && !Array.isArray(navigation?.filters?.calendar)
    ? Number((navigation?.filters?.calendar as Record<string, unknown>).watchlistId)
    : NaN;
  const [watchlistId, setWatchlistId] = useState<number | null>(Number.isFinite(initialWatchlistId) ? initialWatchlistId : null);
  const [calendar, setCalendar] = useState<WatchlistCalendar | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!navigation?.filters?.calendar || typeof navigation.filters.calendar !== 'object' || Array.isArray(navigation.filters.calendar)) return;
    const requested = Number((navigation.filters.calendar as Record<string, unknown>).watchlistId);
    if (Number.isFinite(requested)) setWatchlistId(requested);
  }, [navigation]);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const lists = await api.getWatchlists();
      setWatchlists(lists);
      const id = watchlistId && lists.some(list => list.id === watchlistId) ? watchlistId : lists[0]?.id;
      setWatchlistId(id ?? null);
      setCalendar(id ? await api.getWatchlistCalendar(id) : null);
    } catch (err: any) {
      setError(err?.message || 'Failed to load the events calendar');
    } finally { setLoading(false); }
  }, [watchlistId]);

  useEffect(() => { void load(); }, [load]);

  const selectWatchlist = async (id: number) => {
    setWatchlistId(id); setLoading(true); setError(null);
    try { setCalendar(await api.getWatchlistCalendar(id)); }
    catch (err: any) { setError(err?.message || 'Failed to load the events calendar'); }
    finally { setLoading(false); }
  };

  const selectedSymbols = new Set((navigation?.selectedRecords || []).map(symbol => symbol.toUpperCase()));
  const groups = (calendar?.events || []).filter(event => selectedSymbols.size === 0 || selectedSymbols.has(event.symbol.toUpperCase())).reduce<Record<string, CalendarEvent[]>>((result, event) => {
    (result[event.date] ||= []).push(event); return result;
  }, {});

  return <div className="calendar-page">
    <div className="page-title-row"><div><h1>Earnings &amp; Events</h1><p className="subtitle">Upcoming earnings and dividend dates for your watchlist. Dates are provider estimates and can change.</p></div>
      <select value={watchlistId ?? ''} onChange={event => void selectWatchlist(Number(event.target.value))} disabled={!watchlists.length}>
        {watchlists.map(list => <option key={list.id} value={list.id}>{list.name}</option>)}
      </select>
    </div>
    {error && <ErrorBanner message={error} onDismiss={() => setError(null)} onRetry={() => void load()} />}
    {loading ? <div className="card calendar-empty" role="status">Loading watchlist events…</div> : !watchlists.length ? <div className="card calendar-empty">Create a watchlist first to see upcoming events.</div> : !calendar?.events.length ? <div className="card calendar-empty">No upcoming event dates are available for this watchlist yet.</div> :
      <div className="calendar-list">{Object.entries(groups).map(([day, events]) => <section className="card calendar-day" key={day}><h2>{new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })}</h2>{events.map(event => <div className="calendar-event" key={`${event.symbol}-${event.event_type}`}><strong>{event.symbol}</strong><span className={`calendar-event-type calendar-${event.event_type}`}>{EVENT_LABELS[event.event_type] || event.event_type}</span><small>Yahoo Finance</small></div>)}</section>)}</div>}
  </div>;
}
