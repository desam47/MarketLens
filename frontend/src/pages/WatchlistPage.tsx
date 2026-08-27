import React, { useState, useEffect } from 'react';
import api, { Watchlist } from '../services/api';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { ErrorBanner } from '../components/ErrorBanner';

interface WatchlistPageProps {
  onSelectSymbol: (symbol: string) => void;
}

export function WatchlistPage({ onSelectSymbol }: WatchlistPageProps) {
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newSymbol, setNewSymbol] = useState('');
  const [newWatchlistName, setNewWatchlistName] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [symbols, setSymbols] = useState<import('../services/api').WatchlistSymbol[]>([]);
  const [symbolsLoading, setSymbolsLoading] = useState(false);
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

  const fetchSymbols = async (watchlistId: number) => {
    setSymbolsLoading(true);
    try {
      const data = await api.getWatchlistSymbols(watchlistId);
      setSymbols(data);
      setSymbolCounts(prev => ({ ...prev, [watchlistId]: data.length }));
    } catch (err: any) {
      setError(err.message || 'Failed to load symbols');
      setSymbols([]);
    } finally {
      setSymbolsLoading(false);
    }
  };

  useEffect(() => {
    if (selectedId != null) {
      fetchSymbols(selectedId);
    } else {
      setSymbols([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

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

  const handleRemoveSymbol = async (symbol: string) => {
    if (!selectedWatchlist) return;
    if (!window.confirm(`Remove ${symbol} from watchlist?`)) return;
    try {
      await api.removeSymbolFromWatchlist(selectedWatchlist.id, symbol);
      await fetchWatchlists();
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

  const handleMoveSymbol = async (symbol: string, direction: 'up' | 'down') => {
    if (!selectedWatchlist) return;
    const symbolList = symbols.map(s => s.symbol);
    const idx = symbolList.indexOf(symbol);
    if (idx === -1) return;

    const newIdx = direction === 'up' ? idx - 1 : idx + 1;
    if (newIdx < 0 || newIdx >= symbolList.length) return;

    [symbolList[idx], symbolList[newIdx]] = [symbolList[newIdx], symbolList[idx]];
    try {
      await api.reorderSymbols(selectedWatchlist.id, symbolList);
      await fetchSymbols(selectedWatchlist.id);
    } catch (err: any) {
      setError(err.message);
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
                <button
                  className="btn btn-danger"
                  onClick={() => handleDeleteWatchlist(selectedWatchlist.id)}
                >
                  Delete
                </button>
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

              <div className="symbol-list">
                {symbolsLoading ? (
                  <p className="empty-state">Loading symbols…</p>
                ) : symbols.length === 0 ? (
                  <p className="empty-state">No symbols yet. Add one above.</p>
                ) : (
                  symbols.map((s, i) => (
                    <div key={s.symbol} className="symbol-row">
                      <div className="symbol-row-actions">
                        <button
                          onClick={() => handleMoveSymbol(s.symbol, 'up')}
                          disabled={i === 0}
                        >↑</button>
                        <button
                          onClick={() => handleMoveSymbol(s.symbol, 'down')}
                          disabled={i === symbols.length - 1}
                        >↓</button>
                      </div>
                      <span
                        className="symbol-ticker"
                        onClick={() => onSelectSymbol(s.symbol)}
                      >
                        {s.symbol}
                      </span>
                      <span className="symbol-status">
                        {s.is_enabled ? '🟢' : '⚪'}
                      </span>
                      <button
                        className="btn-remove"
                        onClick={() => handleRemoveSymbol(s.symbol)}
                      >
                        ✕
                      </button>
                    </div>
                  ))
                )}
              </div>
            </>
          ) : (
            <p className="empty-state">Select or create a watchlist to view symbols</p>
          )}
        </div>
      </div>
    </div>
  );
}
