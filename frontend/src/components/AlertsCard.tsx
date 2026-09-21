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
  { value: 'trend_crosses_above_70', label: 'Trend crosses bullish', hint: 'No parameter required' },
  { value: 'trend_crosses_below_70', label: 'Trend crosses bearish', hint: 'No parameter required' },
  { value: 'trend_direction_changes', label: 'Trend direction changes', hint: 'No parameter required' },
  { value: 'trend_strengthens', label: 'Trend strengthens', hint: 'minimum score delta (e.g. 5)' },
  { value: 'trend_weakens', label: 'Trend weakens', hint: 'minimum score delta (e.g. 5)' },
  { value: 'full_timeframe_alignment', label: 'Multi-timeframe alignment', hint: 'No parameter required' },
  { value: 'timeframe_conflict', label: 'Multi-timeframe conflict', hint: 'No parameter required' },
  { value: 'volume_expansion', label: 'Volume spike', hint: 'volume multiple (e.g. 2 for 2× average)' },
  { value: 'divergence', label: 'Momentum divergence', hint: 'Choose positive or negative' },
  { value: 'breakout', label: 'Breakout', hint: 'lookback bars (e.g. 20)' },
  { value: 'breakdown', label: 'Breakdown', hint: 'lookback bars (e.g. 20)' },
  { value: 'market_regime_change', label: 'Market regime change', hint: 'Optional regime filter' },
  { value: 'news_arrival', label: 'News arrival', hint: 'minimum relevance 0–1 (e.g. 0.5)' },
  { value: 'insider_sentiment_change', label: 'Insider sentiment change', hint: 'minimum sentiment delta (e.g. 0.2)' },
  { value: 'options_activity_change', label: 'Options activity change', hint: 'minimum volume/OI change % (e.g. 50)' },
  { value: 'earnings_approaching', label: 'Earnings approaching', hint: 'days before estimated earnings' },
  { value: 'spread_widening', label: 'Spread widening (live)', hint: 'minimum widening in bps (e.g. 3)' },
  { value: 'bid_ask_imbalance', label: 'Bid/ask imbalance (live)', hint: 'bid positive / ask negative (e.g. 0.2 or -0.2)' },
  { value: 'large_print_activity', label: 'Large-print activity (live)', hint: 'minimum recent block prints (e.g. 1)' },
  { value: 'tape_pressure_reversal', label: 'Tape-pressure reversal (live)', hint: 'buy, sell, or any' },
  { value: 'trade_rate_spike', label: 'Trade-rate spike (live)', hint: 'minimum acceleration (e.g. 1.5)' },
  { value: 'live_volume_acceleration', label: 'Live volume acceleration', hint: 'minimum acceleration (e.g. 1.5)' },
  { value: 'stream_status', label: 'Webull stream status', hint: 'connected or disconnected' },
  { value: 'symbol_data_status', label: 'Symbol data status', hint: 'live, stale, or rest_fallback' },
  { value: 'signal_profile', label: 'Signal profile', hint: 'Use Signal Alert Center for guided filters' },
];

const NO_PARAMETER_CONDITIONS = new Set([
  'trend_crosses_above_70', 'trend_crosses_below_70', 'trend_direction_changes',
  'full_timeframe_alignment', 'timeframe_conflict',
]);

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
  { value: 'VOLUME_SPIKE', label: 'Volume Spike' },
  { value: 'RSI_OVERSOLD_REVERSAL', label: 'RSI Oversold Reversal' },
  { value: 'BREAKOUT', label: 'Breakout' },
  { value: 'BREAKDOWN', label: 'Breakdown' },
  { value: 'VOLATILITY_CONTRACTION', label: 'Volatility Contraction' },
  { value: 'VOLATILITY_EXPANSION', label: 'Volatility Expansion' },
  { value: 'RELATIVE_STRENGTH_OUTPERFORMER', label: 'Relative Strength Outperformer' },
  { value: 'RELATIVE_STRENGTH_UNDERPERFORMER', label: 'Relative Strength Underperformer' },
  { value: 'HEAVY_BUY_PRESSURE', label: 'Heavy Buy Pressure (tape)' },
  { value: 'HEAVY_SELL_PRESSURE', label: 'Heavy Sell Pressure (tape)' },
  { value: 'BLOCK_ACTIVITY', label: 'Block Activity (tape)' },
];

// Keep the canonical arrays stable for backwards-compatible defaults and
// tests, but present both pickers in a predictable alphabetical order.
const SORTED_CONDITIONS = [...CONDITIONS].sort((left, right) => left.label.localeCompare(right.label));
const SORTED_SIGNAL_PARAMETERS = [...SIGNAL_PARAMETERS].sort((left, right) => left.label.localeCompare(right.label));

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
    case 'trend_crosses_above_70':
      return <>trend crosses bullish threshold</>;
    case 'trend_crosses_below_70':
      return <>trend crosses bearish threshold</>;
    case 'trend_direction_changes':
      return <>trend direction changes</>;
    case 'full_timeframe_alignment':
      return <>all timeframes align</>;
    case 'timeframe_conflict':
      return <>timeframes conflict</>;
    case 'volume_expansion':
      return <>volume ≥ {p || '2'}× average</>;
    case 'divergence':
      return <>{p || 'negative'} divergence</>;
    case 'breakout':
      return <>breakout ({p || '20'} bars)</>;
    case 'breakdown':
      return <>breakdown ({p || '20'} bars)</>;
    case 'market_regime_change':
      return <>market regime changes{p ? ` to ${p}` : ''}</>;
    case 'news_arrival':
      return <>new news (relevance ≥ {p || '0'})</>;
    case 'insider_sentiment_change':
      return <>insider sentiment Δ ≥ {p || '0.2'}</>;
    case 'options_activity_change':
      return <>options volume/OI Δ ≥ {p || '50'}%</>;
    case 'earnings_approaching':
      return <>earnings expected within {p} days</>;
    case 'spread_widening':
      return <>live spread widened ≥ {p || '3'} bps</>;
    case 'bid_ask_imbalance':
      return <>live bid/ask imbalance {p || '0.2'}</>;
    case 'large_print_activity':
      return <>live large-print activity ≥ {p || '1'}</>;
    case 'tape_pressure_reversal':
      return <>live tape reverses {p || 'any'} side</>;
    case 'trade_rate_spike':
      return <>live trade rate ≥ {p || '1.5'}× baseline</>;
    case 'live_volume_acceleration':
      return <>live volume ≥ {p || '1.5'}× baseline</>;
    case 'stream_status':
      return <>Webull stream is {p || 'disconnected'}</>;
    case 'symbol_data_status':
      return <>symbol data is {p || 'rest_fallback'}</>;
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
    if (!payload.name || !payload.symbol || (!NO_PARAMETER_CONDITIONS.has(payload.condition_type) && !payload.parameter && payload.condition_type !== 'market_regime_change')) {
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
          : 'Create price, scanner, technical, and provider-data alerts that fire during the next scan.'}
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
            {SORTED_CONDITIONS.map(c => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Parameter{NO_PARAMETER_CONDITIONS.has(conditionType) ? ' (optional)' : ''}</span>
          {NO_PARAMETER_CONDITIONS.has(conditionType) ? (
            <input type="text" value="Not required" disabled aria-label="Parameter not required" />
          ) : conditionType === 'signal_equals' ? (
            <select
              value={parameter}
              onChange={e => setParameter(e.target.value)}
              disabled={submitting}
            >
              <option value="" disabled>Select a signal…</option>
              {SORTED_SIGNAL_PARAMETERS.map(s => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          ) : conditionType === 'divergence' ? (
            <select value={parameter} onChange={e => setParameter(e.target.value)} disabled={submitting}>
              <option value="" disabled>Select divergence…</option>
              <option value="negative">Negative (price up, momentum weak)</option>
              <option value="positive">Positive (price down, momentum strong)</option>
            </select>
          ) : conditionType === 'market_regime_change' ? (
            <select value={parameter} onChange={e => setParameter(e.target.value)} disabled={submitting}>
              <option value="">Any new regime</option>
              <option value="risk_on">Risk on</option>
              <option value="risk_off">Risk off</option>
              <option value="neutral">Neutral</option>
              <option value="transition">Transition</option>
            </select>
          ) : conditionType === 'earnings_approaching' ? (
            <select value={parameter} onChange={e => setParameter(e.target.value)} disabled={submitting}>
              <option value="" disabled>Select lead time…</option>
              {[3, 7, 14, 30].map(days => <option key={days} value={days}>{days} days</option>)}
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
        <div className="error-text">
          ⚠ Failed to load alerts: {loadError}
          <button className="btn btn-small data-state-retry" onClick={() => { void refreshAlerts(); void refreshTriggers(); }}>Retry</button>
        </div>
      )}

      {loading && !loadError && <p className="empty-state" role="status">Loading alerts…</p>}

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
