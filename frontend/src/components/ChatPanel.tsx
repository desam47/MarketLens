/**
 * ChatPanel — the universal AI Hub chat (2026-09-10).
 *
 * Not tied to a ticker: ask about any stock (in your watchlist or not),
 * several at once, or the market as a whole with no ticker named. The
 * backend resolves the relevant tickers from each message, always
 * attaches a market-wide baseline, and answers from live quant data
 * where it has it. Single open universal thread (optionally scoped to a
 * specific alert trigger via `alertTriggerId` if opened from an alert
 * row).
 *
 * Sending a message is optimistic-then-reconcile (mirrors AlertsCard):
 * append the user's bubble immediately, then append the real assistant
 * reply (or roll back + show an error on failure).
 *
 * "Clear" (top-right) permanently deletes the chat history — every
 * universal session and its messages — then opens a fresh session.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import api, { ChatMessage } from '../services/api';
import { highlightMessage } from '../utils/textHighlight';

interface ChatPanelProps {
  alertTriggerId?: number | null;
  /**
   * Called with the primary ticker a chat turn resolved to (the first
   * `focus`, else the first `partial`). The AI Hub uses this to point
   * its Analysis / Templates sections at whatever the trader just asked
   * about. Not called for market-wide turns or when only `unavailable`
   * tickers were named. Fires at most once per turn.
   */
  onSymbolResolved?: (symbol: string) => void;
}

// A message in local state may be a not-yet-finalized streaming bubble.
type LocalMessage = ChatMessage & { streaming?: boolean };

// What the quick-action buttons need to know to be smart instead of
// dumb: every watchlist that exists, and which one (if any) already
// holds a given ticker. Loaded once per ChatPanel mount and kept
// current locally as the buttons themselves add things — it does NOT
// pick up a watchlist change made through the chat's own AI tools in
// the same session (no push signal for that), only what happened
// through these buttons.
interface WatchlistOption { id: number; name: string; }
interface WatchlistIndex {
  lists: WatchlistOption[];
  memberOf: Record<string, number>;  // ticker -> the watchlist id already holding it
}

const EXAMPLES = [
  "How's NVDA looking?",
  "What's the market doing today?",
  'Which of my names look weak?',
  'Compare AAPL and MSFT',
];

export function ChatPanel({ alertTriggerId = null, onSymbolResolved }: ChatPanelProps) {
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [slow, setSlow] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [watchlistIndex, setWatchlistIndex] = useState<WatchlistIndex | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Loaded once on mount so the very first quick-action button already
  // knows whether a ticker is watchlisted and how many lists exist —
  // fetched via the same endpoints the Watchlist page uses, not a new
  // backend route.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const lists = await api.getWatchlists();
        const memberOf: Record<string, number> = {};
        await Promise.all(lists.map(async wl => {
          try {
            const syms = await api.getWatchlistSymbols(wl.id);
            for (const s of syms) {
              if (!(s.symbol in memberOf)) memberOf[s.symbol] = wl.id;
            }
          } catch { /* best-effort — this list's membership just stays unknown */ }
        }));
        if (!cancelled) {
          setWatchlistIndex({ lists: lists.map(wl => ({ id: wl.id, name: wl.name })), memberOf });
        }
      } catch { /* best-effort — quick actions fall back to dumb/one-click behavior */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const markSymbolWatchlisted = useCallback((symbol: string, watchlistId: number) => {
    setWatchlistIndex(prev =>
      prev ? { ...prev, memberOf: { ...prev.memberOf, [symbol]: watchlistId } } : prev);
  }, []);

  const addWatchlistToIndex = useCallback((wl: WatchlistOption) => {
    setWatchlistIndex(prev =>
      prev ? { ...prev, lists: [...prev.lists, wl] } : { lists: [wl], memberOf: {} });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setMessages([]);
    setSessionId(null);

    (async () => {
      try {
        const session = await api.createChatSession(undefined, alertTriggerId);
        if (cancelled) return;
        setSessionId(session.id);
        const history = await api.getChatMessages(session.id);
        if (cancelled) return;
        setMessages(history);
      } catch (e: any) {
        if (!cancelled) setError(e?.message || 'Failed to open chat');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [alertTriggerId]);

  const handleClear = useCallback(async () => {
    setClearing(true);
    setError(null);
    try {
      // Actually flush the history — sessions + messages — not just
      // hide it behind a new session.
      await api.clearChatHistory(alertTriggerId ?? undefined);
      const session = await api.createChatSession(undefined, alertTriggerId, true);
      setSessionId(session.id);
      setMessages([]);
      setInput('');
    } catch (e: any) {
      setError(e?.message || 'Failed to clear chat');
    } finally {
      setClearing(false);
    }
  }, [alertTriggerId]);

  useEffect(() => {
    // scrollTo is missing in jsdom — guard so tests don't throw.
    listRef.current?.scrollTo?.({ top: listRef.current.scrollHeight });
  }, [messages, sending]);

  // "Still working…" only if the turn is taking a while.
  useEffect(() => {
    if (!sending) { setSlow(false); return; }
    const t = setTimeout(() => setSlow(true), 4000);
    return () => clearTimeout(t);
  }, [sending]);

  // Proactive nudges: backend.ai.nudges.NudgeService can drop an
  // unprompted assistant message into the universal session at any
  // time (an alert firing, a watched symbol crossing a strong
  // scanner score). Nothing here subscribes to a push channel, so
  // polling is the only way this panel finds out — merge in any
  // message id not already in state. Skipped for an alert-scoped
  // session (nudges only ever target the universal thread) and
  // while a turn is in flight (its own reconciliation already
  // covers the latest state, and mid-stream is not a safe time to
  // splice in extra messages).
  useEffect(() => {
    if (!sessionId || alertTriggerId) return;
    const interval = setInterval(async () => {
      if (sending) return;
      try {
        const fresh = await api.getChatMessages(sessionId);
        setMessages(prev => {
          const known = new Set(prev.map(m => m.id));
          const additions = fresh.filter(m => !known.has(m.id));
          return additions.length ? [...prev, ...additions] : prev;
        });
      } catch {
        // best-effort — a missed poll just tries again next tick
      }
    }, 20000);
    return () => clearInterval(interval);
  }, [sessionId, alertTriggerId, sending]);

  const submit = useCallback(async (content: string) => {
    if (!content || !sessionId || sending) return;
    setSending(true);
    setError(null);
    const now = Date.now();
    const optimisticUser: LocalMessage = {
      id: -now,
      session_id: sessionId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
      grounded: null,
    };
    const placeholderId = -now - 1;
    const placeholder: LocalMessage = {
      id: placeholderId,
      session_id: sessionId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
      grounded: null,
      streaming: true,
    };
    setMessages(prev => [...prev, optimisticUser, placeholder]);
    setInput('');

    const patchPlaceholder = (patch: Partial<LocalMessage>) =>
      setMessages(prev => prev.map(m => (m.id === placeholderId ? { ...m, ...patch } : m)));

    // Point the Hub's Analysis/Templates at the ticker this turn is
    // about — first `focus`, else first `partial`. Once per turn.
    let adopted = false;
    const adoptSymbol = (focus?: string[], partial?: string[]) => {
      if (adopted || !onSymbolResolved) return;
      const sym = (focus && focus[0]) || (partial && partial[0]);
      if (sym && !sym.startsWith('^')) {
        adopted = true;
        onSymbolResolved(sym);
      }
    };

    let sawDelta = false;
    try {
      const finalMsg = await api.streamChatMessage(sessionId, content, {
        onMeta: m => {
          patchPlaceholder({ focus: m.focus, partial: m.partial, unavailable: m.unavailable });
          adoptSymbol(m.focus, m.partial);
        },
        onDelta: t => {
          sawDelta = true;
          setMessages(prev =>
            prev.map(m => (m.id === placeholderId ? { ...m, content: m.content + t } : m)),
          );
        },
      });
      setMessages(prev => prev.map(m => (m.id === placeholderId ? finalMsg : m)));
      adoptSymbol(finalMsg.focus, finalMsg.partial);
    } catch (e: any) {
      if (e?.beforeFirstDelta && !sawDelta) {
        // Stream never started — fall back to the plain blocking endpoint.
        try {
          const finalMsg = await api.sendChatMessage(sessionId, content);
          setMessages(prev => prev.map(m => (m.id === placeholderId ? finalMsg : m)));
          adoptSymbol(finalMsg.focus, finalMsg.partial);
          return;
        } catch (e2: any) {
          setError(e2?.message || 'Failed to send message');
        }
      } else {
        setError(e?.message || 'Failed to send message');
      }
      setMessages(prev => prev.filter(m => m.id !== optimisticUser.id && m.id !== placeholderId));
      setInput(content);
    } finally {
      setSending(false);
    }
  }, [sessionId, sending, onSymbolResolved]);

  const handleSend = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    submit(input.trim());
  }, [input, submit]);

  const fillExample = (q: string) => {
    setInput(q);
    inputRef.current?.focus();
  };

  return (
    <div className="card chat-panel-card">
      <div className="chat-panel-header">
        <div className="chat-panel-header-top">
          <h2>💬 Chat</h2>
          <button
            type="button"
            className={`btn btn-secondary ${clearing ? 'btn-loading' : ''}`}
            onClick={handleClear}
            disabled={loading || clearing || messages.length === 0}
            title="Permanently delete this chat's history and start fresh"
          >
            {clearing ? '⟳' : '🗑 Clear'}
          </button>
        </div>
        <p className="info-text">
          Uses live quant data where available — full coverage for your watchlist,
          price&nbsp;+&nbsp;indicators only for other tickers. Research to inform your
          own decision, not financial advice.
        </p>
      </div>

      {error && <div className="chat-panel-error">⚠️ {error}</div>}

      <div className="chat-message-list" ref={listRef}>
        {loading && <p className="info-text">Opening chat…</p>}
        {!loading && messages.length === 0 && (
          <div className="chat-empty-state">
            <p>Ask about any stock — in your watchlist or not — or the market as a whole.</p>
            <div className="chat-example-chips">
              {EXAMPLES.map(q => (
                <button key={q} type="button" className="chat-example-chip" onClick={() => fillExample(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map(m => {
          const pending = m.streaming && !m.content;
          return (
            <div key={m.id} className={`chat-bubble-row ${m.role}`}>
              <div className={`chat-bubble ${m.role}${pending ? ' chat-bubble-pending' : ''}`}>
                {pending
                  ? (slow ? 'Still working — pulling data for the tickers you mentioned…' : '…')
                  : m.role === 'assistant'
                    ? highlightMessage(m.content, m.focus ?? [], m.partial ?? [], m.unavailable ?? [])
                    : m.content}
                {m.role === 'assistant' && !m.streaming && <ProvenanceRow message={m} />}
                {m.role === 'assistant' && !m.streaming && (
                  <ChatQuickActions
                    symbols={Array.from(new Set([...(m.focus ?? []), ...(m.partial ?? [])]))
                      .filter(s => !s.startsWith('^'))}
                    watchlistIndex={watchlistIndex}
                    onWatchlisted={markSymbolWatchlisted}
                    onWatchlistCreated={addWatchlistToIndex}
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>

      <form className="chat-input-row" onSubmit={handleSend}>
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Ask about any stock, your watchlist, or the market…"
          maxLength={2000}
          disabled={loading || sending || !sessionId}
        />
        <button
          type="submit"
          className={`btn btn-primary ${sending ? 'btn-loading' : ''}`}
          disabled={loading || sending || !input.trim() || !sessionId}
        >
          {sending ? '⟳' : 'Send'}
        </button>
      </form>
    </div>
  );
}

/** Per-message row showing which tickers the answer was grounded in. */
function ProvenanceRow({ message }: { message: ChatMessage }) {
  const focus = message.focus ?? [];
  const partial = message.partial ?? [];
  const unavailable = message.unavailable ?? [];
  const grounded = message.grounded;
  const total = focus.length + partial.length + unavailable.length;

  // Clean single-ticker fully-grounded answer — a quiet check, or nothing.
  if (total <= 1 && !partial.length && !unavailable.length && grounded !== false) {
    return focus.length === 1 ? (
      <span className="chat-provenance">
        <span className="chat-pill ok" title={`Grounded in live quant data for ${focus[0]}`}>
          {focus[0]} ✓
        </span>
      </span>
    ) : null;
  }

  // No ticket resolved at all, but the model flagged it couldn't answer.
  if (total === 0 && grounded === false) {
    return <span className="chat-ungrounded-tag">not fully grounded</span>;
  }

  return (
    <span className="chat-provenance">
      {focus.map(s => (
        <span key={s} className="chat-pill ok" title={`Grounded in live quant data for ${s}`}>
          {s} ✓
        </span>
      ))}
      {partial.map(s => (
        <span
          key={s}
          className="chat-pill partial"
          title={`Partial data for ${s}: live price / RSI / support-resistance only — not in your watchlist, so no multi-timeframe trend or confidence`}
        >
          {s} ◐ partial
        </span>
      ))}
      {unavailable.map(s => (
        <span key={s} className="chat-pill none" title={`No quant data for ${s}`}>
          {s} ✗ no data
        </span>
      ))}
    </span>
  );
}

/**
 * Per-ticker quick-action buttons on an assistant bubble ("➕ Watchlist",
 * "🔔 Alert") — UI shortcuts that call the same REST endpoints the
 * Watchlist/Alerts pages use directly, independent of the chat's own
 * add_to_watchlist / create_alert AI tools (either path works alone).
 * Aware of watchlistIndex: already-watchlisted tickers show a plain
 * tag instead of the add button, and adding with 2+ lists asks which
 * one instead of guessing.
 */
function ChatQuickActions({ symbols, watchlistIndex, onWatchlisted, onWatchlistCreated }: {
  symbols: string[];
  watchlistIndex: WatchlistIndex | null;
  onWatchlisted: (symbol: string, watchlistId: number) => void;
  onWatchlistCreated: (wl: WatchlistOption) => void;
}) {
  if (symbols.length === 0) return null;
  return (
    <div className="chat-quick-actions">
      {symbols.map(sym => (
        <TickerQuickActions
          key={sym}
          symbol={sym}
          watchlistIndex={watchlistIndex}
          onWatchlisted={onWatchlisted}
          onWatchlistCreated={onWatchlistCreated}
        />
      ))}
    </div>
  );
}

type QuickActionState = 'idle' | 'busy' | 'done' | 'error';

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

function TickerQuickActions({ symbol, watchlistIndex, onWatchlisted, onWatchlistCreated }: {
  symbol: string;
  watchlistIndex: WatchlistIndex | null;
  onWatchlisted: (symbol: string, watchlistId: number) => void;
  onWatchlistCreated: (wl: WatchlistOption) => void;
}) {
  const [watchlistState, setWatchlistState] = useState<QuickActionState>('idle');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickedWatchlistId, setPickedWatchlistId] = useState<number | ''>('');
  const [alertOpen, setAlertOpen] = useState(false);
  const [alertCondition, setAlertCondition] = useState('price_above');
  const [alertValue, setAlertValue] = useState('');
  const [alertState, setAlertState] = useState<QuickActionState>('idle');

  const existingWatchlistId = watchlistIndex?.memberOf[symbol];
  const alreadyWatchlisted = watchlistState === 'done' || existingWatchlistId !== undefined;
  const lists = watchlistIndex?.lists ?? [];

  const addToList = useCallback(async (watchlistId: number) => {
    setWatchlistState('busy');
    try {
      await api.addSymbolToWatchlist(watchlistId, symbol);
      setWatchlistState('done');
      setPickerOpen(false);
      onWatchlisted(symbol, watchlistId);
    } catch {
      setWatchlistState('error');
    }
  }, [symbol, onWatchlisted]);

  const handleAddToWatchlist = useCallback(async () => {
    // 2+ lists and the trader hasn't said which — ask instead of
    // guessing, same rule the chat's own add_to_watchlist tool follows.
    if (lists.length > 1) {
      setPickedWatchlistId(lists[0].id);
      setPickerOpen(true);
      return;
    }
    if (lists.length === 1) {
      addToList(lists[0].id);
      return;
    }
    // No watchlist exists yet — create a default one, same as the AI tool.
    setWatchlistState('busy');
    try {
      const created = await api.createWatchlist('Watchlist');
      onWatchlistCreated({ id: created.id, name: created.name });
      await addToList(created.id);
    } catch {
      setWatchlistState('error');
    }
  }, [lists, addToList, onWatchlistCreated]);

  const handlePickWatchlist = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    if (pickedWatchlistId !== '') addToList(pickedWatchlistId);
  }, [pickedWatchlistId, addToList]);

  const handleCreateAlert = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!alertValue.trim()) return;
    setAlertState('busy');
    try {
      await api.createAlert({
        name: `${symbol} ${alertCondition.replace(/_/g, ' ')}`,
        symbol,
        condition_type: alertCondition,
        parameter: alertValue.trim(),
      });
      setAlertState('done');
      setAlertOpen(false);  // collapse back to the button row so "✓ Alert set" shows
    } catch {
      setAlertState('error');
    }
  }, [symbol, alertCondition, alertValue]);

  return (
    <div className="chat-quick-action">
      <span className="chat-quick-action-symbol">{symbol}</span>

      {alreadyWatchlisted ? (
        <span className="chat-quick-action-tag" title={`${symbol} is already on a watchlist`}>
          ✓ Watchlisted
        </span>
      ) : !pickerOpen ? (
        <button
          type="button"
          className="chat-quick-action-btn"
          onClick={handleAddToWatchlist}
          disabled={watchlistState === 'busy'}
          title={
            lists.length > 1
              ? `Add ${symbol} to one of your ${lists.length} watchlists`
              : `Add ${symbol} to your watchlist`
          }
        >
          {watchlistState === 'busy' ? '⟳' : watchlistState === 'error' ? '⚠ retry' : '➕ Watchlist'}
        </button>
      ) : (
        <form className="chat-quick-alert-form" onSubmit={handlePickWatchlist}>
          <select
            value={pickedWatchlistId}
            onChange={e => setPickedWatchlistId(Number(e.target.value))}
            aria-label={`Which watchlist to add ${symbol} to`}
          >
            {lists.map(wl => <option key={wl.id} value={wl.id}>{wl.name}</option>)}
          </select>
          <button
            type="submit"
            className="chat-quick-action-btn"
            disabled={watchlistState === 'busy'}
          >
            {watchlistState === 'busy' ? '⟳' : 'Add'}
          </button>
          <button type="button" className="chat-quick-action-btn" onClick={() => setPickerOpen(false)}>
            ✕
          </button>
        </form>
      )}

      {!alertOpen ? (
        <button
          type="button"
          className="chat-quick-action-btn"
          onClick={() => setAlertOpen(true)}
          disabled={alertState === 'done'}
          title={`Set an alert on ${symbol}`}
        >
          {alertState === 'done' ? '✓ Alert set' : '🔔 Alert'}
        </button>
      ) : (
        <form className="chat-quick-alert-form" onSubmit={handleCreateAlert}>
          <select
            value={alertCondition}
            onChange={e => {
              // The parameter's meaning changes with the condition (a
              // signal name vs. a number) — a leftover value from the
              // other shape would silently submit as garbage.
              setAlertCondition(e.target.value);
              setAlertValue('');
            }}
            aria-label={`Alert condition for ${symbol}`}
          >
            <option value="signal_equals">Signal equals</option>
            <option value="price_above">Price above</option>
            <option value="price_below">Price below</option>
            <option value="pct_change_above">% change above</option>
          </select>
          {alertCondition === 'signal_equals' ? (
            <select
              value={alertValue}
              onChange={e => setAlertValue(e.target.value)}
              className="chat-quick-alert-input"
              aria-label={`Alert threshold for ${symbol}`}
            >
              <option value="" disabled>signal…</option>
              {SIGNAL_PARAMETERS.map(s => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          ) : (
            <input
              type="number"
              step="any"
              placeholder="value"
              value={alertValue}
              onChange={e => setAlertValue(e.target.value)}
              className="chat-quick-alert-input"
              aria-label={`Alert threshold for ${symbol}`}
            />
          )}
          <button
            type="submit"
            className="chat-quick-action-btn"
            disabled={alertState === 'busy' || !alertValue.trim()}
          >
            {alertState === 'busy' ? '⟳' : 'Set'}
          </button>
          <button type="button" className="chat-quick-action-btn" onClick={() => setAlertOpen(false)}>
            ✕
          </button>
        </form>
      )}
    </div>
  );
}

export default ChatPanel;
