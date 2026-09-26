import React, { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import api, { HistoricalSignal, ScanResult, TapeSnapshot } from '../services/api';
import type { NavigationState } from '../utils/appNavigation';
import { SymbolAutocompleteInput, resolveWatchlistSymbol } from '../components/SymbolAutocompleteInput';

const STORAGE_KEY = 'marketlens.trade.journal';
const PENDING_DRAFT_KEY = 'marketlens.trade.journal.pending';
const MAX_SCREENSHOT_BYTES = 1_500_000;

type TradeSide = 'long' | 'short';
type TradeStatus = 'planned' | 'open' | 'closed';

interface JournalSignalContext {
  timeframe: string;
  timestamp: string;
  trendState: string | null;
  trendScore: number | null;
  strength: number | null;
  marketRegime: string | null;
  return5b: number | null;
}

interface JournalMarketContext {
  provider: string | null;
  dataStatus: string | null;
  spreadBps: number | null;
  tapePressure: string | null;
  pressureTrend: string | null;
  tapeAcceleration: number | null;
  volumeAcceleration: number | null;
}

export interface JournalEntry {
  id: string;
  symbol: string;
  side: TradeSide;
  status: TradeStatus;
  entryDate: string;
  exitDate: string | null;
  quantity: number;
  entryPrice: number;
  exitPrice: number | null;
  stopPrice: number | null;
  targetPrice: number | null;
  thesis: string;
  screenshotDataUrl: string | null;
  reviewNotes: string;
  signalContext: JournalSignalContext | null;
  marketContext: JournalMarketContext | null;
  createdAt: string;
  updatedAt: string;
}

function readEntries(): JournalEntry[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((entry): entry is JournalEntry => (
      entry && typeof entry.id === 'string' && typeof entry.symbol === 'string'
      && (entry.side === 'long' || entry.side === 'short')
      && (entry.status === 'planned' || entry.status === 'open' || entry.status === 'closed')
      && typeof entry.entryDate === 'string'
      && Number.isFinite(entry.quantity) && entry.quantity > 0
      && Number.isFinite(entry.entryPrice) && entry.entryPrice > 0
      && (entry.exitPrice == null || Number.isFinite(entry.exitPrice))
    ));
  } catch {
    return [];
  }
}

function fmtMoney(value: number | null, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function fmtPct(value: number | null): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function signalContext(signal: HistoricalSignal | null): JournalSignalContext | null {
  if (!signal) return null;
  return {
    timeframe: signal.timeframe,
    timestamp: signal.timestamp,
    trendState: signal.trend_state,
    trendScore: signal.trend_score,
    strength: signal.strength,
    marketRegime: signal.market_regime,
    return5b: signal.return_5b,
  };
}

function latestSignal(signals: Record<string, HistoricalSignal>): HistoricalSignal | null {
  return Object.values(signals)
    .filter(signal => signal && typeof signal.timestamp === 'string')
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))[0] || null;
}

function marketContext(scan: ScanResult | null, tape: TapeSnapshot | null): JournalMarketContext | null {
  if (!scan && !tape) return null;
  const value = scan?.indicator_values?.spread_bps;
  return {
    provider: scan?.quote?.provider || null,
    dataStatus: scan?.quote?.data_status || null,
    spreadBps: typeof value === 'number' ? value : null,
    tapePressure: tape?.pressure || null,
    pressureTrend: tape?.pressure_trend || null,
    tapeAcceleration: tape?.tape_accel ?? null,
    volumeAcceleration: tape?.volume_accel ?? null,
  };
}

function entryPnl(entry: JournalEntry): number | null {
  if (entry.exitPrice == null) return null;
  const direction = entry.side === 'short' ? -1 : 1;
  return (entry.exitPrice - entry.entryPrice) * entry.quantity * direction;
}

function entryRisk(entry: JournalEntry): number | null {
  if (entry.stopPrice == null) return null;
  return Math.abs(entry.entryPrice - entry.stopPrice) * entry.quantity;
}

function formatDate(value: string | null): string {
  if (!value) return '—';
  const parsed = new Date(`${value}${value.length === 10 ? 'T00:00:00' : ''}`);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function todayInputValue(): string {
  const today = new Date();
  const month = String(today.getMonth() + 1).padStart(2, '0');
  const day = String(today.getDate()).padStart(2, '0');
  return `${today.getFullYear()}-${month}-${day}`;
}

function journalNavigation(navigation?: NavigationState): { status?: TradeStatus; search?: string } {
  const value = navigation?.filters?.journal;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  const filters = value as Record<string, unknown>;
  return {
    status: filters.status === 'planned' || filters.status === 'open' || filters.status === 'closed' ? filters.status : undefined,
    search: typeof filters.search === 'string' ? filters.search : undefined,
  };
}

export function TradeJournalPage({ navigation }: { navigation?: NavigationState }) {
  const initialNavigation = journalNavigation(navigation);
  const [entries, setEntries] = useState<JournalEntry[]>(readEntries);
  // Track which entry ids have screenshots in localStorage so we can re-attach
  // them after a remote fetch (screenshots never leave the client).
  const screenshotMapRef = useRef<Map<string, string>>(
    new Map(readEntries().filter(e => e.screenshotDataUrl).map(e => [e.id, e.screenshotDataUrl!]))
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [symbol, setSymbol] = useState(() => {
    if (navigation?.symbol) return navigation.symbol;
    try { return (JSON.parse(window.localStorage.getItem(PENDING_DRAFT_KEY) || '{}')?.symbol || ''); } catch { return ''; }
  });
  const [side, setSide] = useState<TradeSide>('long');
  const [status, setStatus] = useState<TradeStatus>('planned');
  const [entryDate, setEntryDate] = useState(todayInputValue);
  const [exitDate, setExitDate] = useState('');
  const [quantity, setQuantity] = useState('');
  const [entryPrice, setEntryPrice] = useState('');
  const [exitPrice, setExitPrice] = useState('');
  const [stopPrice, setStopPrice] = useState('');
  const [targetPrice, setTargetPrice] = useState('');
  const [thesis, setThesis] = useState(() => {
    try { return (JSON.parse(window.localStorage.getItem(PENDING_DRAFT_KEY) || '{}')?.thesis || ''); } catch { return ''; }
  });
  const [reviewNotes, setReviewNotes] = useState('');
  const [screenshotDataUrl, setScreenshotDataUrl] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | TradeStatus>(initialNavigation.status || 'all');
  const [search, setSearch] = useState(initialNavigation.search || navigation?.symbol || '');
  const [focusedEntryIds, setFocusedEntryIds] = useState<string[]>(navigation?.selectedRecords || []);
  const [formError, setFormError] = useState<string | null>(null);
  const [screenshotError, setScreenshotError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const next = journalNavigation(navigation);
    if (navigation?.symbol) setSearch(navigation.symbol);
    if (next.search != null) setSearch(next.search);
    if (next.status) setFilter(next.status);
    setFocusedEntryIds(navigation?.selectedRecords || []);
  }, [navigation]);

  useEffect(() => {
    try { window.localStorage.removeItem(PENDING_DRAFT_KEY); } catch { /* best effort */ }
  }, []);

  // On mount: fetch remote entries and merge, re-attaching any local screenshots.
  useEffect(() => {
    api.listJournalEntries().then(remote => {
      setEntries(prev => {
        const remoteIds = new Set(remote.map((e: any) => e.id));
        const localOnly = prev.filter(e => !remoteIds.has(e.id));
        const merged: JournalEntry[] = [
          ...remote.map((e: any) => ({
            ...e,
            screenshotDataUrl: screenshotMapRef.current.get(e.id) ?? null,
          })),
          ...localOnly,
        ];
        return merged;
      });
    }).catch(() => { /* backend unavailable — localStorage is source of truth */ });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
    } catch {
      setFormError('Journal saved in memory, but local storage is full. Remove a screenshot or older entry and try again.');
    }
  }, [entries]);

  const resetForm = () => {
    setEditingId(null); setSymbol(''); setSide('long'); setStatus('planned');
    setEntryDate(todayInputValue()); setExitDate('');
    setQuantity(''); setEntryPrice(''); setExitPrice(''); setStopPrice(''); setTargetPrice('');
    setThesis(''); setReviewNotes(''); setScreenshotDataUrl(null); setFormError(null); setScreenshotError(null);
  };

  const handleScreenshot = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) { setScreenshotError('Choose an image file.'); return; }
    if (file.size > MAX_SCREENSHOT_BYTES) { setScreenshotError('Screenshot must be 1.5 MB or smaller.'); return; }
    const reader = new FileReader();
    reader.onload = () => { setScreenshotDataUrl(typeof reader.result === 'string' ? reader.result : null); setScreenshotError(null); };
    reader.onerror = () => setScreenshotError('Could not read that screenshot.');
    reader.readAsDataURL(file);
  };

  const saveEntry = async (event: FormEvent) => {
    event.preventDefault();
    const normalizedSymbol = symbol.trim().toUpperCase();
    const parsedQuantity = Number(quantity);
    const parsedEntry = Number(entryPrice);
    const parsedExit = exitPrice.trim() ? Number(exitPrice) : null;
    const parsedStop = stopPrice.trim() ? Number(stopPrice) : null;
    const parsedTarget = targetPrice.trim() ? Number(targetPrice) : null;
    if (!normalizedSymbol || !entryDate || !Number.isFinite(parsedQuantity) || parsedQuantity <= 0 || !Number.isFinite(parsedEntry) || parsedEntry <= 0) {
      setFormError('Enter a symbol, entry date, positive quantity, and valid entry price.'); return;
    }
    if (status === 'closed' && (parsedExit == null || !exitDate)) {
      setFormError('Closed trades need an exit date and exit price.'); return;
    }
    const optionalPrices = [parsedExit, parsedStop, parsedTarget];
    if (optionalPrices.some(value => value != null && (!Number.isFinite(value) || value <= 0))) {
      setFormError('Exit, stop, and target prices must be blank or positive numbers.'); return;
    }
    if (!await resolveWatchlistSymbol(normalizedSymbol)) {
      setFormError('Choose a symbol from a Watchlist.'); return;
    }
    setSaving(true); setFormError(null);
    let attachedSignal: JournalSignalContext | null = null;
    let attachedMarket: JournalMarketContext | null = null;
    try {
      const [latestResult, scanResult, tapeResult] = await Promise.allSettled([
        api.getLatestSignalsForSymbol(normalizedSymbol),
        api.getScanResult(normalizedSymbol),
        api.getTape(normalizedSymbol),
      ]);
      if (latestResult.status === 'fulfilled') attachedSignal = signalContext(latestSignal(latestResult.value));
      const scan = scanResult.status === 'fulfilled' ? scanResult.value : null;
      const tape = tapeResult.status === 'fulfilled' ? tapeResult.value.snapshot : null;
      attachedMarket = marketContext(scan, tape);
    } catch {
      // A journal entry remains useful when the provider is unavailable.
    }
    const now = new Date().toISOString();
    const existing = editingId ? entries.find(entry => entry.id === editingId) : null;
    const next: JournalEntry = {
      id: existing?.id || `trade-${Date.now()}`,
      symbol: normalizedSymbol, side, status, entryDate, exitDate: exitDate || null,
      quantity: parsedQuantity, entryPrice: parsedEntry, exitPrice: parsedExit,
      stopPrice: parsedStop, targetPrice: parsedTarget, thesis: thesis.trim(),
      screenshotDataUrl, reviewNotes: reviewNotes.trim(),
      signalContext: attachedSignal || existing?.signalContext || null,
      marketContext: attachedMarket || existing?.marketContext || null,
      createdAt: existing?.createdAt || now, updatedAt: now,
    };
    setEntries(current => existing ? current.map(entry => entry.id === existing.id ? next : entry) : [next, ...current]);
    if (next.screenshotDataUrl) screenshotMapRef.current.set(next.id, next.screenshotDataUrl);
    // Sync to backend (no screenshot — too large for SQLite).
    api.upsertJournalEntry({
      client_id: next.id,
      symbol: next.symbol,
      side: next.side,
      status: next.status,
      entry_date: next.entryDate,
      exit_date: next.exitDate,
      quantity: next.quantity,
      entry_price: next.entryPrice,
      exit_price: next.exitPrice,
      stop_price: next.stopPrice,
      target_price: next.targetPrice,
      thesis: next.thesis,
      review_notes: next.reviewNotes,
      signal_context: next.signalContext,
      market_context: next.marketContext,
    }).catch(() => { /* best-effort */ });
    resetForm(); setSaving(false);
  };

  const editEntry = (entry: JournalEntry) => {
    setEditingId(entry.id); setSymbol(entry.symbol); setSide(entry.side); setStatus(entry.status);
    setEntryDate(entry.entryDate); setExitDate(entry.exitDate || ''); setQuantity(String(entry.quantity));
    setEntryPrice(String(entry.entryPrice)); setExitPrice(entry.exitPrice == null ? '' : String(entry.exitPrice));
    setStopPrice(entry.stopPrice == null ? '' : String(entry.stopPrice)); setTargetPrice(entry.targetPrice == null ? '' : String(entry.targetPrice));
    setThesis(entry.thesis); setReviewNotes(entry.reviewNotes); setScreenshotDataUrl(entry.screenshotDataUrl); setFormError(null);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const removeEntry = (id: string) => {
    setEntries(current => current.filter(entry => entry.id !== id));
    screenshotMapRef.current.delete(id);
    api.deleteJournalEntry(id).catch(() => { /* best-effort */ });
  };

  const visibleEntries = useMemo(() => entries.filter(entry => (
    (filter === 'all' || entry.status === filter)
    && (!search.trim() || entry.symbol.includes(search.trim().toUpperCase()) || entry.thesis.toLowerCase().includes(search.trim().toLowerCase()))
  )), [entries, filter, search]);

  const stats = useMemo(() => {
    const closed = entries.filter(entry => entry.status === 'closed' && entryPnl(entry) != null);
    const pnls = closed.map(entry => entryPnl(entry) as number);
    const netPnl = pnls.reduce((sum, value) => sum + value, 0);
    const wins = pnls.filter(value => value > 0).length;
    return { total: entries.length, open: entries.filter(entry => entry.status === 'open').length, planned: entries.filter(entry => entry.status === 'planned').length, closed: closed.length, netPnl, winRate: closed.length ? (wins / closed.length) * 100 : null };
  }, [entries]);

  return (
    <div className="page trade-journal-page">
      <div className="dashboard-header">
        <div><h1>Trade Journal</h1><p className="subtitle">Capture the plan, execution, and review behind every trade.</p></div>
        <div className="header-actions"><span className="journal-provider-note">Manual tracker · no broker connection</span></div>
      </div>
      <div className="journal-notice">Entries stay in this browser. The latest recorded signal for the symbol is attached automatically when available.</div>
      {formError && <div className="error-banner" role="alert">{formError}<button onClick={() => setFormError(null)} aria-label="Dismiss error">×</button></div>}

      <form className="card journal-form" onSubmit={saveEntry}>
        <div className="journal-form-heading"><div><h2>{editingId ? 'Edit trade' : 'Record a trade'}</h2><span className="info-text">Use planned and open entries to document your thesis before the outcome is known.</span></div>{editingId && <button className="btn" type="button" onClick={resetForm}>Cancel edit</button>}</div>
        <div className="journal-form-grid">
          <label>Symbol<SymbolAutocompleteInput aria-label="Trade symbol" value={symbol} onChange={setSymbol} placeholder="AAPL" maxLength={10} /></label>
          <label>Side<select aria-label="Trade side" value={side} onChange={event => setSide(event.target.value as TradeSide)}><option value="long">Long</option><option value="short">Short</option></select></label>
          <label>Status<select aria-label="Trade status" value={status} onChange={event => setStatus(event.target.value as TradeStatus)}><option value="planned">Planned</option><option value="open">Open</option><option value="closed">Closed</option></select></label>
          <label>Entry date<input aria-label="Entry date" type="date" value={entryDate} onChange={event => setEntryDate(event.target.value)} /></label>
          <label>Exit date <span className="label-muted">(optional)</span><input aria-label="Exit date" type="date" value={exitDate} onChange={event => setExitDate(event.target.value)} /></label>
          <label>Quantity<input aria-label="Trade quantity" type="number" min="0" step="any" value={quantity} onChange={event => setQuantity(event.target.value)} placeholder="100" /></label>
          <label>Entry price<input aria-label="Entry price" type="number" min="0" step="any" value={entryPrice} onChange={event => setEntryPrice(event.target.value)} placeholder="185.00" /></label>
          <label>Exit price <span className="label-muted">(optional)</span><input aria-label="Exit price" type="number" min="0" step="any" value={exitPrice} onChange={event => setExitPrice(event.target.value)} placeholder="195.00" /></label>
          <label>Stop <span className="label-muted">(optional)</span><input aria-label="Stop price" type="number" min="0" step="any" value={stopPrice} onChange={event => setStopPrice(event.target.value)} placeholder="178.00" /></label>
          <label>Target <span className="label-muted">(optional)</span><input aria-label="Target price" type="number" min="0" step="any" value={targetPrice} onChange={event => setTargetPrice(event.target.value)} placeholder="200.00" /></label>
        </div>
        <div className="journal-text-grid">
          <label>Thesis<textarea aria-label="Trade thesis" value={thesis} onChange={event => setThesis(event.target.value)} placeholder="Why did this trade make sense? What invalidates it?" rows={4} /></label>
          <label>Review notes<textarea aria-label="Review notes" value={reviewNotes} onChange={event => setReviewNotes(event.target.value)} placeholder="What worked, what failed, and what will you change?" rows={4} /></label>
        </div>
        <div className="journal-form-footer">
          <label className="journal-file-label">Screenshot <span className="label-muted">(optional, max 1.5 MB)</span><input aria-label="Trade screenshot" type="file" accept="image/*" onChange={handleScreenshot} /></label>
          {screenshotDataUrl && <button className="btn btn-secondary btn-small" type="button" onClick={() => setScreenshotDataUrl(null)}>Remove screenshot</button>}
          <button className="btn btn-primary" type="submit" disabled={saving}>{saving ? 'Saving…' : editingId ? 'Update trade' : 'Save trade'}</button>
        </div>
        {screenshotError && <p className="error-text" role="alert">{screenshotError}</p>}
      </form>

      <div className="journal-summary-grid">
        <div className="card journal-summary-card"><span>Total entries</span><strong>{stats.total}</strong><small>{stats.planned} planned · {stats.open} open</small></div>
        <div className="card journal-summary-card"><span>Closed trades</span><strong>{stats.closed}</strong><small>{stats.winRate == null ? 'No completed outcomes yet' : `${stats.winRate.toFixed(1)}% win rate`}</small></div>
        <div className="card journal-summary-card"><span>Net realized P&amp;L</span><strong className={stats.netPnl >= 0 ? 'journal-positive' : 'journal-negative'}>{fmtMoney(stats.netPnl)}</strong><small>Closed trades only</small></div>
      </div>

      <div className="journal-list-header"><div><h2>Journal entries</h2><span className="info-text">{visibleEntries.length} shown · {entries.length} total</span></div><div className="journal-filters"><input aria-label="Filter journal" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search symbol or thesis" /><select aria-label="Filter by status" value={filter} onChange={event => setFilter(event.target.value as 'all' | TradeStatus)}><option value="all">All statuses</option><option value="planned">Planned</option><option value="open">Open</option><option value="closed">Closed</option></select></div></div>
      {visibleEntries.length === 0 ? <div className="card journal-empty-state"><div>📝</div><h2>{entries.length ? 'No matching entries' : 'Your journal is empty'}</h2><p>{entries.length ? 'Adjust the filter to see another trade.' : 'Record your first plan above. Keeping the thesis beside the result makes review much easier.'}</p></div> : (
        <div className="journal-entry-list">
          {visibleEntries.map(entry => {
            const pnl = entryPnl(entry);
            const risk = entryRisk(entry);
            const rMultiple = pnl != null && risk ? pnl / risk : null;
            const focused = focusedEntryIds.includes(entry.id);
            return <article className={`card journal-entry-card${focused ? ' journal-entry-focused' : ''}`} key={entry.id} aria-label={focused ? `${entry.symbol} focused journal entry` : undefined}>
              <div className="journal-entry-header"><div><strong className="journal-symbol">{entry.symbol}</strong><span className={`journal-badge journal-${entry.side}`}>{entry.side}</span><span className={`journal-badge journal-status-${entry.status}`}>{entry.status}</span></div><div className="journal-entry-actions"><button className="btn btn-secondary btn-small" onClick={() => editEntry(entry)}>Edit</button><button className="btn btn-danger btn-small" onClick={() => removeEntry(entry.id)}>Delete</button></div></div>
              <div className="journal-entry-metrics"><span><small>Entry</small>{formatDate(entry.entryDate)} · {fmtMoney(entry.entryPrice)}</span><span><small>Exit</small>{entry.exitPrice == null ? '—' : `${formatDate(entry.exitDate)} · ${fmtMoney(entry.exitPrice)}`}</span><span><small>Size</small>{entry.quantity.toLocaleString()}</span><span><small>P&amp;L</small><strong className={pnl == null ? '' : pnl >= 0 ? 'journal-positive' : 'journal-negative'}>{fmtMoney(pnl)}</strong>{rMultiple != null && <em>{rMultiple >= 0 ? '+' : ''}{rMultiple.toFixed(2)}R</em>}</span></div>
              <div className="journal-entry-copy"><div><h3>Thesis</h3><p>{entry.thesis || 'No thesis recorded.'}</p></div><div><h3>Review</h3><p>{entry.reviewNotes || 'No review notes yet.'}</p></div></div>
              <div className="journal-entry-footer">{entry.signalContext ? <span className="journal-signal"><strong>Signal attached:</strong> {entry.signalContext.timeframe} · {entry.signalContext.trendState || 'unknown'} · {entry.signalContext.marketRegime || 'regime unavailable'} <small>{formatDate(entry.signalContext.timestamp.slice(0, 10))}{entry.signalContext.return5b == null ? '' : ` · 5-bar ${fmtPct(entry.signalContext.return5b)}`}</small></span> : <span className="info-text">No recorded signal was available when this entry was saved.</span>}{entry.marketContext && <span className="journal-signal"><strong>Market context:</strong> {entry.marketContext.tapePressure ? `tape ${entry.marketContext.tapePressure}` : 'tape unavailable'}{entry.marketContext.pressureTrend && ` · ${entry.marketContext.pressureTrend}`}{entry.marketContext.spreadBps != null && ` · spread ${entry.marketContext.spreadBps.toFixed(1)} bps`}{entry.marketContext.provider && ` · ${entry.marketContext.provider}`}</span>}{entry.screenshotDataUrl && <img className="journal-screenshot" src={entry.screenshotDataUrl} alt={`${entry.symbol} trade screenshot`} />}</div>
            </article>;
          })}
        </div>
      )}
    </div>
  );
}

export default TradeJournalPage;
