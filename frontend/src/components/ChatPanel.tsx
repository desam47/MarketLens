/**
 * ChatPanel — Version 4 AI feature 4: conversational AI chat panel.
 *
 * Net new UI — no chat/message-list precedent exists anywhere else in
 * this app. Scope (see docs/plan): single-turn-per-message (the
 * backend rebuilds context fresh per message, no server-side
 * "memory" beyond the stored transcript text), no streaming, no
 * tool-calling — the AI can read what already exists (the latest
 * analysis, an alert trigger's own facts) but never triggers new
 * work on the user's behalf.
 *
 * One open session per symbol (optionally scoped to a specific alert
 * trigger via `alertTriggerId`, if a caller ever opens this from an
 * alert row) — mirrors AlertsCard's optimistic-then-reconcile shape
 * for sending a message: append the user's bubble immediately, then
 * replace/append with the real response (or roll back + show an
 * error on failure).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import api, { ChatMessage } from '../services/api';

interface ChatPanelProps {
  symbol: string;
  alertTriggerId?: number | null;
}

export function ChatPanel({ symbol, alertTriggerId = null }: ChatPanelProps) {
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setMessages([]);
    setSessionId(null);

    (async () => {
      try {
        const session = await api.createChatSession(symbol, alertTriggerId);
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
  }, [symbol, alertTriggerId]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);

  const handleSend = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    const content = input.trim();
    if (!content || !sessionId || sending) return;

    setSending(true);
    setError(null);
    const optimisticUser: ChatMessage = {
      id: -Date.now(), // negative placeholder id, replaced on reconcile
      session_id: sessionId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
      grounded: null,
    };
    setMessages(prev => [...prev, optimisticUser]);
    setInput('');

    try {
      const assistantReply = await api.sendChatMessage(sessionId, content);
      setMessages(prev => [...prev, assistantReply]);
    } catch (e: any) {
      setError(e?.message || 'Failed to send message');
      // Roll back the optimistic bubble — the message never made it.
      setMessages(prev => prev.filter(m => m.id !== optimisticUser.id));
      setInput(content);
    } finally {
      setSending(false);
    }
  }, [input, sessionId, sending]);

  return (
    <div className="card chat-panel-card">
      <div className="chat-panel-header">
        <h2>💬 Ask about {symbol}</h2>
        <p className="info-text">Grounded in the current quant context — not a trade advisor.</p>
      </div>

      {error && (
        <div className="chat-panel-error">⚠️ {error}</div>
      )}

      <div className="chat-message-list" ref={listRef}>
        {loading && <p className="info-text">Opening chat…</p>}
        {!loading && messages.length === 0 && (
          <p className="empty-state">Ask a question about {symbol} to get started.</p>
        )}
        {messages.map(m => (
          <div key={m.id} className={`chat-bubble-row ${m.role}`}>
            <div className={`chat-bubble ${m.role}`}>
              {m.content}
              {m.role === 'assistant' && m.grounded === false && (
                <span className="chat-ungrounded-tag">not fully grounded</span>
              )}
            </div>
          </div>
        ))}
        {sending && (
          <div className="chat-bubble-row assistant">
            <div className="chat-bubble assistant chat-bubble-pending">…</div>
          </div>
        )}
      </div>

      <form className="chat-input-row" onSubmit={handleSend}>
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder={`Ask about ${symbol}…`}
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

export default ChatPanel;
