import React, { useEffect, useState, useCallback } from 'react';
import api, { Alert, AlertTrigger } from '../services/api';
import { formatETDateTime } from './chartMath';

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
  { value: 'signal_profile', label: 'Signal profile', hint: 'Use Signal Alert Center for guided filters' },
];

// Every signal name the scanner can emit — mirrors
// backend/scanner/scanner.py::_generate_signals. A signal_equals alert's
// parameter must match one of these exactly (the engine checks
// membership in ScanResult.signals), so this is a picker, not free text.
const SIGNAL_PARAMETERS: { value: string; label: string }[] = [
  { value: 'RSI_OVERSOLD', label: 'RSI Oversold' },
  { value: 'RSI_OVERBOUGHT', label: 'RSI Overbought' },
  { value: 'MACD_BULLISH', label: 'MACD Bullish' },
  { value: 'MACD_BEARISH', label: 'MACD Bearish' },
  { value: 'MULTI_TIMEFRAME_BULLISH', label: 'Multi-Timeframe Bullish' },
  { value: 'MULTI_TIMEFRAME_BEARISH', label: 'Multi-Timeframe Bearish' },
  { value: 'HIGH_VOLUME', label: 'High Volume' },
  { value: 'HEAVY_BUY_PRESSURE', label: 'Heavy Buy Pressure (tape)' },
  { value: 'HEAVY_SELL_PRESSURE', label: 'Heavy Sell Pressure (tape)' },
  { value: 'BLOCK_ACTIVITY', label: 'Block Activity (tape)' },
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
    case 'signal_profile':
      return <>guided signal profile</>;
    default:
      return <>{c}({p})</>;
  }
}

function formatTime(t: string | null): string {
  if (!t) return '—';
  try {
    return formatETDateTime(t);
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
  // Non-null while editing an existing alert instead of creating a new
  // one — the form is reused for both. The backend's PUT doesn't accept
  // a symbol change, so the symbol field locks while this is set.
  const [editingId, setEditingId] = useState<number | null>(null);

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

  // Version 4 AI feature 3: ai_commentary is populated asynchronously
  // (an RQ job runs after the trigger row commits) — there's no
  // discrete "job" to poll here, just re-fetch the trigger list
  // periodically so commentary appears without a manual reload.
  useEffect(() => {
    const interval = setInterval(refreshTriggers, 30_000);
    return () => clearInterval(interval);
  }, [refreshTriggers]);

  const resetForm = () => {
    setEditingId(null);
    setName('');
    setSymbol(defaultSymbol);
    setConditionType(CONDITIONS[0].value);
    setParameter('');
  };

  const handleEditClick = (a: Alert) => {
    setEditingId(a.id);
    setName(a.name);
    setSymbol(a.symbol);
    setConditionType(a.condition_type);
    setParameter(a.parameter);
    setStatus(null);
  };

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
      if (editingId !== null) {
        // Symbol isn't part of the update payload — the backend's PUT
        // doesn't support changing it (the field stays locked in the UI).
        await api.updateAlert(editingId, {
          name: payload.name,
          condition_type: payload.condition_type,
          parameter: payload.parameter,
        });
        setStatus({ msg: `Alert "${payload.name}" updated.`, isError: false });
      } else {
        await api.createAlert(payload);
        setStatus({ msg: `Alert "${payload.name}" created.`, isError: false });
      }
      resetForm();
      await refreshAlerts();
    } catch (err: any) {
      setStatus({
        msg: err?.message || `Failed to ${editingId !== null ? 'update' : 'create'} alert`,
        isError: true,
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm('Delete this alert?')) return;
    try {
      await api.deleteAlert(id);
      setStatus({ msg: 'Alert deleted.', isError: false });
      if (editingId === id) resetForm(); // was mid-edit on the alert just deleted
      await refreshAlerts();
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Failed to delete alert', isError: true });
    }
  };

  const handleClearTriggers = async () => {
    if (!window.confirm('Clear all trigger history? This does not delete your alerts.')) return;
    try {
      const { deleted } = await api.clearAlertTriggers();
      setStatus({ msg: `Cleared ${deleted} trigger${deleted === 1 ? '' : 's'}.`, isError: false });
      await refreshTriggers();
    } catch (err: any) {
      setStatus({ msg: err?.message || 'Failed to clear trigger history', isError: true });
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
        {editingId !== null
          ? 'Editing an existing alert — the symbol can\'t be changed here; delete and recreate for that.'
          : 'Create price and signal alerts that fire during the next scan.'}
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
            disabled={submitting || editingId !== null}
            title={editingId !== null ? "Can't change the symbol of an existing alert" : undefined}
          />
        </label>
        <label>
          <span>Condition</span>
          <select
            value={conditionType}
            onChange={e => {
              // The parameter's shape changes with the condition (a
              // signal name vs. a number) — a leftover value from the
              // other shape would silently submit as garbage.
              setConditionType(e.target.value);
              setParameter('');
            }}
            disabled={submitting}
          >
            {CONDITIONS.map(c => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Parameter</span>
          {conditionType === 'signal_equals' ? (
            <select
              value={parameter}
              onChange={e => setParameter(e.target.value)}
              disabled={submitting}
            >
              <option value="" disabled>Select a signal…</option>
              {SIGNAL_PARAMETERS.map(s => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={parameter}
              onChange={e => setParameter(e.target.value)}
              placeholder={currentHint}
              disabled={submitting}
            />
          )}
        </label>
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting
            ? (editingId !== null ? 'Saving…' : 'Adding…')
            : (editingId !== null ? 'Save Changes' : 'Add Alert')}
        </button>
        {editingId !== null && (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={resetForm}
            disabled={submitting}
          >
            Cancel
          </button>
        )}
      </form>

      {status && (
        <div className={status.isError ? 'error-text' : 'info-text'} style={{ marginBottom: 8 }}>
          {status.msg}
        </div>
      )}

      {triggers.length > 0 && (
        <div className="alert-triggers-list">
          <div className="alert-triggers-header">
            <span className="info-text">
              {triggers.length} trigger{triggers.length === 1 ? '' : 's'} fired in the last 24h.
            </span>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={handleClearTriggers}
              title="Permanently clear this history — does not delete your alerts"
            >
              🗑 Clear
            </button>
          </div>
          {triggers.map(t => {
            const alert = alerts.find(a => a.id === t.alert_id);
            return (
              <div key={t.id} className="alert-trigger-row">
                <div className="alert-trigger-summary">
                  <strong>{t.symbol}</strong>
                  {alert && (
                    <span className="label">
                      {' '}— {conditionLabel(alert.condition_type, alert.parameter)}
                    </span>
                  )}
                  <span className="label alert-trigger-time"> · {formatTime(t.triggered_at)}</span>
                </div>
                {t.message && <div className="alert-trigger-message">{t.message}</div>}
                {t.ai_commentary && (
                  <div className="alert-trigger-ai-commentary">
                    <span className="ai-badge">AI</span> {t.ai_commentary}
                  </div>
                )}
              </div>
            );
          })}
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
                  <td className="alerts-row-actions">
                    <button
                      className="btn btn-secondary btn-small"
                      onClick={() => handleEditClick(a)}
                      title="Edit alert"
                    >
                      ✎
                    </button>
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
