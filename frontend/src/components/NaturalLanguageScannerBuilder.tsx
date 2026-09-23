/**
 * NaturalLanguageScannerBuilder — translate a question into the exact
 * editable FilterBuilder payload.  Translation never executes a scan; only
 * the user pressing "Use editable filters" hands it to ScannerPage.
 */
import React, { useState } from 'react';
import api, { FilterSpec, NLScannerPreviewResponse } from '../services/api';

interface NaturalLanguageScannerBuilderProps {
  watchlistId: number | null;
  disabled?: boolean;
  onUsePreview: (filters: FilterSpec[], match: 'AND' | 'OR') => void;
}

const EXAMPLES = [
  'Oversold reversals with rising volume',
  'Premarket breakouts with tight spread below 10 bps',
  'Stocks above the 50 day SMA and avoid earnings within 7 days',
];

function filterLabel(filter: FilterSpec): string {
  const name = filter.type.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
  const params = Object.entries(filter.params)
    .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`)
    .join(' · ');
  return params ? `${name} — ${params}` : name;
}

export function NaturalLanguageScannerBuilder({
  watchlistId,
  disabled = false,
  onUsePreview,
}: NaturalLanguageScannerBuilderProps) {
  const [query, setQuery] = useState('');
  const [preview, setPreview] = useState<NLScannerPreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const translate = async (value = query) => {
    const normalized = value.trim();
    if (!normalized || disabled) return;
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      const response = await api.previewScannerFilters({
        query: normalized,
        watchlist_id: watchlistId,
        scope: 'watchlist',
      });
      setPreview(response);
    } catch (cause: any) {
      setError(cause?.message || 'Could not translate the scanner request.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="nl-scanner-builder" aria-label="Natural-language scanner builder">
      <div className="nl-scanner-builder-heading">
        <div>
          <h2>Build a scan in plain English</h2>
          <p>Preview filters first, then edit and run the exact same filters below.</p>
        </div>
        <span className="nl-scanner-builder-status">No scan runs during preview</span>
      </div>
      <form
        className="nl-scanner-builder-form"
        onSubmit={event => {
          event.preventDefault();
          void translate();
        }}
      >
        <input
          value={query}
          onChange={event => setQuery(event.target.value)}
          placeholder="e.g. Oversold stocks with rising volume above their 20-day average"
          disabled={disabled}
          aria-label="Describe the scan you want"
        />
        <button className="btn btn-primary" type="submit" disabled={disabled || loading || !query.trim()}>
          {loading ? 'Translating…' : 'Preview filters'}
        </button>
      </form>
      <div className="nl-scanner-builder-examples">
        {EXAMPLES.map(example => (
          <button
            className="nl-scanner-example"
            type="button"
            key={example}
            disabled={disabled || loading}
            onClick={() => {
              setQuery(example);
              void translate(example);
            }}
          >
            {example}
          </button>
        ))}
      </div>
      {error && <p className="nl-scanner-builder-error" role="alert">{error}</p>}
      {preview && (
        <div className="nl-scanner-preview">
          <div className="nl-scanner-preview-header">
            <strong>Filter preview</strong>
            <span className={`nl-scanner-parser ${preview.ambiguous ? 'needs-review' : ''}`}>
              {preview.ambiguous ? 'Review before use' : 'Ready to edit'} · {preview.parser_used}
            </span>
          </div>
          {preview.filters.length > 0 ? (
            <>
              <p className="nl-scanner-preview-description">{preview.filter_description}</p>
              <ul className="nl-scanner-filter-list">
                {preview.filters.map((filter, index) => <li key={`${filter.type}-${index}`}>{filterLabel(filter)}</li>)}
              </ul>
              <p className="nl-scanner-preview-match">Match: <strong>{preview.match === 'AND' ? 'all filters' : 'any filter'}</strong></p>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => onUsePreview(preview.filters, preview.match)}
              >
                Use editable filters
              </button>
            </>
          ) : (
            <p className="nl-scanner-preview-description">No executable filters were produced.</p>
          )}
          {preview.unresolved.length > 0 && (
            <ul className="nl-scanner-unresolved">
              {preview.unresolved.map(item => <li key={item}>{item}</li>)}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

export default NaturalLanguageScannerBuilder;
