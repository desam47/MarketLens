import React, { useEffect, useState, useCallback } from 'react';
import api, { Alert, AlertTrigger } from '../services/api';
import { parseET } from './chartMath';

interface AlertsCardProps {
  /** Optional symbol to prefill the form with. */
  defaultSymbol?: string;
}

// Mirrors the constants in backend/alerts/conditions.py — the API
// rejects unknown condition_types with 422, so the dropdown is the
// canonical list users can pick from.
const CONDITIONS: { value: string; label: string; hint: string }[] = [
  { value: 'signal_equals', label: 'Signal equals', hint: 'signal name (e.g. RSI_OVERSOLD)' },
  { value: 'price_above', label: 'Price above', hint: 'price threshold (e.g. 150.00)' },
  { value: 'price_below', label: 'Price below', hint: 'price threshold (e.g. 140.00)' },
  { value: 'pct_change_above', label: 'Pct change above', hint: 'percent (e.g. 5 for 5%)' },
];

function conditionLabel(c: string, p: string): React.ReactNode {
  // Short human-readable rule string per condition type. Mirrors the
  // backend's VALID_CONDITION_TYPES set in alerts/conditions.py.
  switch (c) {
    case 'signal_equals':
      return <>signal == <code>{p}</code></>;
    case 'price_above':
      return <>price &gt; {p}</>;
    case 'price_below':
      return <>price &lt; {p}</>;
    case 'pct_change_above':
      return <>Δ% &gt; {p}%</>;
    default:
      return <>{c}({p})</>;
  }
}

function formatTime(t: string | null): string {
  if (!t) return '—';
  try {
    return parseET(t).toLocaleString();
  } catch {
    return t;
  }
}

export function AlertsCard({ defaultSymbol = '' }: AlertsCardProps) {
  // Form state
  const [name, setName] = useState('');
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [conditionType, setConditionType] = useState(CONDITIONS[0].value);
  const [parameter, setParameter] = useState('');

  // List state
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [triggers, setTriggers] = useState<AlertTrigger[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [status, setStatus] = useState<{ msg: string; isError: boolean } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Keep the form's symbol in sync with the page-level symbol.
  useEffect(() => {
    if (defaultSymbol) setSymbol(defaultSymbol);
  }, [defaultSymbol]);

  const refreshAlerts = useCallback(async () => {
    try {
      const list = await api.getAlerts();
      setAlerts(list);
      setLoadError(null);
    } catch (err: any) {
      setLoadError(err?.message || 'Failed to load alerts');
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshTriggers = useCallback(async () => {
    try {
      const list = await api.getActiveAlertTriggers();
      setTriggers(list);
    } catch {
      // Banner is best-effort; don't surface a separate error.
    }
  }, []);

  useEffect(() => {
    refreshAlerts();
    refreshTriggers();
  }, [refreshAlerts, refreshTriggers]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload = {
      name: name.trim(),
      symbol: symbol.trim().toUpperCase(),
      condition_type: conditionType,
      parameter: parameter.trim(),
    };
    if (!payload.name || !payload.symbol || !payload.parameter) {
      setStatus({ msg: 'All fields are required.', isError: true });
      return;
    }
    setSubmitting(true);
    try {
      await api.createAlert(payload);
      setStatus({ msg: `Alert "${payload.name}" created.`, isError: false });
      setName('');
      setParameter('');
      await refreshAlerts();
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Failed to create alert', isError: true });
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm('Delete this alert?')) return;
    try {
      await api.deleteAlert(id);
      setStatus({ msg: 'Alert deleted.', isError: false });
      await refreshAlerts();
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Failed to delete alert', isError: true });
    }
  };

  const handleToggle = async (id: number, enabled: boolean) => {
    try {
      await api.updateAlert(id, { is_enabled: enabled });
      setStatus({ msg: `Alert ${enabled ? 'enabled' : 'disabled'}.`, isError: false });
      await refreshAlerts();
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Failed to update alert', isError: true });
      // Revert the optimistic toggle by re-fetching the list.
      await refreshAlerts();
    }
  };

  const currentHint = CONDITIONS.find(c => c.value === conditionType)?.hint || '';

  return (
    <div className="card alerts-card">
      <h2>Alerts</h2>
      <p className="label" style={{ marginTop: 0 }}>
        Create price and signal alerts that fire during the next scan.
      </p>

      <form className="alerts-form" onSubmit={handleSubmit}>
        <label>
          <span>Alert Name</span>
          <input
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="e.g. AAPL RSI Oversold"
            maxLength={80}
            disabled={submitting}
          />
        </label>
        <label>
          <span>Symbol</span>
          <input
            type="text"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            placeholder="AAPL"
            maxLength={5}
            disabled={submitting}
          />
        </label>
        <label>
          <span>Condition</span>
          <select
            value={conditionType}
            onChange={e => setConditionType(e.target.value)}
            disabled={submitting}
          >
            {CONDITIONS.map(c => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Parameter</span>
          <input
            type="text"
            value={parameter}
            onChange={e => setParameter(e.target.value)}
            placeholder={currentHint}
            disabled={submitting}
          />
        </label>
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? 'Adding…' : 'Add Alert'}
        </button>
      </form>

      {status && (
        <div className={status.isError ? 'error-text' : 'info-text'} style={{ marginBottom: 8 }}>
          {status.msg}
        </div>
      )}

      {triggers.length > 0 && (
        <div className="info-text" style={{ marginBottom: 8 }}>
          {triggers.length} trigger{triggers.length === 1 ? '' : 's'} fired in the last 24h.
        </div>
      )}

      {loadError && (
        <div className="error-text">⚠ Failed to load alerts: {loadError}</div>
      )}

      {!loading && !loadError && alerts.length === 0 && (
        <p className="empty-state">No alerts configured.</p>
      )}

      {alerts.length > 0 && (
        <div className="data-grid">
          <table className="alerts-table trades-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Symbol</th>
                <th>Condition</th>
                <th>Enabled</th>
                <th>Updated</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {alerts.map(a => (
                <tr key={a.id}>
                  <td>{a.name}</td>
                  <td><strong>{a.symbol}</strong></td>
                  <td>{conditionLabel(a.condition_type, a.parameter)}</td>
                  <td>
                    <label className="alert-toggle">
                      <input
                        type="checkbox"
                        checked={a.is_enabled}
                        onChange={e => handleToggle(a.id, e.target.checked)}
                      />
                      <span className={a.is_enabled ? 'enabled-yes' : 'enabled-no'}>
                        {a.is_enabled ? 'on' : 'off'}
                      </span>
                    </label>
                  </td>
                  <td className="label">{formatTime(a.updated_at)}</td>
                  <td>
                    <button
                      className="btn btn-danger btn-remove"
                      onClick={() => handleDelete(a.id)}
                      title="Delete alert"
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default AlertsCard;
