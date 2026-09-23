import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api, { Alert, AlertDelivery, AlertDeliverySummary, AlertTrigger } from '../services/api';
import { formatETDateTime } from './chartMath';
import { TIMEFRAME_LABELS } from '../utils/timeframeUtils';
import { openAlertConversation } from '../utils/alertConversation';

const ALERT_TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d'] as const;
const ACK_KEY = 'marketlens.signal-alerts.acknowledged';

interface SignalProfile {
  direction: string;
  min_score: number;
  min_strength: number;
  market_regime: string;
  session: string;
  timeframe: string;
  cooldown_minutes: number;
  channels: string[];
  webhook_url?: string;
  email_to?: string;
  quiet_hours?: { start: string; end: string };
  snoozed_until?: string;
}

interface SignalAlertForm {
  name: string;
  symbol: string;
  direction: string;
  minScore: number;
  minStrength: number;
  market_regime: string;
  session: string;
  timeframe: string;
  cooldown_minutes: number;
  channels: string[];
  webhookUrl: string;
  emailTo: string;
  quietStart: string;
  quietEnd: string;
}

const DEFAULT_PROFILE: SignalProfile = {
  direction: 'bullish',
  min_score: 70,
  min_strength: 0.7,
  market_regime: 'any',
  session: 'any',
  timeframe: '1d',
  cooldown_minutes: 60,
  channels: ['in_app', 'browser'],
};

const INITIAL_FORM: SignalAlertForm = {
  name: '',
  symbol: 'SPY',
  direction: DEFAULT_PROFILE.direction,
  minScore: DEFAULT_PROFILE.min_score,
  minStrength: DEFAULT_PROFILE.min_strength,
  market_regime: DEFAULT_PROFILE.market_regime,
  session: DEFAULT_PROFILE.session,
  timeframe: DEFAULT_PROFILE.timeframe,
  cooldown_minutes: DEFAULT_PROFILE.cooldown_minutes,
  channels: DEFAULT_PROFILE.channels,
  webhookUrl: '',
  emailTo: '',
  quietStart: '',
  quietEnd: '',
};

function parseProfile(alert: Alert): SignalProfile {
  try {
    const parsed = JSON.parse(alert.parameter);
    const merged = { ...DEFAULT_PROFILE, ...(parsed || {}) };
    return {
      ...merged,
      channels: Array.isArray(merged.channels) && merged.channels.length > 0 ? merged.channels : DEFAULT_PROFILE.channels,
    };
  } catch {
    return { ...DEFAULT_PROFILE };
  }
}

function profileSummary(profile: SignalProfile): string {
  const direction = profile.direction === 'any' ? 'Any direction' : profile.direction;
  const regime = profile.market_regime === 'any' ? 'any regime' : profile.market_regime;
  const session = profile.session === 'any' ? 'any session' : profile.session.replace('_', ' ');
  return `${direction} · score ≥ ${profile.min_score} · strength ≥ ${profile.min_strength} · ${profile.timeframe || 'all TF'} · ${regime} · ${session}`;
}

function isQuietNow(profile: SignalProfile): boolean {
  const quiet = profile.quiet_hours;
  if (!quiet?.start || !quiet.end) return false;
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(new Date());
  const hour = parts.find((part) => part.type === 'hour')?.value || '00';
  const minute = parts.find((part) => part.type === 'minute')?.value || '00';
  const current = `${hour}:${minute}`;
  if (quiet.start === quiet.end) return true;
  return quiet.start < quiet.end
    ? current >= quiet.start && current < quiet.end
    : current >= quiet.start || current < quiet.end;
}

function readAcknowledged(): Set<number> {
  try {
    const values = JSON.parse(window.localStorage.getItem(ACK_KEY) || '[]');
    return new Set(Array.isArray(values) ? values.filter((value) => Number.isInteger(value)) : []);
  } catch {
    return new Set();
  }
}

function writeAcknowledged(values: Set<number>): void {
  window.localStorage.setItem(ACK_KEY, JSON.stringify(Array.from(values).slice(-500)));
}

export function SignalAlertCenter() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [triggers, setTriggers] = useState<AlertTrigger[]>([]);
  const [form, setForm] = useState<SignalAlertForm>(INITIAL_FORM);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [status, setStatus] = useState<{ message: string; error: boolean } | null>(null);
  const [acknowledged, setAcknowledged] = useState<Set<number>>(() => readAcknowledged());
  const [deliveriesByAlert, setDeliveriesByAlert] = useState<Map<number, AlertDelivery[]>>(new Map());
  const [deliverySummary, setDeliverySummary] = useState<AlertDeliverySummary | null>(null);
  const [editingAlertId, setEditingAlertId] = useState<number | null>(null);
  const [editingSnoozedUntil, setEditingSnoozedUntil] = useState<string | undefined>(undefined);
  const [expandedDeliveries, setExpandedDeliveries] = useState<Set<number>>(new Set());
  const seenTriggerIds = useRef<Set<number> | null>(null);

  const signalAlerts = useMemo(
    () => alerts.filter((alert) => alert.condition_type === 'signal_profile'),
    [alerts],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [alertRows, triggerRows] = await Promise.all([
        api.getAlerts(),
        api.getActiveAlertTriggers(),
      ]);
      setAlerts(alertRows);
      setTriggers(triggerRows);
      setDeliverySummary(await api.getAlertDeliverySummary().catch(() => null));
      const signalRows = alertRows.filter((alert) => alert.condition_type === 'signal_profile');
      const deliveryRows = await Promise.all(signalRows.map(async (alert) => [
        alert.id,
        await api.getAlertDeliveries(alert.id, 100).catch(() => []),
      ] as const));
      setDeliveriesByAlert(new Map(deliveryRows));
      const previous = seenTriggerIds.current;
      if (previous && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        triggerRows.filter((trigger) => !previous.has(trigger.id)).forEach((trigger) => {
          const alert = signalRows.find((row) => row.id === trigger.alert_id);
          if (!alert) return;
          const profile = parseProfile(alert);
          if (!profile.channels.includes('browser') || isQuietNow(profile)) return;
          new Notification('MarketLens signal alert', {
            body: `${trigger.symbol}: ${trigger.message || alert.name}`,
          });
        });
      }
      seenTriggerIds.current = new Set(triggerRows.map((trigger) => trigger.id));
      setStatus(null);
    } catch (err: any) {
      setStatus({ message: err?.message || 'Unable to load signal alerts.', error: true });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const signalAlertIds = useMemo(() => new Set(signalAlerts.map((alert) => alert.id)), [signalAlerts]);
  const visibleTriggers = triggers.filter((trigger) => signalAlertIds.has(trigger.alert_id) && !acknowledged.has(trigger.id));
  const alertById = useMemo(() => new Map(signalAlerts.map((alert) => [alert.id, alert])), [signalAlerts]);

  const updateProfile = (key: keyof typeof form, value: string | number | string[]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const toggleChannel = (channel: string) => {
    setForm((current) => ({
      ...current,
      channels: current.channels.includes(channel)
        ? current.channels.filter((value) => value !== channel)
        : [...current.channels, channel],
    }));
  };

  const requestBrowserPermission = async () => {
    if (typeof Notification === 'undefined') {
      setStatus({ message: 'Browser notifications are not supported here.', error: true });
      return;
    }
    try {
      const permission = await Notification.requestPermission();
      setStatus({
        message: permission === 'granted' ? 'Browser notifications enabled.' : 'Browser notification permission was not granted.',
        error: permission !== 'granted',
      });
    } catch {
      setStatus({ message: 'Unable to request browser notification permission.', error: true });
    }
  };

  const retryDelivery = async (delivery: AlertDelivery) => {
    try {
      await api.retryAlertDelivery(delivery.id);
      setStatus({ message: `${delivery.channel} delivery retry queued.`, error: false });
      await refresh();
    } catch (err: any) {
      setStatus({ message: err?.message || 'Failed to retry delivery.', error: true });
    }
  };

  const profileFromForm = (): SignalProfile => ({
    direction: form.direction,
    min_score: Number(form.minScore),
    min_strength: Number(form.minStrength),
    market_regime: form.market_regime,
    session: form.session,
    timeframe: form.timeframe,
    cooldown_minutes: Number(form.cooldown_minutes),
    channels: form.channels,
    webhook_url: form.webhookUrl || undefined,
    email_to: form.emailTo || undefined,
    quiet_hours: form.quietStart && form.quietEnd ? { start: form.quietStart, end: form.quietEnd } : undefined,
    snoozed_until: editingSnoozedUntil,
  });

  const editAlert = (alert: Alert) => {
    const profile = parseProfile(alert);
    setEditingAlertId(alert.id);
    setEditingSnoozedUntil(profile.snoozed_until);
    setForm({
      name: alert.name,
      symbol: alert.symbol,
      direction: profile.direction,
      minScore: profile.min_score,
      minStrength: profile.min_strength,
      market_regime: profile.market_regime,
      session: profile.session,
      timeframe: profile.timeframe,
      cooldown_minutes: profile.cooldown_minutes,
      channels: profile.channels,
      webhookUrl: profile.webhook_url || '',
      emailTo: profile.email_to || '',
      quietStart: profile.quiet_hours?.start || '',
      quietEnd: profile.quiet_hours?.end || '',
    });
    setStatus({ message: `Editing ${alert.name}.`, error: false });
  };

  const cancelEdit = () => {
    setEditingAlertId(null);
    setEditingSnoozedUntil(undefined);
    setForm(INITIAL_FORM);
  };

  const duplicateAlert = async (alert: Alert) => {
    const name = `${alert.name} copy`.slice(0, 120);
    const profile = parseProfile(alert);
    delete profile.snoozed_until;
    try {
      await api.createAlert({
        name,
        symbol: alert.symbol,
        condition_type: alert.condition_type,
        parameter: JSON.stringify(profile),
      });
      setStatus({ message: `Duplicated ${alert.name}.`, error: false });
      await refresh();
    } catch (err: any) {
      setStatus({ message: err?.message || 'Failed to duplicate alert.', error: true });
    }
  };

  const removeAlert = async (alert: Alert) => {
    if (!window.confirm(`Delete “${alert.name}”? This also removes its trigger history.`)) return;
    try {
      await api.deleteAlert(alert.id);
      if (editingAlertId === alert.id) cancelEdit();
      setStatus({ message: `Deleted ${alert.name}.`, error: false });
      await refresh();
    } catch (err: any) {
      setStatus({ message: err?.message || 'Failed to delete alert.', error: true });
    }
  };

  const testDelivery = async (alert: Alert, channel: string) => {
    try {
      const result = await api.testAlertDelivery(alert.id, channel);
      setStatus({ message: `${channel} test: ${result.response}`, error: result.status !== 'delivered' });
    } catch (err: any) {
      setStatus({ message: err?.message || `Failed to test ${channel} delivery.`, error: true });
    }
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const name = form.name.trim();
    const symbol = form.symbol.trim().toUpperCase();
    if (!name || !symbol) {
      setStatus({ message: 'Alert name and symbol are required.', error: true });
      return;
    }
    if (form.minScore < 0 || form.minScore > 100 || form.minStrength < 0 || form.minStrength > 1) {
      setStatus({ message: 'Score must be 0–100 and strength must be 0–1.', error: true });
      return;
    }
    setSubmitting(true);
    try {
      const profile = profileFromForm();
      if (editingAlertId !== null) {
        await api.updateAlert(editingAlertId, { name, parameter: JSON.stringify(profile) });
        setStatus({ message: `Signal alert "${name}" updated.`, error: false });
        setEditingAlertId(null);
        setEditingSnoozedUntil(undefined);
        setForm(INITIAL_FORM);
      } else {
        await api.createAlert({
          name,
          symbol,
          condition_type: 'signal_profile',
          parameter: JSON.stringify(profile),
        });
        setForm((current) => ({ ...current, name: '' }));
        setStatus({ message: `Signal alert "${name}" created.`, error: false });
      }
      await refresh();
    } catch (err: any) {
      setStatus({ message: err?.message || 'Failed to create signal alert.', error: true });
    } finally {
      setSubmitting(false);
    }
  };

  const toggleAlert = async (alert: Alert, enabled: boolean) => {
    try {
      await api.updateAlert(alert.id, { is_enabled: enabled });
      await refresh();
    } catch (err: any) {
      setStatus({ message: err?.message || 'Failed to update signal alert.', error: true });
    }
  };

  const snoozeAlert = async (alert: Alert, hours: number) => {
    try {
      const profile = parseProfile(alert);
      profile.snoozed_until = new Date(Date.now() + hours * 60 * 60 * 1000).toISOString();
      await api.updateAlert(alert.id, { parameter: JSON.stringify(profile) });
      setStatus({ message: `${alert.name} snoozed for ${hours}h.`, error: false });
      await refresh();
    } catch (err: any) {
      setStatus({ message: err?.message || 'Failed to snooze signal alert.', error: true });
    }
  };

  const resumeAlert = async (alert: Alert) => {
    try {
      const profile = parseProfile(alert);
      delete profile.snoozed_until;
      await api.updateAlert(alert.id, { parameter: JSON.stringify(profile) });
      setStatus({ message: `${alert.name} resumed.`, error: false });
      await refresh();
    } catch (err: any) {
      setStatus({ message: err?.message || 'Failed to resume signal alert.', error: true });
    }
  };

  const acknowledge = (triggerId: number) => {
    const next = new Set(acknowledged);
    next.add(triggerId);
    setAcknowledged(next);
    writeAcknowledged(next);
  };

  return (
    <div className="card signal-alert-card">
      <div className="signal-alert-heading">
        <div>
          <h2>Signal Alert Center</h2>
          <p className="label">Create and manage real-time trend alerts with delivery preferences and quiet hours.</p>
        </div>
        <a className="btn btn-secondary" href="#historical-replay">Open Replay</a>
      </div>

      <form className="signal-alert-form" onSubmit={handleSubmit}>
        <label><span>Name</span><input value={form.name} onChange={(event) => updateProfile('name', event.target.value)} placeholder="SPY bullish setup" maxLength={80} /></label>
        <label><span>Symbol</span><input value={form.symbol} disabled={editingAlertId !== null} onChange={(event) => updateProfile('symbol', event.target.value.toUpperCase())} maxLength={8} /></label>
        <label><span>Direction</span><select value={form.direction} onChange={(event) => updateProfile('direction', event.target.value)}><option value="bullish">Bullish</option><option value="bearish">Bearish</option><option value="any">Any</option></select></label>
        <label><span>Timeframe</span><select value={form.timeframe} onChange={(event) => updateProfile('timeframe', event.target.value)}><option value="">All timeframes</option>{ALERT_TIMEFRAMES.map((tf) => <option key={tf} value={tf}>{TIMEFRAME_LABELS[tf] || tf}</option>)}</select></label>
        <label><span>Min score</span><input type="number" min="0" max="100" step="1" value={form.minScore} onChange={(event) => updateProfile('minScore', Number(event.target.value))} /></label>
        <label><span>Min strength</span><input type="number" min="0" max="1" step="0.05" value={form.minStrength} onChange={(event) => updateProfile('minStrength', Number(event.target.value))} /></label>
        <label><span>Regime</span><select value={form.market_regime} onChange={(event) => updateProfile('market_regime', event.target.value)}><option value="any">Any regime</option><option value="risk_on">Risk on</option><option value="risk_off">Risk off</option><option value="neutral">Neutral</option><option value="transition">Transition</option></select></label>
        <label><span>Market session</span><select value={form.session} onChange={(event) => updateProfile('session', event.target.value)}><option value="any">Any session</option><option value="premarket">Premarket</option><option value="regular">Regular</option><option value="after_hours">After-hours</option></select></label>
        <label><span>Cooldown (min)</span><input type="number" min="1" max="1440" step="1" value={form.cooldown_minutes} onChange={(event) => updateProfile('cooldown_minutes', Number(event.target.value))} /></label>
        <fieldset className="signal-alert-notifications"><legend>Notification channels</legend><label className="signal-alert-check"><input type="checkbox" checked disabled /> In-app</label><label className="signal-alert-check"><input type="checkbox" checked={form.channels.includes('browser')} onChange={() => toggleChannel('browser')} /> Browser</label><label className="signal-alert-check"><input type="checkbox" checked={form.channels.includes('webhook')} onChange={() => toggleChannel('webhook')} /> Webhook</label><label className="signal-alert-check"><input type="checkbox" checked={form.channels.includes('email')} onChange={() => toggleChannel('email')} /> Email</label><button type="button" className="btn btn-secondary btn-small" onClick={() => void requestBrowserPermission()}>{typeof Notification !== 'undefined' && Notification.permission === 'granted' ? 'Browser notifications enabled' : 'Enable Browser Notifications'}</button></fieldset>
        {form.channels.includes('webhook') && <label><span>Webhook URL</span><input type="url" value={form.webhookUrl} onChange={(event) => updateProfile('webhookUrl', event.target.value)} placeholder="https://example.com/hooks/…" /></label>}
        {form.channels.includes('email') && <label><span>Email recipient</span><input type="email" value={form.emailTo} onChange={(event) => updateProfile('emailTo', event.target.value)} placeholder="you@example.com" /></label>}
        <label><span>Quiet hours start (ET)</span><input type="time" value={form.quietStart} onChange={(event) => updateProfile('quietStart', event.target.value)} /></label>
        <label><span>Quiet hours end (ET)</span><input type="time" value={form.quietEnd} onChange={(event) => updateProfile('quietEnd', event.target.value)} /></label>
        <button className="btn btn-primary" type="submit" disabled={submitting}>{submitting ? (editingAlertId !== null ? 'Saving…' : 'Creating…') : (editingAlertId !== null ? 'Save Signal Alert' : 'Create Signal Alert')}</button>
        {editingAlertId !== null && <button className="btn btn-secondary" type="button" onClick={cancelEdit}>Cancel edit</button>}
      </form>

      {status && <div className={status.error ? 'error-text' : 'info-text'} style={{ marginBottom: 8 }}>{status.message}</div>}
      {deliverySummary && deliverySummary.total > 0 && <div className="signal-alert-metrics" aria-label="Alert delivery metrics"><span><strong>{deliverySummary.total}</strong> deliveries</span><span className="delivery-metric-delivered"><strong>{deliverySummary.by_status.delivered || 0}</strong> delivered</span><span className="delivery-metric-failed"><strong>{deliverySummary.by_status.failed || 0}</strong> failed</span><span className="delivery-metric-pending"><strong>{deliverySummary.by_status.pending || 0}</strong> pending</span><span className="delivery-metric-skipped"><strong>{deliverySummary.by_status.skipped || 0}</strong> skipped</span></div>}
      {loading && <div className="empty-state signal-alert-empty">Loading signal alerts…</div>}
      {!loading && signalAlerts.length === 0 && <div className="empty-state signal-alert-empty">No signal alerts configured yet.</div>}

      {signalAlerts.length > 0 && <div className="signal-alert-list">
        {signalAlerts.map((alert) => {
          const profile = parseProfile(alert);
          const snoozedUntil = profile.snoozed_until ? Date.parse(profile.snoozed_until) : NaN;
          const snoozed = Number.isFinite(snoozedUntil) && snoozedUntil > Date.now();
          return <div className="signal-alert-row" key={alert.id}>
            <div className="signal-alert-main"><strong>{alert.name}</strong><span className="label">{alert.symbol} · {profileSummary(profile)}</span>{snoozed && <span className="signal-alert-snoozed">Snoozed until {formatETDateTime(profile.snoozed_until)} ET</span>}</div>
            <div className="signal-alert-actions">
              <label className="alert-toggle"><input type="checkbox" checked={alert.is_enabled} onChange={(event) => void toggleAlert(alert, event.target.checked)} /><span className={alert.is_enabled ? 'enabled-yes' : 'enabled-no'}>{alert.is_enabled ? 'on' : 'off'}</span></label>
              {snoozed ? <button className="btn btn-secondary btn-small" onClick={() => void resumeAlert(alert)}>Resume</button> : <select className="btn btn-secondary btn-small" defaultValue="" onChange={(event) => { const hours = Number(event.target.value); if (hours) void snoozeAlert(alert, hours); event.currentTarget.value = ''; }} aria-label={`Snooze ${alert.name}`}><option value="">Snooze…</option><option value="1">1 hour</option><option value="4">4 hours</option><option value="24">1 day</option></select>}
              <button className="btn btn-secondary btn-small" onClick={() => editAlert(alert)}>Edit</button>
              <button className="btn btn-secondary btn-small" onClick={() => void duplicateAlert(alert)}>Duplicate</button>
              <button className="btn btn-secondary btn-small" onClick={() => void removeAlert(alert)}>Delete</button>
            </div>
            {profile.channels.some((channel) => channel === 'webhook' || channel === 'email') && <div className="signal-alert-test-actions">{profile.channels.filter((channel) => channel === 'webhook' || channel === 'email').map((channel) => <button className="btn btn-secondary btn-small" key={`test-${channel}`} onClick={() => void testDelivery(alert, channel)}>Test {channel}</button>)}</div>}
            {(deliveriesByAlert.get(alert.id) || []).length > 0 && <div className="signal-alert-deliveries"><div className="signal-alert-deliveries-heading"><span className="label">Delivery history ({(deliveriesByAlert.get(alert.id) || []).length})</span><button className="btn btn-secondary btn-small" onClick={() => setExpandedDeliveries((current) => { const next = new Set(current); if (next.has(alert.id)) next.delete(alert.id); else next.add(alert.id); return next; })}>{expandedDeliveries.has(alert.id) ? 'Hide history' : 'View all'}</button></div>{(expandedDeliveries.has(alert.id) ? deliveriesByAlert.get(alert.id) || [] : (deliveriesByAlert.get(alert.id) || []).slice(0, 3)).map((delivery) => <div className="signal-alert-delivery" key={delivery.id}><span>{delivery.channel}: <strong className={`delivery-${delivery.status}`}>{delivery.status}</strong>{delivery.response ? ` · ${delivery.response}` : ''}</span>{(delivery.status === 'failed' || (delivery.status === 'skipped' && ['webhook', 'email'].includes(delivery.channel))) && <button className="btn btn-secondary btn-small" onClick={() => void retryDelivery(delivery)}>Retry</button>}</div>)}</div>}
          </div>;
        })}
      </div>}

      {visibleTriggers.length > 0 && <div className="signal-alert-triggers">
        <div className="signal-alert-triggers-heading"><h3>Unacknowledged triggers</h3><span className="label">Acknowledge items after reviewing them in replay.</span></div>
        {visibleTriggers.map((trigger) => {
          const alert = alertById.get(trigger.alert_id);
          return <div className="signal-alert-trigger" key={trigger.id}>
            <div><strong>{trigger.symbol}</strong><span className="label">{alert ? ` · ${alert.name}` : ''} · {formatETDateTime(trigger.triggered_at)} ET</span><div>{trigger.message || trigger.observed_value || 'Signal profile matched.'}</div>{trigger.ai_commentary && <div className="alert-trigger-ai-commentary"><span className="ai-badge">AI</span> {trigger.ai_commentary}</div>}</div>
            <div className="signal-alert-trigger-actions"><a className="btn btn-secondary btn-small" href="#historical-replay">Replay</a><button className="btn btn-secondary btn-small" onClick={() => openAlertConversation(trigger)}>💬 AI Hub</button><button className="btn btn-small" onClick={() => acknowledge(trigger.id)}>Acknowledge</button></div>
          </div>;
        })}
      </div>}
    </div>
  );
}

export default SignalAlertCenter;
