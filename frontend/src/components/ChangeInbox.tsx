import React, { useCallback, useEffect, useState } from 'react';
import api, { ChangeInboxResponse, ChangeItem } from '../services/api';
import { formatETDateTime } from './chartMath';

const CHECKPOINT_KEY = 'marketlens.ai.changes.last-visit';

const categoryLabels: Record<ChangeItem['category'], string> = {
  watchlist: 'Watchlist',
  alert: 'Alerts',
  signal: 'Signals',
  catalyst: 'Catalysts',
  provider: 'Provider health',
};

function readCheckpoint(): string | undefined {
  try {
    return window.localStorage.getItem(CHECKPOINT_KEY) || undefined;
  } catch {
    return undefined;
  }
}

function saveCheckpoint(value: string): void {
  try {
    window.localStorage.setItem(CHECKPOINT_KEY, value);
  } catch {
    // A full/private localStorage should not prevent the inbox from rendering.
  }
}

export function ChangeInbox() {
  const [data, setData] = useState<ChangeInboxResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.getChangeInbox(readCheckpoint());
      setData(result);
      saveCheckpoint(result.as_of);
    } catch (err: any) {
      setError(err?.message || 'Failed to load changes');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <div className="card change-inbox-card" aria-label="What changed inbox">
      <div className="change-inbox-header">
        <div>
          <h2>📬 What changed</h2>
          <p className="info-text">New activity since your last AI Hub visit.</p>
        </div>
        <button type="button" className="btn btn-secondary btn-small" onClick={() => void load()} disabled={loading}>
          {loading ? 'Loading…' : '↻ Refresh'}
        </button>
      </div>

      {error && <div className="error-banner" role="alert">{error}<button type="button" onClick={() => void load()}>Retry</button></div>}
      {loading && !data && <p className="empty-state">Checking watchlists, alerts, signals, catalysts, and provider health…</p>}

      {data && (
        <>
          <div className="change-inbox-meta">
            <span className="info-text">Checked {formatETDateTime(data.as_of)}</span>
            {Object.entries(data.counts).map(([category, count]) => (
              <span className="signal-chip" key={category}>{categoryLabels[category as ChangeItem['category']] || category}: {count}</span>
            ))}
          </div>
          {data.items.length === 0 ? (
            <p className="empty-state">Nothing new since your last visit.</p>
          ) : (
            <div className="change-inbox-list" role="region" aria-label="What changed items" tabIndex={0}>
              {data.items.map(item => (
                <article className={`change-inbox-item change-${item.severity}`} key={item.id}>
                  <div className="change-inbox-item-main">
                    <span className="change-inbox-category">{categoryLabels[item.category]}</span>
                    <strong>{item.title}</strong>
                    {item.symbol && <span className="change-inbox-symbol">{item.symbol}</span>}
                    {item.detail && <p>{item.detail}</p>}
                  </div>
                  <div className="change-inbox-item-side">
                    <time dateTime={item.occurred_at}>{formatETDateTime(item.occurred_at)}</time>
                    <a className="btn btn-secondary btn-small" href={item.href}>Open</a>
                  </div>
                </article>
              ))}
            </div>
          )}
          {data.warnings.map(warning => <p className="info-text change-inbox-warning" key={warning}>ⓘ {warning}</p>)}
        </>
      )}
    </div>
  );
}

export default ChangeInbox;
