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
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

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
                  : m.content}
                {m.role === 'assistant' && !m.streaming && <ProvenanceRow message={m} />}
                {m.role === 'assistant' && !m.streaming && (
                  <ChatQuickActions
                    symbols={Array.from(new Set([...(m.focus ?? []), ...(m.partial ?? [])]))
                      .filter(s => !s.startsWith('^'))}
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
 * Deliberately dumb: no disambiguation prompt if several watchlists
 * exist — just uses the first one, since one click should stay one click.
 */
function ChatQuickActions({ symbols }: { symbols: string[] }) {
  if (symbols.length === 0) return null;
  return (
    <div className="chat-quick-actions">
      {symbols.map(sym => <TickerQuickActions key={sym} symbol={sym} />)}
    </div>
  );
}

type QuickActionState = 'idle' | 'busy' | 'done' | 'error';

function TickerQuickActions({ symbol }: { symbol: string }) {
  const [watchlistState, setWatchlistState] = useState<QuickActionState>('idle');
  const [alertOpen, setAlertOpen] = useState(false);
  const [alertCondition, setAlertCondition] = useState('price_above');
  const [alertValue, setAlertValue] = useState('');
  const [alertState, setAlertState] = useState<QuickActionState>('idle');

  const handleAddToWatchlist = useCallback(async () => {
    setWatchlistState('busy');
    try {
      const lists = await api.getWatchlists();
      const target = lists[0] ?? await api.createWatchlist('Watchlist');
      await api.addSymbolToWatchlist(target.id, symbol);
      setWatchlistState('done');
    } catch {
      setWatchlistState('error');
    }
  }, [symbol]);

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
      <button
        type="button"
        className="chat-quick-action-btn"
        onClick={handleAddToWatchlist}
        disabled={watchlistState === 'busy' || watchlistState === 'done'}
        title={`Add ${symbol} to your watchlist`}
      >
        {watchlistState === 'done' ? '✓ Watchlist'
          : watchlistState === 'busy' ? '⟳'
          : watchlistState === 'error' ? '⚠ retry'
          : '➕ Watchlist'}
      </button>

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
            onChange={e => setAlertCondition(e.target.value)}
            aria-label={`Alert condition for ${symbol}`}
          >
            <option value="price_above">Price above</option>
            <option value="price_below">Price below</option>
            <option value="pct_change_above">% change above</option>
          </select>
          <input
            type="number"
            step="any"
            placeholder="value"
            value={alertValue}
            onChange={e => setAlertValue(e.target.value)}
            className="chat-quick-alert-input"
            aria-label={`Alert threshold for ${symbol}`}
          />
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
