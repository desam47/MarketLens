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
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api, { AlertConversationContext, ChatChartState, ChatMessage, ChatPreferences as ChatPreferencesType, ChatRegenerationMode, ChatRegenerationScope, ChatResponseBlock } from '../services/api';
import { highlightMessage } from '../utils/textHighlight';
import type { AppPage, NavigationState } from '../utils/appNavigation';
import { loadChartState } from '../utils/chartState';
import { ChatPreferencesPanel } from './ChatPreferencesPanel';
import { ChatSharingSettings, collectChatBrowserData, isChatBrowserDataRelevant, isSharingAnything, loadChatSharing, saveChatSharing } from '../utils/chatBrowserData';
import {
  createChatNotebook,
  getChatNotebookClientKey,
  loadChatNotebooks,
  mergeServerChatNotebooks,
  removeChatNotebook,
  removeChatNotebookItem,
  renameChatNotebook,
  saveMessageToChatNotebook,
  type ChatNotebook,
} from '../utils/chatNotebooks';
import {
  isDefaultChatPreferences,
  loadChatPreferences,
  resetChatPreferences,
  saveChatPreferences,
} from '../utils/chatPreferences';

interface ChatPanelProps {
  alertTriggerId?: number | null;
  alertSymbol?: string | null;
  alertContext?: AlertConversationContext | null;
  /**
   * Called with the primary ticker a chat turn resolved to (the first
   * `focus`, else the first `partial`). The AI Hub uses this to point
   * its Analysis / Templates sections at whatever the trader just asked
   * about. Not called for market-wide turns or when only `unavailable`
   * tickers were named. Fires at most once per turn.
   */
  onSymbolResolved?: (symbol: string) => void;
  onNavigate?: (page: AppPage, symbol?: string, navigation?: NavigationState) => void;
}

// A message in local state may be a not-yet-finalized streaming bubble, or
// the partial text of a stream that broke before its final message.
type LocalMessage = ChatMessage & { streaming?: boolean; interrupted?: boolean };

// After the client stops waiting for a turn (Cancel, a timeout, a broken
// stream), the server still finishes it and saves the reply (BF-13). The
// panel checks for that reply this often, for this long.
const AWAIT_REPLY_POLL_MS = 4000;
const AWAIT_REPLY_TIMEOUT_MS = 120000;

/** Whether ``fresh`` holds a reply to the latest user message ``content``. */
export function replyArrived(fresh: ChatMessage[], content: string): boolean {
  const userIndex = fresh.map(m => m.role === 'user' && m.content === content).lastIndexOf(true);
  return userIndex !== -1 && fresh.slice(userIndex + 1).some(m => m.role === 'assistant');
}

/**
 * Merge a polled transcript into local state. Only the assistant
 * placeholder is swapped for the server row when a turn finishes; the
 * optimistic user message keeps its temporary negative id. A server user
 * row matching one of those is swapped in place, not appended, so the
 * poll doesn't show the trader's own message a second time.
 */
export function mergePolledMessages(prev: LocalMessage[], fresh: ChatMessage[]): LocalMessage[] {
  const known = new Set(prev.map(m => m.id));
  let next = prev;
  const additions: LocalMessage[] = [];
  for (const m of fresh) {
    if (known.has(m.id)) continue;
    if (m.role === 'user') {
      const idx = next.findIndex(x => x.id < 0 && x.role === 'user' && x.content === m.content);
      if (idx !== -1) {
        next = next.map((x, i) => (i === idx ? m : x));
        continue;
      }
    }
    additions.push(m);
  }
  return next === prev && !additions.length ? prev : [...next, ...additions];
}

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

const JOURNAL_STORAGE_KEY = 'marketlens.trade.journal';
const REPORT_PAGE_TARGETS: Record<string, AppPage> = {
  symbol: 'symbol',
  scanner: 'scanner',
  risk: 'risk',
  replay: 'signals',
  alerts: 'alerts',
  journal: 'journal',
  options: 'options',
  health: 'health',
};

function mergeSavedJournalEntry(entry: any): void {
  if (!entry || typeof entry !== 'object' || typeof window === 'undefined') return;
  try {
    const existing = JSON.parse(window.localStorage.getItem(JOURNAL_STORAGE_KEY) || '[]');
    const rows = Array.isArray(existing) ? existing : [];
    const normalized = {
      id: String(entry.id || `trade-${Date.now()}`),
      symbol: String(entry.symbol || '').toUpperCase(),
      side: entry.side === 'short' ? 'short' : 'long',
      status: entry.status === 'closed' || entry.status === 'open' ? entry.status : 'planned',
      entryDate: String(entry.entry_date || entry.entryDate || new Date().toISOString().slice(0, 10)),
      exitDate: entry.exit_date || entry.exitDate || null,
      quantity: Number(entry.quantity || 0),
      entryPrice: Number(entry.entry_price ?? entry.entryPrice ?? 0),
      exitPrice: entry.exit_price ?? entry.exitPrice ?? null,
      stopPrice: entry.stop_price ?? entry.stopPrice ?? null,
      targetPrice: entry.target_price ?? entry.targetPrice ?? null,
      thesis: String(entry.notes || entry.thesis || ''),
      screenshotDataUrl: entry.screenshot_data_url || entry.screenshotDataUrl || null,
      reviewNotes: String(entry.review_notes || entry.reviewNotes || ''),
      signalContext: entry.signal_context || entry.signalContext || null,
      marketContext: entry.market_context || entry.marketContext || null,
      createdAt: String(entry.created_at || entry.createdAt || new Date().toISOString()),
      updatedAt: new Date().toISOString(),
    };
    const withoutDuplicate = rows.filter((row: any) => row?.id !== normalized.id);
    window.localStorage.setItem(JOURNAL_STORAGE_KEY, JSON.stringify([...withoutDuplicate, normalized]));
  } catch {
    // The Journal page will still show the save confirmation from Chat.
  }
}

function persistJournalBlocks(blocks: ChatResponseBlock[] | undefined): void {
  for (const block of blocks || []) {
    if (block.type === 'journal_save') mergeSavedJournalEntry(block.data.saved_entry);
  }
}

const EXAMPLES = [
  "How's NVDA looking?",
  "What's the market doing today?",
  'Which of my names look weak?',
  'Compare AAPL and MSFT',
];

export function ChatPanel({
  alertTriggerId = null,
  alertSymbol = null,
  alertContext = null,
  onSymbolResolved,
  onNavigate,
}: ChatPanelProps) {
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [slow, setSlow] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [memoryNotice, setMemoryNotice] = useState<string | null>(null);
  // The stream in flight. ``teardown`` marks an abort caused by leaving the
  // session (switch or unmount), after which nothing should be updated.
  const streamRef = useRef<{ controller: AbortController; teardown: boolean } | null>(null);
  const [cancellable, setCancellable] = useState(false);
  const [streamNotice, setStreamNotice] = useState<string | null>(null);
  const [awaitingReply, setAwaitingReply] = useState<{ content: string; since: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionAttempt, setSessionAttempt] = useState(0);
  const [watchlistIndex, setWatchlistIndex] = useState<WatchlistIndex | null>(null);
  const [preferences, setPreferences] = useState<ChatPreferencesType>(() => loadChatPreferences());
  const [sharing, setSharing] = useState<ChatSharingSettings>(() => loadChatSharing());
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const [chartState, setChartState] = useState<ChatChartState | null>(() => loadChartState());
  const [notebooks, setNotebooks] = useState<ChatNotebook[]>(() => loadChatNotebooks());
  const [notebooksOpen, setNotebooksOpen] = useState(false);
  const [newNotebookName, setNewNotebookName] = useState('');
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const navigateFromChat = useCallback((page: AppPage, targetSymbol?: string, navigation?: NavigationState) => {
    const state = chartState;
    const nextNavigation = {
      ...navigation,
      ...(page === 'symbol' && state ? { timeframe: state.timeframe, session: state.session } : {}),
    };
    if (Object.keys(nextNavigation).length > 0) onNavigate?.(page, targetSymbol, nextNavigation);
    else onNavigate?.(page, targetSymbol);
  }, [chartState, onNavigate]);

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

  useEffect(() => {
    let cancelled = false;
    const loadServerNotebooks = (api as any).getChatNotebooks as ((clientKey: string) => Promise<any[]>) | undefined;
    if (!loadServerNotebooks) return () => { cancelled = true; };
    loadServerNotebooks(getChatNotebookClientKey()).then(serverNotebooks => {
      if (!cancelled && Array.isArray(serverNotebooks) && serverNotebooks.length) {
        setNotebooks(mergeServerChatNotebooks(serverNotebooks));
      }
    }).catch(() => { /* local notebooks remain available when the server is offline */ });
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
    setStreamNotice(null);
    setAwaitingReply(null);

    (async () => {
      try {
        // Alert sessions retain the triggering symbol so the first follow-up
        // automatically receives the same ticker context as the attachment.
        const session = await api.createChatSession(alertSymbol ?? undefined, alertTriggerId);
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

    return () => {
      cancelled = true;
      // Leaving the session (or unmounting) stops the stream; its turn
      // belongs to the old session, so nothing may be updated from it.
      if (streamRef.current) {
        streamRef.current.teardown = true;
        streamRef.current.controller.abort();
      }
    };
  }, [alertTriggerId, alertSymbol, sessionAttempt]);

  const handleClear = useCallback(async () => {
    setClearing(true);
    setError(null);
    try {
      // Actually flush the history — sessions + messages — not just
      // hide it behind a new session.
      await api.clearChatHistory(alertTriggerId ?? undefined);
      const session = await api.createChatSession(alertSymbol ?? undefined, alertTriggerId, true);
      setSessionId(session.id);
      setMessages([]);
      setInput('');
    } catch (e: any) {
      setError(e?.message || 'Failed to clear chat');
    } finally {
      setClearing(false);
    }
  }, [alertTriggerId, alertSymbol]);

  const handleResetMemory = useCallback(async () => {
    if (!sessionId) return;
    setError(null);
    try {
      await api.resetChatMemory(sessionId);
      setMemoryNotice('Chat memory reset — remembered tickers, watchlist, timeframe, dates, and calculations are cleared. Messages are kept.');
    } catch (e: any) {
      setError(e?.message || 'Failed to reset chat memory');
    }
  }, [sessionId]);

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
  //
  // The same poll also watches for a reply the client stopped waiting for
  // (see awaitServerReply) — faster, and in alert sessions too, until the
  // reply arrives or AWAIT_REPLY_TIMEOUT_MS passes.
  useEffect(() => {
    if (!sessionId || (alertTriggerId && !awaitingReply)) return;
    const interval = setInterval(async () => {
      if (sending) return;
      try {
        const fresh = await api.getChatMessages(sessionId);
        if (awaitingReply && replyArrived(fresh, awaitingReply.content)) {
          setMessages(prev => mergePolledMessages(prev.filter(m => !m.interrupted), fresh));
          setAwaitingReply(null);
          setStreamNotice(null);
          return;
        }
        setMessages(prev => mergePolledMessages(prev, fresh));
        if (awaitingReply && Date.now() - awaitingReply.since > AWAIT_REPLY_TIMEOUT_MS) {
          const saved = fresh.some(m => m.role === 'user' && m.content === awaitingReply.content);
          setAwaitingReply(null);
          if (saved) {
            setStreamNotice("The reply hasn't arrived yet. It will appear here once it's saved.");
          } else {
            // The turn never reached the server: nothing is coming.
            setMessages(prev => prev.filter(m => !(m.id < 0 && m.role === 'user' && m.content === awaitingReply.content) && !m.interrupted));
            setStreamNotice(null);
            setError('That message never reached the server — please send it again.');
          }
        }
      } catch {
        // best-effort — a missed poll just tries again next tick
      }
    }, awaitingReply ? AWAIT_REPLY_POLL_MS : 20000);
    return () => clearInterval(interval);
  }, [sessionId, alertTriggerId, sending, awaitingReply]);

  // The server keeps running a turn after the client stops listening and saves
  // its reply (BF-13), so a stopped or broken stream is never resent — that
  // would repeat any action the turn took. Show what the server has stored,
  // then watch for the reply.
  const awaitServerReply = useCallback(async (
    targetSessionId: number,
    content: string,
    placeholderId: number,
    keepPlaceholder: boolean,
  ): Promise<boolean> => {
    try {
      const fresh = await api.getChatMessages(targetSessionId);
      if (replyArrived(fresh, content)) {
        setMessages(prev => mergePolledMessages(prev.filter(m => m.id !== placeholderId && !m.interrupted), fresh));
        return true;
      }
      setMessages(prev => mergePolledMessages(keepPlaceholder ? prev : prev.filter(m => m.id !== placeholderId), fresh));
    } catch {
      if (!keepPlaceholder) setMessages(prev => prev.filter(m => m.id !== placeholderId));
    }
    setAwaitingReply({ content, since: Date.now() });
    return false;
  }, []);

  const submit = useCallback(async (content: string, regenerationMode?: RegenerationMode, regenerationScope?: ChatRegenerationScope) => {
    if (!content || !sessionId || sending) return;
    setSending(true);
    setError(null);
    const currentChartState = loadChartState();
    // Read the opted-in snapshot at send time so it reflects the current
    // Risk Dashboard / Journal / preset state, not whatever was stored when
    // the panel mounted.
    const browserData = isChatBrowserDataRelevant(content, sharing)
      ? collectChatBrowserData(sharing, content)
      : null;
    const apiRegenerationMode = regenerationMode;
    setChartState(currentChartState);
    setStreamNotice(null);
    const stream = { controller: new AbortController(), teardown: false };
    streamRef.current = stream;
    setCancellable(true);
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
    let streamedContent = '';
    try {
      const finalMsg = await api.streamChatMessage(sessionId, content, {
        onMeta: m => {
          patchPlaceholder({ focus: m.focus, partial: m.partial, unavailable: m.unavailable });
          adoptSymbol(m.focus, m.partial);
        },
        onDelta: t => {
          sawDelta = true;
          streamedContent += t;
          setMessages(prev =>
            prev.map(m => (m.id === placeholderId ? { ...m, content: m.content + t } : m)),
          );
        },
        preferences: isDefaultChatPreferences(preferences) ? null : preferences,
        chartState: currentChartState,
        regenerationMode: apiRegenerationMode,
        regenerationScope,
        browserData,
        signal: stream.controller.signal,
      });
      persistJournalBlocks(finalMsg.blocks);
      setMessages(prev => prev.map(m => (m.id === placeholderId ? finalMsg : m)));
      adoptSymbol(finalMsg.focus, finalMsg.partial);
    } catch (e: any) {
      // A new session or an unmount stopped the stream; its turn belongs to
      // the session being left.
      if (stream.teardown) return;
      setCancellable(false);
      if (e?.aborted || e?.timedOut) {
        setStreamNotice(e?.timedOut
          ? 'The reply is taking longer than expected. It will appear here when it is ready.'
          : 'Stopped waiting for this reply. The server still finishes it, so it will appear here shortly.');
        if (await awaitServerReply(sessionId, content, placeholderId, false)) setStreamNotice(null);
        return;
      }
      // Deltas only follow `meta`, so seeing one means the turn started too.
      const turnStarted = Boolean(e?.turnStarted) || sawDelta;
      if (turnStarted && e?.serverFailed) {
        // The server reported the failure itself; no reply is coming. Show
        // what it stored instead of resending (which would repeat any actions).
        setError(e?.message || 'The chat turn failed after it started.');
        try {
          const fresh = await api.getChatMessages(sessionId);
          setMessages(prev => mergePolledMessages(prev.filter(m => m.id !== placeholderId), fresh));
        } catch {
          setMessages(prev => prev.filter(m => m.id !== placeholderId));
        }
        return;
      }
      if (turnStarted) {
        // The connection broke mid-turn; the server still finishes it.
        const partial = sawDelta && Boolean(streamedContent);
        if (partial) {
          patchPlaceholder({
            content: `${streamedContent}\n\nStream interrupted before verification completed. The verified answer will appear here when it is saved.`,
            grounded: false,
            streaming: false,
            interrupted: true,
          });
        } else {
          setStreamNotice('The connection dropped. The reply will appear here when it is saved.');
        }
        if (await awaitServerReply(sessionId, content, placeholderId, partial)) setStreamNotice(null);
        return;
      }
      if (e?.beforeFirstDelta && !sawDelta) {
        // Stream never started — fall back to the plain blocking endpoint.
        // Nothing was saved (the server saves the user message only once
        // the turn starts), so this is not a duplicate.
        try {
          const sentPreferences = isDefaultChatPreferences(preferences) ? null : preferences;
          const finalMsg = (apiRegenerationMode || regenerationScope || browserData)
            ? await api.sendChatMessage(sessionId, content, sentPreferences, currentChartState, apiRegenerationMode, regenerationScope, browserData)
            : currentChartState
              ? await api.sendChatMessage(sessionId, content, sentPreferences, currentChartState)
              : await api.sendChatMessage(sessionId, content, sentPreferences);
          persistJournalBlocks(finalMsg.blocks);
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
      if (streamRef.current === stream) streamRef.current = null;
      // Unlock the input unless a newer send is already in flight (a torn-
      // down stream's cleanup can land after the next session's first send).
      if (streamRef.current === null) {
        setCancellable(false);
        setSending(false);
      }
    }
  }, [sessionId, sending, onSymbolResolved, preferences, sharing, awaitServerReply]);

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
          <div className="chat-panel-header-actions">
            <button
              type="button"
              className={`btn btn-secondary ${!isDefaultChatPreferences(preferences) ? 'chat-pref-set' : ''}`}
              onClick={() => setPreferencesOpen(open => !open)}
              title="Personal preferences — mode, timeframes, risk, detail level"
              aria-expanded={preferencesOpen}
            >
              ⚙ Preferences{!isDefaultChatPreferences(preferences) || isSharingAnything(sharing) ? ' •' : ''}
            </button>
            <button
              type="button"
              className={`btn btn-secondary ${notebooks.length ? 'chat-pref-set' : ''}`}
              onClick={() => setNotebooksOpen(open => !open)}
              aria-expanded={notebooksOpen}
              title="Save grounded answers into local research notebooks"
            >
              📓 Notebooks
            </button>
            <button
              type="button"
              className={`btn btn-secondary ${clearing ? 'btn-loading' : ''}`}
              onClick={handleClear}
              disabled={loading || clearing || messages.length === 0}
              title="Permanently delete this chat's history and start fresh"
            >
              {clearing ? '⟳' : '🗑 Clear'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleResetMemory}
              disabled={loading || !sessionId || sending}
              title="Forget remembered tickers, watchlist, timeframe, dates, and calculations; keep the messages"
            >
              ↺ Reset memory
            </button>
          </div>
        </div>
        {memoryNotice && <div className="chat-draft-status" role="status">{memoryNotice}</div>}
        {preferencesOpen && (
          <ChatPreferencesPanel
            preferences={preferences}
            onChange={next => {
              setPreferences(next);
              saveChatPreferences(next);
            }}
            onReset={() => setPreferences(resetChatPreferences())}
            onClose={() => setPreferencesOpen(false)}
            sharing={sharing}
            onSharingChange={next => {
              setSharing(next);
              saveChatSharing(next);
            }}
          />
        )}
        {notebooksOpen && (
          <ChatNotebookPanel
            notebooks={notebooks}
            newName={newNotebookName}
            onNewNameChange={setNewNotebookName}
            onCreate={async () => {
              const notebook = createChatNotebook(newNotebookName);
              setNotebooks(current => [notebook, ...current]);
              setNewNotebookName('');
              const createServerNotebook = (api as any).createChatNotebook as ((clientKey: string, name: string) => Promise<unknown>) | undefined;
              if (createServerNotebook) {
                try {
                  const serverNotebook = await createServerNotebook(getChatNotebookClientKey(), notebook.name);
                  const loadServerNotebooks = (api as any).getChatNotebooks as ((clientKey: string) => Promise<any[]>) | undefined;
                  if (loadServerNotebooks && serverNotebook) {
                    const serverNotebooks = await loadServerNotebooks(getChatNotebookClientKey());
                    if (Array.isArray(serverNotebooks)) setNotebooks(mergeServerChatNotebooks(serverNotebooks));
                  }
                } catch { /* browser-local notebook remains the fallback */ }
              }
            }}
            onRename={async (notebookId, name) => {
              const serverNotebookId = notebookId.startsWith('server-') ? Number(notebookId.slice(7)) : null;
              const renameServerNotebook = (api as any).renameChatNotebook as ((clientKey: string, id: number, name: string) => Promise<unknown>) | undefined;
              if (serverNotebookId && renameServerNotebook) {
                await renameServerNotebook(getChatNotebookClientKey(), serverNotebookId, name);
              }
              setNotebooks(renameChatNotebook(notebookId, name));
            }}
            onDelete={async notebookId => {
              const serverNotebookId = notebookId.startsWith('server-') ? Number(notebookId.slice(7)) : null;
              const deleteServerNotebook = (api as any).deleteChatNotebook as ((clientKey: string, id: number) => Promise<unknown>) | undefined;
              if (serverNotebookId && deleteServerNotebook) {
                await deleteServerNotebook(getChatNotebookClientKey(), serverNotebookId);
              }
              setNotebooks(removeChatNotebook(notebookId));
            }}
            onDeleteItem={async (notebookId, itemId) => {
              const serverNotebookId = notebookId.startsWith('server-') ? Number(notebookId.slice(7)) : null;
              const serverItemId = itemId.startsWith('server-item-') ? Number(itemId.slice(12)) : null;
              const deleteServerItem = (api as any).deleteChatNotebookItem as ((clientKey: string, notebookId: number, itemId: number) => Promise<unknown>) | undefined;
              if (serverNotebookId && serverItemId && deleteServerItem) {
                await deleteServerItem(getChatNotebookClientKey(), serverNotebookId, serverItemId);
              }
              setNotebooks(removeChatNotebookItem(notebookId, itemId));
            }}
          />
        )}
        <p className="info-text">
          Uses live quant data where available — full coverage for your watchlist,
          price&nbsp;+&nbsp;indicators only for other tickers. Research to inform your
          own decision, not financial advice.
        </p>
        {chartState && (
          <small className="chat-chart-state-banner" role="status">
            Chart context ready: {chartState.symbol} · {chartState.timeframe} · {chartState.session}
            {chartState.selected_candle ? ' · selected candle' : ''}
          </small>
        )}
        {alertContext && <AlertContextAttachment context={alertContext} />}
      </div>

      {error && (
        <div className="chat-panel-error">
          ⚠️ {error}
          <button className="btn btn-small data-state-retry" onClick={() => setSessionAttempt(attempt => attempt + 1)}>Retry</button>
        </div>
      )}

      {streamNotice && <div className="chat-draft-status chat-stream-notice" role="status">{streamNotice}</div>}

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
        {messages.map((m, messageIndex) => {
          const pending = m.streaming && !m.content;
          // Streamed text arrives before server-side answer verification; it
          // is shown as a labeled draft until the authoritative final message
          // (possibly replaced with explicit uncertainty) arrives.
          const draft = m.role === 'assistant' && m.streaming && Boolean(m.content);
          const previousUser = messages.slice(0, messageIndex).reverse().find(item => item.role === 'user');
          return (
            <div key={m.id} className={`chat-bubble-row ${m.role}`}>
              <div className={`chat-bubble ${m.role}${pending ? ' chat-bubble-pending' : ''}${draft ? ' chat-bubble-draft' : ''}`}>
                {pending
                  ? (slow ? 'Still working — pulling data for the tickers you mentioned…' : '…')
                  : m.role === 'assistant'
                    ? highlightMessage(m.content, m.focus ?? [], m.partial ?? [], m.unavailable ?? [])
                    : m.content}
                {draft && <span className="chat-draft-status" role="status">Unverified draft — numbers are checked before this answer is final.</span>}
                {m.role === 'assistant' && !m.streaming && <TypedResponseBlocks blocks={m.blocks ?? []} onNavigate={navigateFromChat} />}
                {m.role === 'assistant' && !m.streaming && <ProvenanceRow message={m} />}
                {m.role === 'assistant' && !m.streaming && <ToolTraceRow message={m} />}
                {m.role === 'assistant' && !m.streaming && (
                  <ChatQuickActions
                    symbols={Array.from(new Set([...(m.focus ?? []), ...(m.partial ?? [])]))
                      .filter(s => !s.startsWith('^'))}
                    watchlistIndex={watchlistIndex}
                    onWatchlisted={markSymbolWatchlisted}
                    onWatchlistCreated={addWatchlistToIndex}
                    onNavigate={navigateFromChat}
                  />
                )}
                {m.role === 'assistant' && !m.streaming && m.id > 0 && (
                  <ChatFeedbackRow
                    message={m}
                    onSaved={(messageId, feedback) =>
                      setMessages(prev => prev.map(msg => (msg.id === messageId ? { ...msg, feedback } : msg)))
                    }
                  />
                )}
                {m.role === 'assistant' && !m.streaming && m.id > 0 && previousUser && (
                  <ChatRegenerationRow
                    stale={messageNeedsRefresh(m)}
                    currentChartState={chartState}
                    preferredTimeframes={preferences.preferred_timeframes}
                    // The question is resent unchanged; the backend applies the
                    // typed mode/scope, so no instruction text enters the transcript.
                    onRegenerate={(mode, scope) => submit(previousUser.content, mode, scope)}
                  />
                )}
                {m.role === 'assistant' && !m.streaming && m.id > 0 && (
                  <NotebookSaveButton
                    message={m}
                    question={previousUser?.content ?? ''}
                    notebooks={notebooks}
                    onSaved={(updated, notebookId) => {
                      setNotebooks(updated);
                      const serverNotebookId = notebookId.startsWith('server-') ? Number(notebookId.slice(7)) : null;
                      const saveServerItem = (api as any).saveChatNotebookItem as ((clientKey: string, id: number, messageId: number, question: string) => Promise<unknown>) | undefined;
                      if (serverNotebookId && saveServerItem) {
                        saveServerItem(getChatNotebookClientKey(), serverNotebookId, m.id, previousUser?.content ?? '').catch(() => { /* local copy remains */ });
                      }
                    }}
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
        {sending && cancellable && (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => streamRef.current?.controller.abort()}
            aria-label="Cancel reply"
            title="Stop waiting for this reply. The server still finishes it, and the reply appears here."
          >
            Cancel
          </button>
        )}
      </form>
    </div>
  );
}

/**
 * Human-readable summary of an action_confirmation block's `detail` —
 * built from the request's own typed action_* fields (see chat.py's
 * _action_step_detail), never from parsing prose. Returns null for a
 * tool with nothing tool-specific to add.
 */
function describeActionDetail(tool: string, detail: any): string | null {
  if (!detail || typeof detail !== 'object') return null;
  switch (tool) {
    case 'create_alert':
    case 'modify_alert': {
      const parts = [detail.symbol, detail.condition_type ? String(detail.condition_type).replace(/_/g, ' ') : null, detail.parameter].filter(Boolean);
      return parts.length ? parts.join(' ') : null;
    }
    case 'delete_alert':
      return detail.target_id != null ? `alert #${detail.target_id}` : null;
    case 'add_to_watchlist':
      return detail.symbol ? `${detail.symbol} → ${detail.watchlist ?? 'watchlist'}` : null;
    case 'remove_from_watchlist':
      return detail.symbol ? `${detail.symbol} from ${detail.watchlist ?? 'watchlist'}` : null;
    case 'create_watchlist':
      return detail.watchlist ? `"${detail.watchlist}"` : null;
    case 'delete_watchlist':
      return detail.watchlist ? `"${detail.watchlist}"` : detail.target_id != null ? `#${detail.target_id}` : null;
    default:
      return null;
  }
}

/**
 * Client-side scenario-slider preview. Mirrors scenario_analysis_tool's
 * linear price-shock formula exactly (backend/ai/market_tools.py):
 * scenario_price = base_price * (1 + shock / 100). total_pnl_delta only
 * depends on the PRICE CHANGE — entry_price cancels out of the delta
 * algebraically (pnl_delta = (scenario_price - base_price) * quantity *
 * sign) — so this needs nothing beyond what the verified block already
 * exposes per position (base_price, quantity, side). stop-loss risk is
 * NOT shock-dependent in the backend model (a function of stop_price and
 * entry_price only, neither of which moves with a price shock), so it is
 * intentionally never recomputed here — always shown at its original
 * verified value regardless of slider position.
 */
function recomputeScenario(positions: any[], shockPercent: number): { grossExposure: number; totalPnlDelta: number } {
  let grossExposure = 0;
  let totalPnlDelta = 0;
  for (const position of positions) {
    const basePrice = Number(position?.base_price);
    const quantity = Number(position?.quantity);
    if (!Number.isFinite(basePrice) || !Number.isFinite(quantity)) continue;
    const sign = position.side === 'short' ? -1 : 1;
    const scenarioPrice = basePrice * (1 + shockPercent / 100);
    grossExposure += scenarioPrice * quantity;
    totalPnlDelta += (scenarioPrice - basePrice) * quantity * sign;
  }
  return { grossExposure, totalPnlDelta };
}

/** Render the application-owned typed envelope without parsing model markup. */
function TypedResponseBlocks({ blocks, onNavigate }: { blocks: ChatResponseBlock[]; onNavigate?: (page: AppPage, symbol?: string, navigation?: NavigationState) => void }) {
  const [copiedReportId, setCopiedReportId] = useState<string | null>(null);
  // Per-block "show all" toggles (evidence/options tables, mini chart)
  // keyed by block.id — expand the SAME bounded data already delivered,
  // never fetch more, so there's no new evidence-integrity surface here.
  const [expandedBlocks, setExpandedBlocks] = useState<Set<string>>(new Set());
  const toggleExpanded = (id: string) => setExpandedBlocks(current => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  // Per-block scenario-slider shock percent, keyed by block.id.
  const [scenarioShock, setScenarioShock] = useState<Record<string, number>>({});
  if (!blocks.length) return null;
  return (
    <div className="chat-typed-blocks" aria-label="Structured answer details">
      {blocks.filter(block => block.type !== 'prose').map(block => {
        const quality = block.quality;
        const qualityLabel = quality.state.replace('_', ' ');
        if (block.type === 'calculation') {
          const values = block.data.values && typeof block.data.values === 'object'
            ? Object.entries(block.data.values) : [];
          return (
            <section className="chat-typed-card chat-calculation-card" key={block.id} aria-label="Verified calculation">
              <div className="chat-typed-card-heading">🧮 Calculation <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
              <dl>{values.map(([key, value]) => <div key={key}><dt>{key.replace(/_/g, ' ')}</dt><dd>{String(value)}</dd></div>)}</dl>
              {Array.isArray(block.data.formulas) && block.data.formulas.length > 0 && (
                <div className="chat-calculation-formula">Formula: {String(block.data.formulas[0])}</div>
              )}
            </section>
          );
        }
        if (block.type === 'verification') {
          const status = String(block.data.status ?? 'degraded');
          const refs = Array.isArray(block.data.evidence_refs) ? block.data.evidence_refs : [];
          const issues = Array.isArray(block.data.issues) ? block.data.issues : [];
          const statusLabel = status === 'verified' ? 'Verified' : status === 'blocked' ? 'Needs review' : 'Limited verification';
          const expanded = expandedBlocks.has(block.id);
          return (
            <section className={`chat-typed-card chat-verification-card ${status}`} key={block.id} role="status" aria-label="Answer verification">
              <button type="button" className="chat-typed-card-heading chat-collapsible-heading" onClick={() => toggleExpanded(block.id)} aria-expanded={expanded}>
                Answer verification <span className={`chat-quality ${quality.state}`}>{statusLabel}</span>
                <span className="chat-collapse-chevron">{expanded ? '▴' : '▾'}</span>
              </button>
              {expanded && <>
                <p>{refs.length > 0 ? `Checked against ${refs.length} evidence source${refs.length === 1 ? '' : 's'}.` : 'No source evidence was available for this answer.'}</p>
                {issues.length > 0 && <ul>{issues.map((issue: string) => <li key={issue}>{formatVerificationIssue(issue)}</li>)}</ul>}
                <small>Verifier {String(block.data.version ?? 'unknown')}</small>
              </>}
            </section>
          );
        }
        if (block.type === 'evidence') {
          const symbols = block.data.symbols ?? {};
          const items = Array.isArray(block.data.items) ? block.data.items : [];
          const expanded = expandedBlocks.has(block.id);
          // Opening the card and showing every item are separate choices:
          // an open card still caps the list at 8 until "Show all".
          const showAllKey = `${block.id}:all`;
          const showAll = expandedBlocks.has(showAllKey);
          const visibleItems = showAll ? items : items.slice(0, 8);
          return (
            <section className="chat-typed-card chat-evidence-card" key={block.id} aria-label="Evidence">
              <button type="button" className="chat-typed-card-heading chat-collapsible-heading" onClick={() => toggleExpanded(block.id)} aria-expanded={expanded}>
                Evidence <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span>
                <span className="chat-collapse-chevron">{expanded ? '▴' : '▾'}</span>
              </button>
              {expanded && <>
                <div className="chat-evidence-symbols">
                  {(symbols.verified ?? []).map((s: string) => <span className="chat-evidence-symbol verified" key={`v-${s}`}>{s} ✓</span>)}
                  {(symbols.partial ?? []).map((s: string) => <span className="chat-evidence-symbol partial" key={`p-${s}`}>{s} ◐</span>)}
                  {(symbols.unavailable ?? []).map((s: string) => <span className="chat-evidence-symbol unavailable" key={`u-${s}`}>{s} ✗</span>)}
                </div>
                {items.length > 0 && <ul>{visibleItems.map((item: any, index: number) => (
                  <li key={`${item.tool ?? 'evidence'}-${index}`}>
                    {item.tool ?? 'Market data'}{item.provider ? ` · ${item.provider}` : ''}
                    {item.timeframe ? ` · ${item.timeframe}` : ''}{item.session ? ` · ${item.session}` : ''}
                    {item.freshness_status && item.freshness_status !== 'fresh' ? ` · ${item.freshness_status}` : ''}
                    {item.entitlement && item.entitlement !== 'not_applicable' ? ` · entitlement: ${item.entitlement}` : ''}
                  </li>
                ))}</ul>}
                {items.length > 8 && (
                  <button type="button" className="chat-quick-action-btn chat-expand-btn" onClick={() => toggleExpanded(showAllKey)}>
                    {showAll ? 'Show less' : `Show all ${items.length}`}
                  </button>
                )}
                {block.data.chart_state && (
                  <small className="chat-chart-state-note">
                    Chart context: {block.data.chart_state.symbol ?? 'symbol'} · {block.data.chart_state.timeframe ?? 'timeframe'} · {block.data.chart_state.session ?? 'session'}
                    {block.data.chart_state.selected_candle ? ' · selected candle included' : ''}
                  </small>
                )}
                {block.data.regeneration && (
                  <small className="chat-chart-state-note" role="status">
                    Regenerated: {String(block.data.regeneration.mode).replace(/_/g, ' ')} · {block.data.regeneration.reused_context ? 'eligible recent context reused' : 'fresh context requested'}
                  </small>
                )}
              </>}
            </section>
          );
        }
        if (block.type === 'warning') {
          const items = Array.isArray(block.data.items) ? block.data.items : [];
          return <div className="chat-typed-warning" key={block.id} role="status" aria-label="Answer warnings">
            <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span>
            <ul>{items.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul>
          </div>;
        }
        if (block.type === 'action_confirmation') {
          const actions = Array.isArray(block.data.actions) ? block.data.actions : [];
          return <div className="chat-typed-action" key={block.id} role="status" aria-label="Action status">
            {actions.map((action: any, index: number) => {
              const detailText = describeActionDetail(action.tool, action.detail);
              return <span key={`${action.tool ?? 'action'}-${index}`}>
                {action.status === 'completed' ? '✓' : action.status === 'failed' ? '⚠' : '•'} {action.tool ?? 'action'}{detailText ? `: ${detailText}` : ''} · {action.status ?? 'unknown'}
              </span>;
            })}
          </div>;
        }
        if (block.type === 'comparison_table') {
          const columns = Array.isArray(block.data.columns) ? block.data.columns : [];
          const rows = Array.isArray(block.data.rows) ? block.data.rows : [];
          return <section className="chat-typed-card" key={block.id} aria-label="Comparison table">
            <div className="chat-typed-card-heading">Comparison <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            <div className="chat-typed-table-wrap"><table><thead><tr>{columns.map((column: string) => <th key={column}>{column}</th>)}</tr></thead>
              <tbody>{rows.map((row: any[], index: number) => <tr key={index}>{(Array.isArray(row) ? row : columns.map(column => row?.[column])).map((cell: any, cellIndex: number) => <td key={cellIndex}>{String(cell ?? '—')}</td>)}</tr>)}</tbody>
            </table></div>
          </section>;
        }
        if (block.type === 'ranked_results') {
          const items = Array.isArray(block.data.items) ? block.data.items : [];
          return <section className="chat-typed-card" key={block.id} aria-label="Ranked results">
            <div className="chat-typed-card-heading">Ranked results <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            <ol>{items.slice(0, 20).map((item: any, index: number) => <li key={index}>{typeof item === 'object' ? `${item.symbol ?? item.name ?? 'Result'}${item.score != null ? ` · ${item.score}` : ''}` : String(item)}</li>)}</ol>
          </section>;
        }
        if (block.type === 'chart') {
          const bars = Array.isArray(block.data.bars) ? block.data.bars : [];
          const closes = bars.map((bar: any) => Number(bar.close)).filter(Number.isFinite);
          const min = Math.min(...closes);
          const max = Math.max(...closes);
          const spread = max - min || 1;
          const expanded = expandedBlocks.has(block.id);
          const chartHeight = expanded ? 70 : 40;
          const plotBottom = chartHeight - 4;
          const plotHeight = chartHeight - 8;
          const points = closes.map((close: number, index: number) => `${(index / Math.max(closes.length - 1, 1)) * 100},${plotBottom - ((close - min) / spread) * plotHeight}`).join(' ');
          return <section className="chat-typed-card chat-mini-chart" key={block.id} aria-label="Mini price chart">
            <div className="chat-typed-card-heading">Price chart <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            {closes.length > 1 ? (
              <div className={expanded ? 'chat-chart-expanded' : undefined}>
                <svg viewBox={`0 0 100 ${chartHeight}`} preserveAspectRatio="none" role="img" aria-label={`${block.data.symbol ?? 'Symbol'} price trend`}><polyline points={points} /></svg>
                {expanded && <div className="chat-chart-range"><span>High {max.toFixed(2)}</span><span>Low {min.toFixed(2)}</span></div>}
              </div>
            ) : <p className="info-text">Chart data is unavailable.</p>}
            <small>{block.data.symbol ?? 'Symbol'} · {block.data.timeframe ?? 'timeframe unavailable'} · {closes.length} bars</small>
            {closes.length > 1 && (
              <button type="button" className="chat-quick-action-btn chat-expand-btn" onClick={() => toggleExpanded(block.id)}>
                {expanded ? 'Collapse' : 'Expand'}
              </button>
            )}
          </section>;
        }
        if (block.type === 'indicator_table') {
          const indicators = block.data.indicators && typeof block.data.indicators === 'object' ? Object.entries(block.data.indicators) : [];
          return <section className="chat-typed-card" key={block.id} aria-label="Indicator table">
            <div className="chat-typed-card-heading">Indicators <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            <dl className="chat-indicator-grid">{indicators.map(([name, value]) => <div key={name}><dt>{name.replace(/_/g, ' ')}</dt><dd>{typeof value === 'number' ? value.toFixed(2) : String(value ?? '—')}</dd></div>)}</dl>
          </section>;
        }
        if (block.type === 'options_chain') {
          const chain = Array.isArray(block.data.chains) ? block.data.chains[0] : null;
          const allContracts = [...(chain?.calls ?? []), ...(chain?.puts ?? [])];
          const expanded = expandedBlocks.has(block.id);
          const contracts = expanded ? allContracts : allContracts.slice(0, 14);
          return <section className="chat-typed-card" key={block.id} aria-label="Options chain card">
            <div className="chat-typed-card-heading">Options snapshot <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            <div className="chat-options-summary">IV {block.data.iv ?? '—'} · IV rank {block.data.iv_rank ?? '—'}</div>
            {contracts.length > 0 ? <div className="chat-typed-table-wrap"><table><thead><tr><th>Type</th><th>Strike</th><th>Last</th><th>Volume</th><th>OI</th></tr></thead><tbody>{contracts.map((contract: any, index: number) => <tr key={`${contract.strike}-${contract.option_type}-${index}`}><td>{contract.option_type ?? (chain?.calls?.includes(contract) ? 'call' : 'put')}</td><td>{contract.strike ?? '—'}</td><td>{contract.last_price ?? contract.lastPrice ?? '—'}</td><td>{contract.volume ?? '—'}</td><td>{contract.open_interest ?? '—'}</td></tr>)}</tbody></table></div> : <p className="info-text">Options chain unavailable.</p>}
            {allContracts.length > 14 && (
              <button type="button" className="chat-quick-action-btn chat-expand-btn" onClick={() => toggleExpanded(block.id)}>
                {expanded ? 'Show less' : `Show all ${allContracts.length}`}
              </button>
            )}
          </section>;
        }
        if (block.type === 'scenario') {
          const positions = Array.isArray(block.data.positions) ? block.data.positions : [];
          const originalShock = Number(block.data.shock_percent ?? 0);
          const currentShock = scenarioShock[block.id] ?? originalShock;
          const isPreview = positions.length > 0 && currentShock !== originalShock;
          const preview = isPreview ? recomputeScenario(positions, currentShock) : null;
          const grossExposure = preview ? preview.grossExposure : Number(block.data.scenario_gross_exposure ?? 0);
          const totalPnlDelta = preview ? preview.totalPnlDelta : Number(block.data.total_pnl_delta ?? 0);
          const staticEntries = Object.entries(block.data).filter(([key, value]) =>
            !['unknowns', 'positions', 'conclusion', 'available', 'shock_percent', 'scenario_gross_exposure', 'total_pnl_delta'].includes(key)
            && value != null && typeof value !== 'object');
          return <section className="chat-typed-card" key={block.id} aria-label="Scenario analysis">
            <div className="chat-typed-card-heading">Scenario analysis <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            {positions.length > 0 && (
              <div className="chat-scenario-slider">
                <label htmlFor={`scenario-shock-${block.id}`}>Price shock: {currentShock.toFixed(1)}%</label>
                <input
                  id={`scenario-shock-${block.id}`}
                  type="range"
                  min={-50}
                  max={50}
                  step={0.5}
                  value={currentShock}
                  onChange={event => setScenarioShock(current => ({ ...current, [block.id]: Number(event.target.value) }))}
                />
              </div>
            )}
            <dl className="chat-indicator-grid">
              <div><dt>shock percent</dt><dd>{currentShock.toFixed(2)}</dd></div>
              <div><dt>gross exposure</dt><dd>{grossExposure.toFixed(2)}</dd></div>
              <div><dt>total pnl delta</dt><dd>{totalPnlDelta.toFixed(2)}</dd></div>
              {staticEntries.map(([name, value]) => <div key={name}><dt>{name.replace(/_/g, ' ')}</dt><dd>{typeof value === 'number' ? value.toFixed(2) : String(value)}</dd></div>)}
            </dl>
            {isPreview && (
              <p className="chat-scenario-preview-note">
                Local preview at {currentShock.toFixed(1)}% — not verified.{' '}
                <button type="button" className="chat-quick-action-btn" onClick={() => setScenarioShock(current => ({ ...current, [block.id]: originalShock }))}>Reset to verified</button>
              </p>
            )}
          </section>;
        }
        if (block.type === 'risk_card' || block.type === 'session_stats') {
          const entries = Object.entries(block.data).filter(([key, value]) => !['unknowns', 'positions', 'conclusion', 'available'].includes(key) && value != null && typeof value !== 'object');
          const title = block.type === 'risk_card' ? 'Risk snapshot' : 'Session statistics';
          return <section className="chat-typed-card" key={block.id} aria-label={title}>
            <div className="chat-typed-card-heading">{title} <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            <dl className="chat-indicator-grid">{entries.map(([name, value]) => <div key={name}><dt>{name.replace(/_/g, ' ')}</dt><dd>{typeof value === 'number' ? value.toFixed(2) : String(value)}</dd></div>)}</dl>
          </section>;
        }
        if (block.type === 'historical_outcomes') {
          const summaries = Array.isArray(block.data.summaries) ? block.data.summaries : [];
          return <section className="chat-typed-card" key={block.id} aria-label="Historical outcomes">
            <div className="chat-typed-card-heading">Historical outcomes <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            <div className="chat-typed-table-wrap"><table><thead><tr><th>Horizon</th><th>Sample</th><th>Mean return</th><th>Win rate</th></tr></thead><tbody>{summaries.map((summary: any) => <tr key={summary.horizon}><td>{summary.horizon} bars</td><td>{summary.sample_size ?? '—'}</td><td>{summary.mean_return_percent == null ? '—' : `${summary.mean_return_percent.toFixed(2)}%`}</td><td>{summary.win_rate_percent == null ? '—' : `${summary.win_rate_percent.toFixed(1)}%`}</td></tr>)}</tbody></table></div>
          </section>;
        }
        if (block.type === 'journal_save') {
          const entry = block.data.saved_entry ?? {};
          return <section className="chat-typed-card" key={block.id} aria-label="Journal save">
            <div className="chat-typed-card-heading">Journal saved <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            <p>{String(entry.symbol ?? 'Trade')} · {String(entry.status ?? 'planned')} · {entry.id ? 'local Journal updated' : 'entry recorded'}</p>
            {onNavigate && <button type="button" className="chat-quick-action-btn" onClick={() => onNavigate('journal', entry.symbol)}>Open Journal</button>}
          </section>;
        }
        if (block.type === 'report') {
          const title = String(block.data.title ?? 'MarketLens report');
          const content = String(block.data.content ?? '');
          const symbol = typeof block.data.symbol === 'string' ? block.data.symbol : undefined;
          const links = block.data.deep_links && typeof block.data.deep_links === 'object' ? block.data.deep_links : {};
          const download = () => {
            const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }));
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = `${title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'marketlens-report'}.md`;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(url);
          };
          const copy = () => {
            const result = navigator.clipboard?.writeText(content);
            if (result) void result.then(() => setCopiedReportId(block.id));
          };
          return <section className="chat-typed-card chat-report-card" key={block.id} aria-label="Local report">
            <div className="chat-typed-card-heading">{title} <span className={`chat-quality ${quality.state}`}>{qualityLabel}</span></div>
            <pre>{content}</pre>
            <div className="chat-report-actions">
              <button type="button" className="chat-quick-action-btn" onClick={download}>Download Markdown</button>
              <button type="button" className="chat-quick-action-btn" onClick={copy}>{copiedReportId === block.id ? 'Copied' : 'Copy report'}</button>
              {Object.entries(links).map(([key, route]) => {
                const target = REPORT_PAGE_TARGETS[key];
                if (!target || typeof route !== 'string') return null;
                const navigation = block.data.navigation && typeof block.data.navigation === 'object'
                  ? block.data.navigation as NavigationState
                  : undefined;
                return <button type="button" className="chat-quick-action-btn" key={key} onClick={() => onNavigate?.(target, ['symbol', 'options', 'health'].includes(key) ? symbol : undefined, navigation)}>{key === 'replay' ? 'Open Replay' : `Open ${key[0].toUpperCase()}${key.slice(1)}`}</button>;
              })}
            </div>
          </section>;
        }
        if (block.type === 'suggested_followups') {
          const items = Array.isArray(block.data.items) ? block.data.items : [];
          return <div className="chat-typed-followups" key={block.id} aria-label="Suggested follow-ups">
            <span>Suggested:</span>{items.map((item: string) => <span className="chat-followup-chip" key={item}>{item}</span>)}
          </div>;
        }
        return <div className="chat-typed-warning" key={block.id} role="status">
          Some structured answer details are unavailable in this client version.
        </div>;
      })}
    </div>
  );
}

function formatVerificationIssue(issue: string): string {
  const labels: Record<string, string> = {
    unknown_ticker: 'Unsupported ticker or market term',
    unsupported_numeric_claim: 'A number could not be matched to source data',
    unit_mismatch: 'The answer used a different unit than the source data',
    live_claim_without_freshness: 'Current-data freshness could not be verified',
    stale_live_claim: 'The available current-data source is stale',
    timeframe_mismatch: 'The answer used a different timeframe than the source',
    session_mismatch: 'The answer used a different market session than the source',
    contradictory_evidence: 'The answer conflicts with the source direction',
  };
  return labels[issue] ?? issue.replace(/_/g, ' ');
}

function AlertContextAttachment({ context }: { context: AlertConversationContext }) {
  const { alert, trigger, chart_state: chart, provenance, recent_triggers: recent, warnings } = context;
  const signalCount = chart.signals?.length ?? 0;
  const catalystCount = Array.isArray(context.symbol_context.news)
    ? context.symbol_context.news.length
    : 0;
  return (
    <div className="chat-alert-context" role="status" aria-label="Alert context attached">
      <div className="chat-alert-context-heading">
        <strong>🔔 Alert context attached</strong>
        <span className="label">{alert.name} · {trigger.symbol} · {trigger.triggered_at ? formatAlertTime(trigger.triggered_at) : 'time unavailable'}</span>
      </div>
      <div className="chat-alert-context-facts">
        <span><b>Trigger:</b> {trigger.message || trigger.observed_value || 'Condition matched'}</span>
        <span><b>Chart:</b> {chart.timeframe || '1d'} · {chart.session || 'session unknown'}{chart.last_price != null ? ` · $${chart.last_price.toFixed(2)}` : ''}</span>
        <span><b>Evidence:</b> {signalCount} signal{signalCount === 1 ? '' : 's'} · {catalystCount} catalyst item{catalystCount === 1 ? '' : 's'} · {recent.length} recent trigger{recent.length === 1 ? '' : 's'}</span>
      </div>
      <div className="chat-alert-context-provenance">
        {provenance.provider} · {provenance.data_status} · {provenance.as_of ? formatAlertTime(provenance.as_of) : 'timestamp unavailable'}
      </div>
      {warnings.length > 0 && <div className="chat-alert-context-warning">⚠ {warnings.join(' ')}</div>}
      <p className="chat-alert-context-help">Ask Chat to explain the move, review the signal, or propose a related alert. Changes still require confirmation.</p>
    </div>
  );
}

function formatAlertTime(value: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', dateStyle: 'medium', timeStyle: 'short',
    }).format(new Date(value));
  } catch {
    return value;
  }
}

/** Per-message row showing which tickers the answer was grounded in. */
const FEEDBACK_CATEGORIES: { value: NonNullable<ChatMessage['feedback']>['category']; label: string }[] = [
  { value: 'wrong_data', label: 'Wrong data' },
  { value: 'wrong_calculation', label: 'Wrong calculation' },
  { value: 'misunderstood_intent', label: 'Misunderstood intent' },
  { value: 'stale_data', label: 'Stale data' },
  { value: 'poor_explanation', label: 'Poor explanation' },
  { value: 'unsafe_action', label: 'Unsafe action' },
];

/** Correct / Incorrect / Not Useful feedback on one assistant message
 * (5.7.8). Correct submits immediately; Incorrect/Not Useful open an
 * optional category + comment before submitting — storage only, this
 * never triggers automatic model retraining. */
function ChatFeedbackRow({ message, onSaved }: { message: ChatMessage; onSaved: (messageId: number, feedback: NonNullable<ChatMessage['feedback']>) => void }) {
  const [expandedRating, setExpandedRating] = useState<'incorrect' | 'not_useful' | null>(null);
  const [category, setCategory] = useState<string>('');
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [fixtureSaved, setFixtureSaved] = useState(false);
  const existing = message.feedback ?? null;

  const submit = async (rating: 'correct' | 'incorrect' | 'not_useful', withDetail: boolean) => {
    setSaving(true);
    try {
      const updated = await api.setChatFeedback(
        message.id,
        rating,
        withDetail && category ? (category as any) : null,
        withDetail && comment.trim() ? comment.trim() : null,
      );
      if (updated.feedback) onSaved(message.id, updated.feedback);
      setExpandedRating(null);
    } catch {
      /* leave the row as-is; the trader can retry */
    } finally {
      setSaving(false);
    }
  };

  if (existing) {
    const label = existing.rating === 'correct' ? '✓ Marked correct' : existing.rating === 'incorrect' ? '✗ Marked incorrect' : '⊘ Marked not useful';
    return <div className="chat-feedback-row chat-feedback-done" role="status">
      <span>{label}{existing.category ? ` · ${existing.category.replace(/_/g, ' ')}` : ''}</span>
      {(existing.rating === 'incorrect' || existing.rating === 'not_useful') && !fixtureSaved && (
        <button type="button" className="chat-quick-action-btn" onClick={async () => {
          try {
            await api.createChatRegressionFixture(message.id);
            setFixtureSaved(true);
          } catch { /* fixture promotion is optional and can be retried */ }
        }}>Add regression fixture</button>
      )}
      {fixtureSaved && <span> · fixture approved</span>}
    </div>;
  }

  return (
    <div className="chat-feedback-row" aria-label="Rate this answer">
      <button type="button" className="chat-quick-action-btn" disabled={saving} onClick={() => submit('correct', false)}>
        👍 Correct
      </button>
      <button type="button" className="chat-quick-action-btn" disabled={saving} onClick={() => setExpandedRating(expandedRating === 'incorrect' ? null : 'incorrect')}>
        👎 Incorrect
      </button>
      <button type="button" className="chat-quick-action-btn" disabled={saving} onClick={() => setExpandedRating(expandedRating === 'not_useful' ? null : 'not_useful')}>
        🚫 Not useful
      </button>
      {expandedRating && (
        <form className="chat-feedback-detail" onSubmit={e => { e.preventDefault(); submit(expandedRating, true); }}>
          <select value={category} onChange={e => setCategory(e.target.value)} aria-label="Feedback category">
            <option value="">No category</option>
            {FEEDBACK_CATEGORIES.map(c => <option key={c.value} value={c.value!}>{c.label}</option>)}
          </select>
          <input
            type="text"
            value={comment}
            onChange={e => setComment(e.target.value)}
            placeholder="Optional comment"
            maxLength={1000}
            aria-label="Feedback comment"
          />
          <button type="submit" className="chat-quick-action-btn" disabled={saving}>
            {saving ? '⟳' : 'Submit'}
          </button>
        </form>
      )}
    </div>
  );
}

type RegenerationMode = ChatRegenerationMode;

function messageNeedsRefresh(message: ChatMessage): boolean {
  return (message.blocks ?? []).some(block => {
    if (block.quality?.state === 'stale' || block.quality?.freshness_status === 'stale') return true;
    const source = block.quality?.source_timestamp;
    if (!source) return false;
    const ageSeconds = (Date.now() - new Date(source).getTime()) / 1000;
    return Number.isFinite(ageSeconds) && ageSeconds > 900 && block.quality?.session !== 'closed';
  });
}

function ChatRegenerationRow({
  stale,
  currentChartState,
  preferredTimeframes,
  onRegenerate,
}: {
  stale: boolean;
  currentChartState: ChatChartState | null;
  preferredTimeframes: string[];
  onRegenerate: (mode: RegenerationMode, scope?: ChatRegenerationScope) => void;
}) {
  const [open, setOpen] = useState(false);
  const [timeframe, setTimeframe] = useState(currentChartState?.timeframe || preferredTimeframes[0] || '1d');
  const initialSession = ['premarket', 'regular', 'after_hours', 'all', 'auto'].includes(currentChartState?.session || '')
    ? currentChartState?.session as NonNullable<ChatRegenerationScope['session']>
    : 'all';
  const [session, setSession] = useState<NonNullable<ChatRegenerationScope['session']>>(initialSession);
  const options: Array<[RegenerationMode, string]> = [
    ['again', 'Run again'],
    ['more_detail', 'More detail'],
    ['simpler', 'Simpler'],
    ['bull_case', 'Bull case'],
    ['bear_case', 'Bear case'],
    ['calculations_only', 'Calculations only'],
    ['sources_only', 'Sources only'],
  ];
  return (
    <div className="chat-regeneration-row" aria-label="Regenerate answer">
      <button type="button" className="chat-quick-action-btn" onClick={() => onRegenerate('again')}>↻ Run again</button>
      <button type="button" className="chat-quick-action-btn" onClick={() => setOpen(value => !value)} aria-expanded={open}>More modes</button>
      {stale && <button type="button" className="chat-quick-action-btn chat-refresh-btn" onClick={() => onRegenerate('refresh')}>↻ Refresh current data</button>}
      {open && <span className="chat-regeneration-scope" aria-label="Regeneration scope">
        <label>Timeframe <select value={timeframe} onChange={event => setTimeframe(event.target.value)} aria-label="Regeneration timeframe">
          {Array.from(new Set(['1m', '5m', '15m', '1h', '4h', '1d', '1wk', ...preferredTimeframes])).map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>
        <label>Session <select value={session} onChange={event => setSession(event.target.value as NonNullable<ChatRegenerationScope['session']>)} aria-label="Regeneration session">
          {(['all', 'premarket', 'regular', 'after_hours', 'auto'] as const).map(value => <option key={value} value={value}>{value.replace('_', ' ')}</option>)}
        </select></label>
        <button type="button" className="chat-quick-action-btn" onClick={() => { setOpen(false); onRegenerate('rescope', { timeframe, session }); }}>Apply timeframe/session</button>
      </span>}
      {open && <span className="chat-regeneration-options">{options.slice(1).map(([mode, label]) => (
        <button key={mode} type="button" className="chat-quick-action-btn" onClick={() => { setOpen(false); onRegenerate(mode); }}>{label}</button>
      ))}</span>}
    </div>
  );
}

function ChatNotebookPanel({
  notebooks,
  newName,
  onNewNameChange,
  onCreate,
  onRename,
  onDelete,
  onDeleteItem,
}: {
  notebooks: ChatNotebook[];
  newName: string;
  onNewNameChange: (value: string) => void;
  onCreate: () => void;
  onRename: (notebookId: string, name: string) => Promise<void>;
  onDelete: (notebookId: string) => Promise<void>;
  onDeleteItem: (notebookId: string, itemId: string) => Promise<void>;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [pendingDelete, setPendingDelete] = useState<{ type: 'notebook' | 'item'; notebookId: string; itemId?: string; label: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const selected = notebooks.find(notebook => notebook.id === selectedId) ?? null;

  const selectNotebook = (notebook: ChatNotebook) => {
    setSelectedId(notebook.id);
    setEditingName(false);
    setNameDraft(notebook.name);
    setPendingDelete(null);
    setActionError(null);
  };

  const saveName = async () => {
    if (!selected || !nameDraft.trim()) return;
    try {
      await onRename(selected.id, nameDraft);
      setEditingName(false);
      setActionError(null);
    } catch {
      setActionError('Could not rename this notebook. Your saved answers were not changed.');
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    try {
      if (pendingDelete.type === 'notebook') {
        await onDelete(pendingDelete.notebookId);
        setSelectedId(null);
      } else if (pendingDelete.itemId) {
        await onDeleteItem(pendingDelete.notebookId, pendingDelete.itemId);
      }
      setPendingDelete(null);
      setActionError(null);
    } catch {
      setActionError(
        pendingDelete.type === 'notebook'
          ? 'Could not delete this notebook. It is still available.'
          : 'Could not remove this saved answer. It is still available.',
      );
    }
  };

  return (
    <div className="chat-notebook-panel" aria-label="Research notebooks">
      <div className="chat-notebook-create">
        <input value={newName} onChange={event => onNewNameChange(event.target.value)} placeholder="New notebook name" maxLength={120} aria-label="New notebook name" />
        <button type="button" className="chat-quick-action-btn" onClick={onCreate}>Create</button>
      </div>
      <small>Notebooks sync to the server for this browser key and preserve original evidence timestamps.</small>
      {notebooks.length > 0 && <div className="chat-notebook-list">{notebooks.map(notebook => (
        <button key={notebook.id} type="button" className="chat-notebook-chip" onClick={() => selectNotebook(notebook)}>
          {notebook.name} · {notebook.items.length} saved
        </button>
      ))}</div>}
      {selected && (
        <>
          <div className="chat-notebook-heading">
            {editingName ? (
              <>
                <input aria-label="Notebook name" value={nameDraft} onChange={event => setNameDraft(event.target.value)} maxLength={120} />
                <button type="button" className="chat-quick-action-btn" onClick={saveName}>Save name</button>
                <button type="button" className="chat-quick-action-btn" onClick={() => { setEditingName(false); setNameDraft(selected.name); }}>Cancel</button>
              </>
            ) : (
              <>
                <strong>{selected.name}</strong>
                <button type="button" className="chat-quick-action-btn" onClick={() => { setNameDraft(selected.name); setEditingName(true); }}>Edit name</button>
                <button type="button" className="chat-quick-action-btn chat-notebook-danger" onClick={() => setPendingDelete({ type: 'notebook', notebookId: selected.id, label: selected.name })}>Delete notebook</button>
              </>
            )}
          </div>
          {actionError && <small className="chat-notebook-error" role="status">{actionError}</small>}
          {pendingDelete && (
            <div className="chat-notebook-confirm" role="alert">
              <span>{pendingDelete.type === 'notebook'
                ? `Delete “${pendingDelete.label}” and all of its saved answers?`
                : 'Remove this saved answer? The original Chat message will remain.'}</span>
              <button type="button" className="chat-quick-action-btn" onClick={() => setPendingDelete(null)}>Cancel</button>
              <button type="button" className="chat-quick-action-btn chat-notebook-danger" onClick={confirmDelete}>
                {pendingDelete.type === 'notebook' ? 'Delete notebook' : 'Remove answer'}
              </button>
            </div>
          )}
          <div className="chat-notebook-items" aria-label={`${selected.name} saved answers`}>
            {selected.items.length === 0 ? <small>No answers saved yet.</small> : selected.items.slice(0, 10).map(item => (
              <article key={item.id} className="chat-notebook-item">
                <div className="chat-notebook-item-heading">
                  <strong>{item.question || 'Saved answer'}</strong>
                  <button
                    type="button"
                    className="chat-quick-action-btn chat-notebook-danger"
                    onClick={() => setPendingDelete({ type: 'item', notebookId: selected.id, itemId: item.id, label: item.question || 'Saved answer' })}
                    aria-label={`Remove saved answer: ${item.question || 'Saved answer'}`}
                  >
                    Remove
                  </button>
                </div>
                <span>{item.answer.slice(0, 220)}{item.answer.length > 220 ? '…' : ''}</span>
                <small>
                  Saved {formatAlertTime(item.saved_at)}
                  {item.symbols?.length ? ` · ${item.symbols.join(', ')}` : ''}
                  {item.content_types?.length ? ` · ${item.content_types.join(', ')}` : ''}
                  {item.evidence_timestamps.length ? ` · evidence ${formatAlertTime(item.evidence_timestamps[0])}` : ''}
                  {item.stale ? ' · stale inputs' : ''}
                </small>
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function NotebookSaveButton({
  message,
  question,
  notebooks,
  onSaved,
}: {
  message: ChatMessage;
  question: string;
  notebooks: ChatNotebook[];
  onSaved: (notebooks: ChatNotebook[], notebookId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!notebooks.length) return null;
  return (
    <div className="chat-notebook-save-row">
      <button type="button" className="chat-quick-action-btn" onClick={() => setOpen(value => !value)} aria-expanded={open}>📓 Save to notebook</button>
      {open && <span className="chat-notebook-options">{notebooks.map(notebook => (
        <button key={notebook.id} type="button" className="chat-quick-action-btn" onClick={() => { onSaved(saveMessageToChatNotebook(notebook.id, message, question), notebook.id); setOpen(false); }}>
          {notebook.name}
        </button>
      ))}</span>}
    </div>
  );
}

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

function ToolTraceRow({ message }: { message: ChatMessage }) {
  const tools = message.tools ?? [];
  if (!tools.length) return null;
  return (
    <span className="chat-tool-trace" title="Tools used to ground this answer">
      {tools.map((item, index) => {
        if (item.kind === 'model') {
          return <span className="chat-tool-pill model-route" key={`model-${index}`}>
            ◇ {item.role ?? 'model'} · {item.model ?? 'unknown'}
          </span>;
        }
        if (item.kind === 'step') {
          const status = item.status ?? 'completed';
          const icon = status === 'completed' ? '✓' : status === 'reused' ? '↻' : status === 'failed' ? '⚠' : '•';
          return <span className={`chat-tool-pill step-${status}`} key={`step-${item.step ?? index}`} title={item.reason ?? ''}>
            {icon} Step {item.step ?? index + 1}: {item.tool} · {status}
            </span>;
        }
        if (item.kind === 'observability') {
          return <span className="chat-tool-pill model-route" key={`observability-${index}`}>
            ◌ Audit · {item.turn_duration_ms != null ? `${Math.round(item.turn_duration_ms)}ms` : 'measured'} · {item.within_target ? 'within target' : 'over target'}
          </span>;
        }
        const freshness = item.freshness_seconds == null ? '' : ` · ${Math.round(item.freshness_seconds)}s old`;
        const fallback = item.fallback ? ' · fallback' : '';
        return <span className={`chat-tool-pill ${item.ok ? 'ok' : 'error'}`} key={`${item.tool ?? 'tool'}-${index}`}>
          {item.ok ? '✓' : '⚠'} {item.tool ?? 'tool'}{item.provider ? ` · ${item.provider}` : ''}{freshness}{fallback}
        </span>;
      })}
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
function ChatQuickActions({ symbols, watchlistIndex, onWatchlisted, onWatchlistCreated, onNavigate }: {
  symbols: string[];
  watchlistIndex: WatchlistIndex | null;
  onWatchlisted: (symbol: string, watchlistId: number) => void;
  onWatchlistCreated: (wl: WatchlistOption) => void;
  onNavigate?: (page: AppPage, symbol?: string, navigation?: NavigationState) => void;
}) {
  if (symbols.length === 0 && !onNavigate) return null;
  return (
    <div className="chat-quick-actions">
      {onNavigate && <button type="button" className="chat-quick-action-btn" onClick={() => onNavigate('scanner')}>Open Scanner</button>}
      {symbols.map(sym => (
        <TickerQuickActions
          key={sym}
          symbol={sym}
          watchlistIndex={watchlistIndex}
          onWatchlisted={onWatchlisted}
          onWatchlistCreated={onWatchlistCreated}
          onNavigate={onNavigate}
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

function TickerQuickActions({ symbol, watchlistIndex, onWatchlisted, onWatchlistCreated, onNavigate }: {
  symbol: string;
  watchlistIndex: WatchlistIndex | null;
  onWatchlisted: (symbol: string, watchlistId: number) => void;
  onWatchlistCreated: (wl: WatchlistOption) => void;
  onNavigate?: (page: AppPage, symbol?: string, navigation?: NavigationState) => void;
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
  const lists = useMemo(() => watchlistIndex?.lists ?? [], [watchlistIndex?.lists]);

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

  const saveJournalDraft = useCallback(() => {
    try {
      window.localStorage.setItem('marketlens.trade.journal.pending', JSON.stringify({
        symbol,
        thesis: `Research context from AI Chat for ${symbol}.`,
        createdAt: new Date().toISOString(),
      }));
    } catch { /* navigation remains useful when storage is unavailable */ }
    onNavigate?.('journal', symbol);
  }, [symbol, onNavigate]);

  return (
    <div className="chat-quick-action">
      <span className="chat-quick-action-symbol">{symbol}</span>
      {onNavigate && <button type="button" className="chat-quick-action-btn" onClick={() => onNavigate('symbol', symbol)}>Open Symbol</button>}

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
      {onNavigate && <button type="button" className="chat-quick-action-btn" onClick={saveJournalDraft}>Save to Journal</button>}
    </div>
  );
}

export default ChatPanel;
