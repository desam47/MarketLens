import React, { useState, useEffect, useCallback } from 'react';
import api, { CustomIndicator } from '../services/api';

const FORMULA_TYPES = [
  { key: 'sma', label: 'Simple Moving Average' },
  { key: 'ema', label: 'Exponential Moving Average' },
  { key: 'rsi', label: 'Relative Strength Index' },
  { key: 'macd', label: 'MACD' },
  { key: 'bollinger', label: 'Bollinger Bands' },
  { key: 'atr', label: 'Average True Range' },
  { key: 'adx', label: 'Average Directional Index' },
  { key: 'obv', label: 'On-Balance Volume' },
  { key: 'roc', label: 'Rate of Change' },
  { key: 'supertrend', label: 'SuperTrend' },
];

const COLOR_PRESETS = [
  '#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444',
  '#ec4899', '#14b8a6', '#f97316', '#06b6d4', '#a855f7',
];

interface CustomIndicatorsPanelProps {
  /** Symbol+timeframe context for "Apply to chart" actions. */
  symbol: string;
  timeframe: string;
}

/**
 * Sidebar panel for managing user-defined custom indicators.
 *
 * - Lists existing indicators (active/inactive toggle)
 * - Provides an inline form to create a new indicator
 * - Lets the user delete indicators
 */
export function CustomIndicatorsPanel({ symbol, timeframe }: CustomIndicatorsPanelProps) {
  const [indicators, setIndicators] = useState<CustomIndicator[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);

  // Form state
  const [formName, setFormName] = useState('');
  const [formSlug, setFormSlug] = useState('');
  const [formFormula, setFormFormula] = useState('sma');
  const [formPeriod, setFormPeriod] = useState(20);
  const [formColor, setFormColor] = useState('#3b82f6');
  const [formLineWidth, setFormLineWidth] = useState(1.5);
  const [formDescription, setFormDescription] = useState('');

  // ── Load indicators ──────────────────────────────────────────────────

  const fetchIndicators = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await api.getCustomIndicators();
      setIndicators(list);
    } catch (e: any) {
      setError(e?.message || 'Failed to load indicators');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchIndicators();
  }, [fetchIndicators]);

  // ── Handlers ─────────────────────────────────────────────────────────

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim() || !formSlug.trim()) {
      setError('Name and slug are required');
      return;
    }
    try {
      setError(null);
      const params: Record<string, any> = { period: formPeriod };
      await api.createCustomIndicator({
        name: formName,
        slug: formSlug,
        description: formDescription || null,
        formula_type: formFormula,
        parameters: params,
        color: formColor,
        line_width: formLineWidth,
        line_style: 'solid',
        is_overlay: formFormula !== 'rsi' && formFormula !== 'macd' && formFormula !== 'atr',
        separate_pane: formFormula === 'rsi' || formFormula === 'macd' || formFormula === 'atr',
        z_index: 1,
      });
      setShowForm(false);
      setFormName('');
      setFormSlug('');
      setFormDescription('');
      setFormPeriod(20);
      setFormColor('#3b82f6');
      await fetchIndicators();
    } catch (e: any) {
      setError(e?.message || 'Failed to create indicator');
    }
  }, [formName, formSlug, formFormula, formPeriod, formColor, formLineWidth, formDescription, fetchIndicators]);

  const handleDelete = useCallback(async (id: number) => {
    if (!window.confirm('Delete this indicator?')) return;
    try {
      await api.deleteCustomIndicator(id);
      await fetchIndicators();
    } catch (e: any) {
      setError(e?.message || 'Failed to delete');
    }
  }, [fetchIndicators]);

  const handleToggle = useCallback(async (ind: CustomIndicator) => {
    try {
      await api.updateCustomIndicator(ind.id, { is_active: !ind.is_active });
      await fetchIndicators();
    } catch (e: any) {
      setError(e?.message || 'Failed to update');
    }
  }, [fetchIndicators]);

  const handleApply = useCallback(async (ind: CustomIndicator) => {
    try {
      setApplyMessage(`Computing ${ind.slug}…`);
      const result = await api.computeCustomIndicator(ind.id, symbol, timeframe, 200);
      setApplyMessage(`${ind.name}: ${result.count} values computed for ${symbol}/${timeframe}`);
      setTimeout(() => setApplyMessage(null), 3000);
    } catch (e: any) {
      setApplyMessage(`Error: ${e?.message}`);
      setTimeout(() => setApplyMessage(null), 3000);
    }
  }, [symbol, timeframe]);

  // Auto-generate slug from name
  useEffect(() => {
    if (formName && !formSlug) {
      setFormSlug(formName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''));
    }
  }, [formName, formSlug]);

  return (
    <div className="card custom-indicators-card">
      <div className="card-header-row">
        <h2>📐 Custom Indicators</h2>
        <button
          type="button"
          className="btn-sm btn-primary"
          onClick={() => setShowForm(s => !s)}
        >
          {showForm ? '✕ Cancel' : '+ New'}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {applyMessage && <div className="info-banner">{applyMessage}</div>}

      {showForm && (
        <form className="custom-indicator-form" onSubmit={handleSubmit}>
          <label className="form-row">
            <span>Name</span>
            <input
              type="text"
              value={formName}
              onChange={e => setFormName(e.target.value)}
              placeholder="My SMA-20"
              required
            />
          </label>
          <label className="form-row">
            <span>Slug</span>
            <input
              type="text"
              value={formSlug}
              onChange={e => setFormSlug(e.target.value)}
              placeholder="my-sma-20"
              pattern="[a-z0-9_-]+"
              required
            />
          </label>
          <label className="form-row">
            <span>Description</span>
            <input
              type="text"
              value={formDescription}
              onChange={e => setFormDescription(e.target.value)}
              placeholder="What this indicator does…"
            />
          </label>
          <label className="form-row">
            <span>Formula</span>
            <select
              value={formFormula}
              onChange={e => setFormFormula(e.target.value)}
            >
              {FORMULA_TYPES.map(f => (
                <option key={f.key} value={f.key}>{f.label}</option>
              ))}
            </select>
          </label>
          <label className="form-row">
            <span>Period</span>
            <input
              type="number"
              value={formPeriod}
              onChange={e => setFormPeriod(parseInt(e.target.value, 10) || 1)}
              min={1}
              max={200}
            />
          </label>
          <label className="form-row">
            <span>Color</span>
            <div className="color-row">
              <input
                type="color"
                value={formColor}
                onChange={e => setFormColor(e.target.value)}
              />
              <div className="color-presets">
                {COLOR_PRESETS.map(c => (
                  <button
                    key={c}
                    type="button"
                    className={`color-swatch${formColor === c ? ' active' : ''}`}
                    style={{ backgroundColor: c }}
                    onClick={() => setFormColor(c)}
                    title={c}
                  />
                ))}
              </div>
            </div>
          </label>
          <label className="form-row">
            <span>Line Width</span>
            <input
              type="number"
              value={formLineWidth}
              onChange={e => setFormLineWidth(parseFloat(e.target.value) || 1)}
              min={0.5}
              max={5}
              step={0.5}
            />
          </label>
          <button type="submit" className="btn btn-primary">Create Indicator</button>
        </form>
      )}

      {loading && <div className="loading">Loading indicators…</div>}

      {!loading && indicators.length === 0 && !showForm && (
        <div className="empty-state">
          No custom indicators yet. Click <strong>+ New</strong> to create one.
        </div>
      )}

      <ul className="indicator-list">
        {indicators.map(ind => (
          <li key={ind.id} className={`indicator-item${!ind.is_active ? ' inactive' : ''}`}>
            <div className="indicator-info">
              <div className="indicator-name-row">
                <span
                  className="indicator-color-dot"
                  style={{ backgroundColor: ind.color || '#3b82f6' }}
                />
                <strong>{ind.name}</strong>
                <code className="indicator-formula">
                  {ind.formula_type}({Object.entries(ind.parameters || {}).map(([k, v]) => `${k}=${v}`).join(', ')})
                </code>
              </div>
              {ind.description && <div className="indicator-desc">{ind.description}</div>}
            </div>
            <div className="indicator-actions">
              <button
                type="button"
                className="btn-sm"
                onClick={() => handleApply(ind)}
                title={`Apply to ${symbol}/${timeframe}`}
              >
                ▶ Apply
              </button>
              <button
                type="button"
                className="btn-sm"
                onClick={() => handleToggle(ind)}
                title={ind.is_active ? 'Disable' : 'Enable'}
              >
                {ind.is_active ? '👁' : '🚫'}
              </button>
              <button
                type="button"
                className="btn-sm btn-danger"
                onClick={() => handleDelete(ind.id)}
                title="Delete"
              >
                🗑
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default CustomIndicatorsPanel;
