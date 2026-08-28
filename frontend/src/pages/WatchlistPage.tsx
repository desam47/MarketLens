import React, { useState, useEffect } from 'react';
import api, { Watchlist } from '../services/api';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { WatchlistTable } from '../components/WatchlistTable';

interface WatchlistPageProps {
  onSelectSymbol: (symbol: string) => void;
}

interface ImportResult {
  imported: string[];
  skipped: string[];
  errors: string[];
}

export function WatchlistPage({ onSelectSymbol }: WatchlistPageProps) {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [newSymbol, setNewSymbol] = useState('');
  const [newWatchlistName, setNewWatchlistName] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [showRename, setShowRename] = useState(false);
  const [renameValue, setRenameValue] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [importText, setImportText] = useState('');
  const [importing, setImporting] = useState(false);
  const [symbolCounts, setSymbolCounts] = useState<Record<number, number>>({});

  const fetchWatchlists = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getWatchlists();
      setWatchlists(data);
      if (data.length > 0 && !selectedId) {
        setSelectedId(data[0].id);
      }
      // Fetch counts for each watchlist (best-effort, non-blocking)
      const counts: Record<number, number> = {};
      await Promise.all(
        data.map(async (wl) => {
          try {
            const syms = await api.getWatchlistSymbols(wl.id);
            counts[wl.id] = syms.length;
          } catch {
            counts[wl.id] = 0;
          }
        })
      );
      setSymbolCounts(counts);
    } catch (err: any) {
      setError(err.message || 'Failed to load watchlists');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWatchlists();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedWatchlist = watchlists.find(w => w.id === selectedId);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWatchlistName.trim()) return;
    try {
      const created = await api.createWatchlist(newWatchlistName);
      setWatchlists([...watchlists, created]);
      setSelectedId(created.id);
      setNewWatchlistName('');
      setShowCreate(false);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleRename = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedWatchlist || !renameValue.trim()) return;
    try {
      const updated = await api.updateWatchlist(selectedWatchlist.id, { name: renameValue.trim() });
      setWatchlists(watchlists.map(w => w.id === updated.id ? updated : w));
      setShowRename(false);
      setRenameValue('');
      setInfo(`Renamed to "${updated.name}"`);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleAddSymbol = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newSymbol.trim() || !selectedWatchlist) return;
    const symbol = newSymbol.toUpperCase().trim();
    try {
      await api.addSymbolToWatchlist(selectedWatchlist.id, symbol);
      await fetchWatchlists();
      setNewSymbol('');
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleDeleteWatchlist = async (id: number) => {
    if (!window.confirm('Delete this watchlist?')) return;
    try {
      await api.deleteWatchlist(id);
      const remaining = watchlists.filter(w => w.id !== id);
      setWatchlists(remaining);
      setSelectedId(remaining[0]?.id || null);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleExport = async (format: 'json' | 'csv') => {
    if (!selectedWatchlist) return;
    try {
      const text = await api.exportWatchlist(selectedWatchlist.id, format);
      const blob = new Blob([text], {
        type: format === 'csv' ? 'text/csv' : 'application/json',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `watchlist_${selectedWatchlist.id}.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleImport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedWatchlist) return;
    // Parse: split by commas and newlines, uppercase, trim, dedupe, drop empties.
    const parsed = Array.from(
      new Set(
        importText
          .split(/[\n,]+/)
          .map(s => s.toUpperCase().trim())
          .filter(s => s.length > 0),
      ),
    );
    if (parsed.length === 0) {
      setError('No symbols to import');
      return;
    }
    setImporting(true);
    try {
      const result: ImportResult = await api.importWatchlist(selectedWatchlist.id, parsed);
      setInfo(
        `Imported ${result.imported.length}, skipped ${result.skipped.length}, errors ${result.errors.length}`,
      );
      if (result.errors.length > 0) {
        setError(`Failed: ${result.errors.slice(0, 3).join('; ')}${result.errors.length > 3 ? '…' : ''}`);
      }
      setShowImport(false);
      setImportText('');
      await fetchWatchlists();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setImporting(false);
    }
  };

  if (loading) return <LoadingSpinner message="Loading watchlists..." />;

  return (
    <div className="watchlist-page">
      <div className="watchlist-header">
        <h1>Watchlists</h1>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          + New Watchlist
        </button>
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {info && (
        <div className="info-banner" onClick={() => setInfo(null)} role="status">
          {info}
        </div>
      )}

      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Create Watchlist</h2>
            <form onSubmit={handleCreate}>
              <input
                type="text"
                placeholder="Watchlist name"
                value={newWatchlistName}
                onChange={e => setNewWatchlistName(e.target.value)}
                autoFocus
              />
              <div className="modal-actions">
                <button type="button" onClick={() => setShowCreate(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showRename && selectedWatchlist && (
        <div className="modal-overlay" onClick={() => setShowRename(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Rename Watchlist</h2>
            <form onSubmit={handleRename}>
              <input
                type="text"
                placeholder="New name"
                value={renameValue}
                onChange={e => setRenameValue(e.target.value)}
                autoFocus
              />
              <div className="modal-actions">
                <button type="button" onClick={() => setShowRename(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Save</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showImport && selectedWatchlist && (
        <div className="modal-overlay" onClick={() => setShowImport(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Import Symbols</h2>
            <form onSubmit={handleImport}>
              <textarea
                placeholder="AAPL, NVDA, MSFT (or one per line)"
                value={importText}
                onChange={e => setImportText(e.target.value)}
                rows={8}
                autoFocus
              />
              <div className="modal-actions">
                <button type="button" onClick={() => setShowImport(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary" disabled={importing}>
                  {importing ? 'Importing…' : 'Import'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="watchlist-layout">
        <aside className="watchlist-sidebar">
          {watchlists.length === 0 ? (
            <p className="empty-state">No watchlists yet. Create one to get started.</p>
          ) : (
            <ul>
              {watchlists.map(w => (
                <li
                  key={w.id}
                  className={selectedId === w.id ? 'active' : ''}
                  onClick={() => setSelectedId(w.id)}
                >
                  {w.name}
                  <span className="symbol-count">{symbolCounts[w.id] ?? '…'}</span>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <div className="watchlist-content">
          {selectedWatchlist ? (
            <>
              <div className="watchlist-title-bar">
                <h2>{selectedWatchlist.name}</h2>
                <div className="watchlist-actions">
                  <button
                    className="btn"
                    onClick={() => {
                      setRenameValue(selectedWatchlist.name);
                      setShowRename(true);
                    }}
                  >
                    Rename
                  </button>
                  <button
                    className="btn"
                    onClick={() => setShowImport(true)}
                  >
                    Import
                  </button>
                  <button
                    className="btn"
                    onClick={() => handleExport('json')}
                    title="Download as JSON"
                  >
                    Export JSON
                  </button>
                  <button
                    className="btn"
                    onClick={() => handleExport('csv')}
                    title="Download as CSV"
                  >
                    Export CSV
                  </button>
                  <button
                    className="btn btn-danger"
                    onClick={() => handleDeleteWatchlist(selectedWatchlist.id)}
                  >
                    Delete
                  </button>
                </div>
              </div>

              <form onSubmit={handleAddSymbol} className="add-symbol-form">
                <input
                  type="text"
                  placeholder="Add symbol (e.g., NVDA)"
                  value={newSymbol}
                  onChange={e => setNewSymbol(e.target.value.toUpperCase())}
                  maxLength={5}
                />
                <button type="submit" className="btn btn-primary">Add</button>
              </form>

              <WatchlistTable
                watchlistId={selectedId!}
                onSelectSymbol={onSelectSymbol}
              />
            </>
          ) : (
            <p className="empty-state">Select or create a watchlist to view symbols</p>
          )}
        </div>
      </div>
    </div>
  );
}
