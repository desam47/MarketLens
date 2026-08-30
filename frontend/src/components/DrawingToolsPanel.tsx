import React, { useState, useEffect, useCallback } from 'react';
import api, { DrawingTool, DrawingType } from '../services/api';

const DRAWING_TYPES: { key: DrawingType; label: string; needsEnd: boolean }[] = [
  { key: 'trend_line', label: 'Trend Line', needsEnd: true },
  { key: 'horizontal_line', label: 'Horizontal Line', needsEnd: false },
  { key: 'fib_retracement', label: 'Fib Retracement', needsEnd: true },
  { key: 'rectangle', label: 'Rectangle', needsEnd: true },
  { key: 'arrow', label: 'Arrow', needsEnd: true },
  { key: 'text', label: 'Text', needsEnd: false },
  { key: 'channel', label: 'Channel', needsEnd: true },
];

const COLOR_PRESETS = [
  '#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444',
  '#ec4899', '#14b8a6', '#f97316', '#06b6d4', '#a855f7',
];

interface DrawingToolsPanelProps {
  symbol: string;
  timeframe: string;
}

/**
 * Sidebar panel for managing chart drawings.
 *
 * - Lists existing drawings for the current symbol+timeframe
 * - Lets the user toggle visibility, delete, or create a new drawing
 */
export function DrawingToolsPanel({ symbol, timeframe }: DrawingToolsPanelProps) {
  const [drawings, setDrawings] = useState<DrawingTool[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [filterType, setFilterType] = useState<DrawingType | 'all'>('all');

  // Form state
  const [formType, setFormType] = useState<DrawingType>('trend_line');
  const [formLabel, setFormLabel] = useState('');
  const [formColor, setFormColor] = useState('#3b82f6');
  const [formLineWidth, setFormLineWidth] = useState(2.0);
  const [formStartTs, setFormStartTs] = useState('');
  const [formStartPrice, setFormStartPrice] = useState<number | ''>('');
  const [formEndTs, setFormEndTs] = useState('');
  const [formEndPrice, setFormEndPrice] = useState<number | ''>('');

  // ── Load drawings ────────────────────────────────────────────────────

  const fetchDrawings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await api.getDrawingTools({ symbol, timeframe });
      setDrawings(list);
    } catch (e: any) {
      setError(e?.message || 'Failed to load drawings');
    } finally {
      setLoading(false);
    }
  }, [symbol, timeframe]);

  useEffect(() => {
    fetchDrawings();
  }, [fetchDrawings]);

  // ── Handlers ─────────────────────────────────────────────────────────

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    const typeDef = DRAWING_TYPES.find(t => t.key === formType);
    if (!formStartTs || formStartPrice === '' || (typeDef?.needsEnd && (!formEndTs || formEndPrice === ''))) {
      setError('Start timestamp + price are required. End fields required for line types.');
      return;
    }
    try {
      setError(null);
      await api.createDrawingTool({
        symbol,
        timeframe,
        drawing_type: formType,
        label: formLabel || null,
        color: formColor,
        line_width: formLineWidth,
        line_style: 'solid',
        opacity: 1.0,
        start_timestamp: formStartTs,
        start_price: typeof formStartPrice === 'number' ? formStartPrice : 0,
        end_timestamp: typeDef?.needsEnd ? (formEndTs || null) : null,
        end_price: typeDef?.needsEnd ? (typeof formEndPrice === 'number' ? formEndPrice : null) : null,
      });
      setShowForm(false);
      setFormLabel('');
      setFormStartTs('');
      setFormStartPrice('');
      setFormEndTs('');
      setFormEndPrice('');
      await fetchDrawings();
    } catch (e: any) {
      setError(e?.message || 'Failed to create drawing');
    }
  }, [symbol, timeframe, formType, formLabel, formColor, formLineWidth, formStartTs, formStartPrice, formEndTs, formEndPrice, fetchDrawings]);

  const handleDelete = useCallback(async (id: number) => {
    if (!window.confirm('Delete this drawing?')) return;
    try {
      await api.deleteDrawingTool(id);
      await fetchDrawings();
    } catch (e: any) {
      setError(e?.message || 'Failed to delete');
    }
  }, [fetchDrawings]);

  const handleToggleVisibility = useCallback(async (d: DrawingTool) => {
    try {
      await api.updateDrawingTool(d.id, { is_visible: !d.is_visible });
      await fetchDrawings();
    } catch (e: any) {
      setError(e?.message || 'Failed to update');
    }
  }, [fetchDrawings]);

  const handleClearAll = useCallback(async () => {
    if (!window.confirm(`Delete all ${drawings.length} drawing(s) for ${symbol}/${timeframe}?`)) return;
    try {
      // Loop delete (or could add a DELETE on collection).
      await Promise.all(drawings.map(d => api.deleteDrawingTool(d.id)));
      await fetchDrawings();
    } catch (e: any) {
      setError(e?.message || 'Failed to clear');
    }
  }, [drawings, symbol, timeframe, fetchDrawings]);

  // ── Filter ───────────────────────────────────────────────────────────

  const filtered = drawings.filter(d => filterType === 'all' || d.drawing_type === filterType);

  return (
    <div className="card drawing-tools-card">
      <div className="card-header-row">
        <h2>✏️ Drawings</h2>
        <div className="card-actions">
          <select
            className="filter-select"
            value={filterType}
            onChange={e => setFilterType(e.target.value as any)}
          >
            <option value="all">All types</option>
            {DRAWING_TYPES.map(t => (
              <option key={t.key} value={t.key}>{t.label}</option>
            ))}
          </select>
          <button
            type="button"
            className="btn-sm btn-primary"
            onClick={() => setShowForm(s => !s)}
          >
            {showForm ? '✕ Cancel' : '+ New'}
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {showForm && (
        <form className="drawing-form" onSubmit={handleSubmit}>
          <label className="form-row">
            <span>Type</span>
            <select
              value={formType}
              onChange={e => setFormType(e.target.value as DrawingType)}
            >
              {DRAWING_TYPES.map(t => (
                <option key={t.key} value={t.key}>{t.label}</option>
              ))}
            </select>
          </label>
          <label className="form-row">
            <span>Label</span>
            <input
              type="text"
              value={formLabel}
              onChange={e => setFormLabel(e.target.value)}
              placeholder="Optional label"
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
              max={10}
              step={0.5}
            />
          </label>
          <label className="form-row">
            <span>Start Time</span>
            <input
              type="datetime-local"
              value={formStartTs}
              onChange={e => setFormStartTs(e.target.value)}
              required
            />
          </label>
          <label className="form-row">
            <span>Start Price</span>
            <input
              type="number"
              value={formStartPrice}
              onChange={e => setFormStartPrice(e.target.value === '' ? '' : parseFloat(e.target.value))}
              step="0.01"
              required
            />
          </label>
          {DRAWING_TYPES.find(t => t.key === formType)?.needsEnd && (
            <>
              <label className="form-row">
                <span>End Time</span>
                <input
                  type="datetime-local"
                  value={formEndTs}
                  onChange={e => setFormEndTs(e.target.value)}
                  required
                />
              </label>
              <label className="form-row">
                <span>End Price</span>
                <input
                  type="number"
                  value={formEndPrice}
                  onChange={e => setFormEndPrice(e.target.value === '' ? '' : parseFloat(e.target.value))}
                  step="0.01"
                  required
                />
              </label>
            </>
          )}
          <button type="submit" className="btn btn-primary">Create Drawing</button>
        </form>
      )}

      {loading && <div className="loading">Loading drawings…</div>}

      {!loading && drawings.length > 0 && (
        <div className="drawing-summary">
          <span>{filtered.length} of {drawings.length} visible</span>
          {drawings.length > 0 && (
            <button type="button" className="btn-sm btn-danger" onClick={handleClearAll}>
              Clear All
            </button>
          )}
        </div>
      )}

      {!loading && drawings.length === 0 && !showForm && (
        <div className="empty-state">
          No drawings for {symbol}/{timeframe} yet. Click <strong>+ New</strong> to add one.
        </div>
      )}

      <ul className="drawing-list">
        {filtered.map(d => (
          <li key={d.id} className={`drawing-item${!d.is_visible ? ' hidden' : ''}`}>
            <div className="drawing-info">
              <div className="drawing-name-row">
                <span
                  className="drawing-color-dot"
                  style={{ backgroundColor: d.color || '#3b82f6' }}
                />
                <strong>{d.label || d.drawing_type.replace('_', ' ')}</strong>
                <code className="drawing-type">{d.drawing_type}</code>
              </div>
              <div className="drawing-coords">
                <span>From: {d.start_timestamp.slice(0, 16)} @ ${d.start_price.toFixed(2)}</span>
                {d.end_timestamp && d.end_price !== null && (
                  <span> → {d.end_timestamp.slice(0, 16)} @ ${d.end_price.toFixed(2)}</span>
                )}
              </div>
            </div>
            <div className="drawing-actions">
              <button
                type="button"
                className="btn-sm"
                onClick={() => handleToggleVisibility(d)}
                title={d.is_visible ? 'Hide' : 'Show'}
              >
                {d.is_visible ? '👁' : '🙈'}
              </button>
              <button
                type="button"
                className="btn-sm btn-danger"
                onClick={() => handleDelete(d.id)}
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

export default DrawingToolsPanel;
