import React from 'react';
import { CalendarEvent } from '../services/api';

export function earningsDaysAway(events: CalendarEvent[]): number | null {
  const earnings = events.filter(event => event.event_type === 'earnings').map(event => event.date).sort()[0];
  if (!earnings) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const eventDate = new Date(`${earnings}T00:00:00`);
  return Math.round((eventDate.getTime() - today.getTime()) / 86_400_000);
}

export function EarningsBadge({ events }: { events: CalendarEvent[] }) {
  const days = earningsDaysAway(events);
  if (days == null || days < 0) return null;
  return <span className={`earnings-badge ${days <= 3 ? 'earnings-imminent' : ''}`} title="Provider-estimated earnings date">Earnings {days === 0 ? 'today' : `in ${days}d`}</span>;
}
