/**
 * AITemplatesPanel — Phase 2.4.5 AI prompt template manager.
 *
 * A sidebar panel for managing user-defined AI analysis templates.
 *
 * Features:
 *   - List templates with active/default/system badges
 *   - Create / edit / delete templates
 *   - Preview a template's rendered system prompt with real variable values
 *   - Run an analysis using a selected template
 */
import React, { useState, useEffect, useCallback } from 'react';
import api, { AITemplate, AITemplatePreview } from '../services/api';
import { renderTemplate, type RenderContext } from './templateRenderer';

interface AITemplatesPanelProps {
  /** Symbol+timeframe context for "Run with template" actions. */
  symbol: string;
  timeframe: string;
}

// ── helpers ────────────────────────────────────────────────────────────────

function badgeClass(tmpl: AITemplate): string {
  if (tmpl.is_system) return 'tmpl-badge tmpl-badge-system';
  if (tmpl.is_default) return 'tmpl-badge tmpl-badge-default';
  if (!tmpl.is_active) return 'tmpl-badge tmpl-badge-inactive';
  return 'tmpl-badge tmpl-badge-active';
}

function badgeLabel(tmpl: AITemplate): string {
  if (tmpl.is_system) return 'system';
  if (tmpl.is_default) return 'default';
  if (!tmpl.is_active) return 'inactive';
  return 'active';
}

// ── Preview row ────────────────────────────────────────────────────────────

interface PreviewRowProps {
  tmpl: AITemplate;
  symbol: string;
  timeframe: string;
  onRunAnalysis: (templateId: number) => void;
}

function PreviewRow({ tmpl, symbol, timeframe, onRunAnalysis }: PreviewRowProps) {
  const [preview, setPreview] = useState<AITemplatePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [runLoading, setRunLoading] = useState(false);

  const loadPreview = useCallback(async () => {
    if (preview) return; // already loaded
    setLoading(true);
    try {
      const p = await api.previewAITemplate(tmpl.id, symbol, timeframe);
      setPreview(p);
    } catch (_) {
      /* non-fatal */
    } finally {
      setLoading(false);
    }
  }, [tmpl.id, symbol, timeframe, preview]);

  const handleToggle = () => {
    if (!expanded) loadPreview();
    setExpanded(e => !e);
  };

  const handleRun = async () => {
    setRunLoading(true);
    try {
      onRunAnalysis(tmpl.id);
    } finally {
      setRunLoading(false);
    }
  };

  const handleRunBackground = async () => {
    // Trigger the AIAnalysisPanel's background runner via the
    // DOM-attached method (cross-component hookup — see
    // AIAnalysisPanel's useEffect that attaches runBackground).
    const el = document.getElementById('ai-analysis-panel') as any;
    if (el && typeof el.runBackground === 'function') {
      el.runBackground(tmpl.id);
    } else {
      // Fall back to sync if the panel isn't on this page.
      onRunAnalysis(tmpl.id);
    }
  };

  return (
    <div className="tmpl-preview-row">
      <button
        type="button"
        className="tmpl-preview-toggle"
        onClick={handleToggle}
        title={expanded ? 'Collapse preview' : 'Expand preview + run'}
      >
        {expanded ? '▾' : '▸'} Preview
        {preview && preview.missing_variables.length > 0 && (
          <span className="tmpl-missing-warn" title="Missing variables">
            {' '}⚠ {preview.missing_variables.join(', ')}
          </span>
        )}
      </button>
      <div className="tmpl-preview-actions">
        <button
          type="button"
          className="btn btn-sm"
          onClick={handleRun}
          disabled={runLoading}
          title={`Run AI analysis synchronously for ${symbol} using "${tmpl.name}"`}
        >
          {runLoading ? '…' : '▶ Analyze'}
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={handleRunBackground}
          title={`Queue background job for ${symbol} using "${tmpl.name}" (polls until done)`}
        >
          ⏱ Background
        </button>
      </div>

      {expanded && (
        <div className="tmpl-preview-body">
          {loading && <div className="tmpl-preview-loading">Rendering preview…</div>}
          {!loading && preview && (
            <>
              <div className="tmpl-preview-label">Variables used:</div>
              {Object.entries(preview.variables_used).map(([k, v]) => (
                <div key={k} className="tmpl-var-chip">
                  <code>{`{{${k}}}`}</code> → <code>{v}</code>
                </div>
              ))}
              {preview.missing_variables.length > 0 && (
                <>
                  <div className="tmpl-preview-label">Missing variables:</div>
                  {preview.missing_variables.map(v => (
                    <div key={v} className="tmpl-var-chip tmpl-var-chip-missing">
                      <code>{`{{${v}}}`}</code>
                    </div>
                  ))}
                </>
              )}
              <div className="tmpl-preview-label">Rendered system prompt:</div>
              <pre className="tmpl-rendered-prompt">{preview.system_prompt_rendered}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main panel ─────────────────────────────────────────────────────────────

export function AITemplatesPanel({ symbol, timeframe }: AITemplatesPanelProps) {
  const [templates, setTemplates] = useState<AITemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mode: 'list' | 'create' | 'edit'
  const [mode, setMode] = useState<'list' | 'create' | 'edit'>('list');
  const [editing, setEditing] = useState<AITemplate | null>(null);

  // Shared form state
  const [formName, setFormName] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formPrompt, setFormPrompt] = useState('');
  const [formInstructions, setFormInstructions] = useState('');
  const [formVariables, setFormVariables] = useState('symbol, timeframe');
  const [formActive, setFormActive] = useState(true);
  const [formDefault, setFormDefault] = useState(false);
  const [formSubmitting, setFormSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Preview state (for the create/edit preview tab)
  const [previewResult, setPreviewResult] = useState<AITemplatePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // ── Load ────────────────────────────────────────────────────────────────

  const fetchTemplates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await api.getAITemplates();
      setTemplates(list);
    } catch (e: any) {
      setError(e?.message || 'Failed to load templates');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTemplates();
  }, [fetchTemplates]);

  // ── Form helpers ────────────────────────────────────────────────────────

  const openCreate = () => {
    setEditing(null);
    setFormName('');
    setFormDescription('');
    setFormPrompt('Analyze {{symbol}} on {{timeframe}} and summarize the key opportunities and risks.');
    setFormInstructions('');
    setFormVariables('symbol, timeframe');
    setFormActive(true);
    setFormDefault(false);
    setFormError(null);
    setPreviewResult(null);
    setMode('create');
  };

  // Render context for client-side template preview — built-in variables only.
  const renderCtx: RenderContext = { symbol, timeframe };

  const openEdit = (tmpl: AITemplate) => {
    setEditing(tmpl);
    setFormName(tmpl.name);
    setFormDescription(tmpl.description || '');
    setFormPrompt(tmpl.system_prompt);
    setFormInstructions(tmpl.user_instructions || '');
    setFormVariables((tmpl.variables || []).join(', '));
    setFormActive(tmpl.is_active);
    setFormDefault(tmpl.is_default);
    setFormError(null);
    setPreviewResult(null);
    setMode('edit');
  };

  const cancelForm = () => {
    setMode('list');
    setEditing(null);
    setFormError(null);
  };

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim()) { setFormError('Name is required'); return; }
    if (!formPrompt.trim() || formPrompt.trim().length < 10) {
      setFormError('System prompt must be at least 10 characters'); return;
    }
    const variables = formVariables
      .split(',')
      .map(v => v.trim())
      .filter(Boolean);
    setFormSubmitting(true);
    setFormError(null);
    try {
      const payload = {
        name: formName.trim(),
        description: formDescription.trim() || undefined,
        system_prompt: formPrompt.trim(),
        user_instructions: formInstructions.trim() || undefined,
        variables,
        is_active: formActive,
        is_default: formDefault,
      };
      if (mode === 'create') {
        await api.createAITemplate(payload);
      } else if (editing) {
        await api.updateAITemplate(editing.id, payload);
      }
      setMode('list');
      setEditing(null);
      await fetchTemplates();
    } catch (err: any) {
      setFormError(err?.message || 'Failed to save template');
    } finally {
      setFormSubmitting(false);
    }
  }, [mode, editing, formName, formDescription, formPrompt, formInstructions, formVariables, formActive, formDefault, fetchTemplates]);

  const handleDelete = useCallback(async (tmpl: AITemplate) => {
    if (!window.confirm(`Delete template "${tmpl.name}"?`)) return;
    try {
      await api.deleteAITemplate(tmpl.id);
      await fetchTemplates();
    } catch (err: any) {
      setError(err?.message || 'Failed to delete template');
    }
  }, [fetchTemplates]);

  const handleToggleActive = useCallback(async (tmpl: AITemplate) => {
    try {
      await api.updateAITemplate(tmpl.id, { is_active: !tmpl.is_active });
      await fetchTemplates();
    } catch (err: any) {
      setError(err?.message || 'Failed to update template');
    }
  }, [fetchTemplates]);

  const handleSetDefault = useCallback(async (tmpl: AITemplate) => {
    try {
      await api.updateAITemplate(tmpl.id, { is_default: true });
      await fetchTemplates();
    } catch (err: any) {
      setError(err?.message || 'Failed to set default');
    }
  }, [fetchTemplates]);

  const handlePreview = async () => {
    if (!formPrompt.trim()) return;
    setPreviewLoading(true);
    try {
      const result = renderTemplate(formPrompt, renderCtx);
      setPreviewResult({
        template_id: editing?.id ?? 0,
        system_prompt_rendered: result.system_prompt_rendered,
        variables_used: result.variables_used,
        missing_variables: result.missing_variables,
      });
    } catch (_) {
      /* non-fatal */
    } finally {
      setPreviewLoading(false);
    }
  };

  // ── Run analysis with selected template ────────────────────────────────

  const [runMessage, setRunMessage] = useState<string | null>(null);

  const handleRunAnalysis = useCallback(async (templateId: number) => {
    try {
      setRunMessage(`Running analysis for ${symbol}…`);
      await api.analyzeSymbol(symbol, timeframe, { template_id: templateId });
      setRunMessage('Analysis complete!');
      setTimeout(() => setRunMessage(null), 3000);
    } catch (err: any) {
      setRunMessage(`Error: ${err?.message}`);
      setTimeout(() => setRunMessage(null), 4000);
    }
  }, [symbol, timeframe]);

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="card ai-templates-card">
      <div className="card-header-row">
        <h2>📝 AI Templates</h2>
        {mode === 'list' && (
          <button type="button" className="btn-sm btn-primary" onClick={openCreate}>
            + New
          </button>
        )}
      </div>

      {error && <div className="error-banner">{error}<button className="btn btn-small data-state-retry" onClick={() => void fetchTemplates()}>Retry</button></div>}
      {runMessage && <div className={`info-banner ${runMessage.startsWith('Error') ? 'error-banner' : ''}`}>{runMessage}</div>}

      {/* ── List view ─────────────────────────────────────────────── */}
      {mode === 'list' && (
        <>
          {loading && <div className="loading">Loading templates…</div>}

          {!loading && templates.length === 0 && (
            <div className="empty-state">
              No templates yet. Click <strong>+ New</strong> to create your first AI template.
            </div>
          )}

          {!loading && templates.length > 0 && (
            <table className="tmpl-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {templates.map(tmpl => (
                  <React.Fragment key={tmpl.id}>
                    <tr className={`tmpl-row${!tmpl.is_active ? ' tmpl-row-inactive' : ''}`}>
                      <td>
                        <div className="tmpl-name-cell">
                          <span className="tmpl-name">{tmpl.name}</span>
                          {tmpl.description && (
                            <span className="tmpl-desc">{tmpl.description}</span>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="tmpl-badges">
                          <span className={badgeClass(tmpl)}>{badgeLabel(tmpl)}</span>
                          {tmpl.is_system && (
                            <span className="tmpl-badge tmpl-badge-system">system</span>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="tmpl-row-actions">
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => openEdit(tmpl)}
                            title="Edit template"
                          >
                            ✏️ Edit
                          </button>
                          {!tmpl.is_system && (
                            <>
                              <button
                                type="button"
                                className="btn btn-sm"
                                onClick={() => handleToggleActive(tmpl)}
                                title={tmpl.is_active ? 'Deactivate' : 'Activate'}
                              >
                                {tmpl.is_active ? '🚫' : '✅'}
                              </button>
                              {!tmpl.is_default && (
                                <button
                                  type="button"
                                  className="btn btn-sm"
                                  onClick={() => handleSetDefault(tmpl)}
                                  title="Set as default"
                                >
                                  ⭐
                                </button>
                              )}
                              <button
                                type="button"
                                className="btn btn-sm btn-danger"
                                onClick={() => handleDelete(tmpl)}
                                title="Delete"
                              >
                                🗑
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                    <tr>
                      <td colSpan={3} className="tmpl-preview-cell">
                        {tmpl.is_active && (
                          <PreviewRow
                            tmpl={tmpl}
                            symbol={symbol}
                            timeframe={timeframe}
                            onRunAnalysis={handleRunAnalysis}
                          />
                        )}
                      </td>
                    </tr>
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {/* ── Create / Edit form ────────────────────────────────────── */}
      {(mode === 'create' || mode === 'edit') && (
        <form className="tmpl-form" onSubmit={handleSubmit}>
          <div className="tmpl-form-header">
            <h3>{mode === 'create' ? 'New Template' : `Edit: ${editing?.name}`}</h3>
            <button type="button" className="btn btn-sm" onClick={cancelForm}>
              ✕ Cancel
            </button>
          </div>

          {formError && <div className="error-banner">{formError}</div>}

          <label className="form-row">
            <span>Name</span>
            <input
              type="text"
              value={formName}
              onChange={e => setFormName(e.target.value)}
              placeholder="Risk Focus"
              maxLength={100}
              required
            />
          </label>

          <label className="form-row">
            <span>Description</span>
            <input
              type="text"
              value={formDescription}
              onChange={e => setFormDescription(e.target.value)}
              placeholder="Brief description of this template's focus…"
              maxLength={500}
            />
          </label>

          <label className="form-row">
            <span>Variables</span>
            <input
              type="text"
              value={formVariables}
              onChange={e => setFormVariables(e.target.value)}
              placeholder="symbol, timeframe, sector"
              title="Comma-separated list of variable names used in the system prompt"
            />
            <span className="form-hint">Comma-separated list: symbol, timeframe, sector…</span>
          </label>

          <label className="form-row form-row-textarea">
            <span>System Prompt</span>
            <textarea
              className="tmpl-prompt-textarea"
              value={formPrompt}
              onChange={e => setFormPrompt(e.target.value)}
              placeholder="You are a financial analyst. Analyze {{symbol}} on {{timeframe}}…"
              rows={8}
              maxLength={10000}
              required
            />
            <span className="form-hint">
              Use <code>{'{{symbol}}'}</code> and <code>{'{{timeframe}}'}</code> as placeholders.
              Min 10 characters.
            </span>
          </label>

          <label className="form-row form-row-textarea">
            <span>User Instructions</span>
            <textarea
              className="tmpl-instructions-textarea"
              value={formInstructions}
              onChange={e => setFormInstructions(e.target.value)}
              placeholder="Optional: extra instructions appended after the system prompt…"
              rows={3}
              maxLength={5000}
            />
          </label>

          <div className="tmpl-form-toggles">
            <label className="tmpl-toggle-row">
              <input
                type="checkbox"
                checked={formActive}
                onChange={e => setFormActive(e.target.checked)}
              />
              <span>Active</span>
            </label>
            <label className="tmpl-toggle-row">
              <input
                type="checkbox"
                checked={formDefault}
                onChange={e => setFormDefault(e.target.checked)}
              />
              <span>Default</span>
            </label>
          </div>

          {/* Inline preview */}
          <div className="tmpl-form-preview">
            <div className="tmpl-form-preview-header">
              <span>Preview</span>
              <button
                type="button"
                className="btn btn-sm"
                onClick={handlePreview}
                disabled={previewLoading}
              >
                {previewLoading ? 'Rendering…' : '🔍 Render preview'}
              </button>
            </div>
            {previewResult && (
              <div className="tmpl-preview-output">
                {Object.entries(previewResult.variables_used).map(([k, v]) => (
                  <div key={k} className="tmpl-var-chip">
                    <code>{`{{${k}}}`}</code> → <code>{v}</code>
                  </div>
                ))}
                {previewResult.missing_variables.length > 0 && (
                  <div className="tmpl-var-chip tmpl-var-chip-missing">
                    Missing: {previewResult.missing_variables.map(v => `{{${v}}}`).join(', ')}
                  </div>
                )}
                <pre className="tmpl-rendered-prompt">{previewResult.system_prompt_rendered}</pre>
              </div>
            )}
          </div>

          <div className="tmpl-form-actions">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={formSubmitting}
            >
              {formSubmitting ? 'Saving…' : mode === 'create' ? 'Create Template' : 'Save Changes'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

export default AITemplatesPanel;
