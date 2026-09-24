import type { ChatMessage, ChatNotebookResponse } from '../services/api';

export interface NotebookItem {
  id: string;
  message_id: number;
  question: string;
  answer: string;
  blocks: ChatMessage['blocks'];
  symbols: string[];
  content_types: string[];
  stale: boolean;
  evidence_timestamps: string[];
  saved_at: string;
}

export interface ChatNotebook {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  items: NotebookItem[];
}

const KEY = 'marketlens.chat.notebooks';
const CLIENT_KEY = 'marketlens.chat.client_key';

function read(): ChatNotebook[] {
  if (typeof window === 'undefined') return [];
  try {
    const value = JSON.parse(window.localStorage.getItem(KEY) || '[]');
    return Array.isArray(value) ? value.filter(item => item && typeof item.id === 'string') : [];
  } catch {
    return [];
  }
}

function write(notebooks: ChatNotebook[]): void {
  try { window.localStorage.setItem(KEY, JSON.stringify(notebooks.slice(0, 20))); } catch { /* optional storage */ }
}

export function loadChatNotebooks(): ChatNotebook[] { return read(); }

export function getChatNotebookClientKey(): string {
  if (typeof window === 'undefined') return 'server-rendered-client';
  const existing = window.localStorage.getItem(CLIENT_KEY);
  if (existing) return existing;
  const generated = `ml-client-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  try { window.localStorage.setItem(CLIENT_KEY, generated); } catch { /* optional storage */ }
  return generated;
}

export function mergeServerChatNotebooks(serverNotebooks: ChatNotebookResponse[]): ChatNotebook[] {
  const mapped = serverNotebooks.map(notebook => ({
    id: `server-${notebook.id}`,
    name: notebook.name,
    created_at: notebook.created_at,
    updated_at: notebook.updated_at,
    items: notebook.items.map(item => ({
      id: `server-item-${item.id}`,
      message_id: item.message_id,
      question: item.question,
      answer: item.answer,
      blocks: item.blocks ?? [],
      symbols: item.symbols ?? [],
      content_types: item.content_types ?? [],
      stale: item.stale || item.material_change_detected,
      evidence_timestamps: item.evidence_timestamps ?? [],
      saved_at: item.created_at,
    })),
  }));
  // Keep what exists only in this browser: notebooks created while the
  // server was unreachable, and items whose server save failed. The server
  // copy wins for anything it already has.
  const local = read();
  const merged = mapped.map(notebook => {
    const localCopy = local.find(candidate => candidate.id === notebook.id);
    if (!localCopy) return notebook;
    const serverMessageIds = new Set(notebook.items.map(item => item.message_id));
    const localOnlyItems = localCopy.items.filter(item => !serverMessageIds.has(item.message_id));
    return localOnlyItems.length ? { ...notebook, items: [...localOnlyItems, ...notebook.items].slice(0, 200) } : notebook;
  });
  const localOnlyNotebooks = local.filter(notebook => !notebook.id.startsWith('server-'));
  const result = [...merged, ...localOnlyNotebooks];
  write(result);
  return result;
}

export function createChatNotebook(name: string): ChatNotebook {
  const now = new Date().toISOString();
  const notebook: ChatNotebook = {
    id: `notebook-${Date.now()}`,
    name: name.trim().slice(0, 120) || 'Market research',
    created_at: now,
    updated_at: now,
    items: [],
  };
  write([notebook, ...read()]);
  return notebook;
}

export function renameChatNotebook(notebookId: string, name: string): ChatNotebook[] {
  const normalizedName = name.trim().slice(0, 120) || 'Market research';
  const now = new Date().toISOString();
  const updated = read().map(notebook => notebook.id === notebookId
    ? { ...notebook, name: normalizedName, updated_at: now }
    : notebook);
  write(updated);
  return updated;
}

export function removeChatNotebook(notebookId: string): ChatNotebook[] {
  const updated = read().filter(notebook => notebook.id !== notebookId);
  write(updated);
  return updated;
}

export function removeChatNotebookItem(notebookId: string, itemId: string): ChatNotebook[] {
  const updated = read().map(notebook => notebook.id === notebookId
    ? {
      ...notebook,
      updated_at: new Date().toISOString(),
      items: notebook.items.filter(item => item.id !== itemId),
    }
    : notebook);
  write(updated);
  return updated;
}

export function saveMessageToChatNotebook(notebookId: string, message: ChatMessage, question: string): ChatNotebook[] {
  const notebooks = read();
  const now = new Date().toISOString();
  const sourceTimestamps = (message.blocks ?? [])
    .map(block => block.quality?.source_timestamp)
    .filter((value): value is string => Boolean(value));
  const symbols = new Set<string>([...(message.focus || []), ...(message.partial || [])]);
  const contentTypes = new Set<string>();
  let stale = false;
  for (const block of message.blocks ?? []) {
    contentTypes.add(block.type);
    const blockSymbol = block.data?.symbol || block.data?.chart_state?.symbol;
    if (typeof blockSymbol === 'string' && blockSymbol.trim()) symbols.add(blockSymbol.trim().toUpperCase());
    if (block.quality?.state === 'stale') stale = true;
    const timestamp = block.quality?.source_timestamp;
    if (timestamp && Date.now() - Date.parse(timestamp) > 900_000) stale = true;
  }
  const item: NotebookItem = {
    id: `notebook-item-${message.id}-${Date.now()}`,
    message_id: message.id,
    question: question.slice(0, 2000),
    answer: message.content,
    blocks: message.blocks ?? [],
    symbols: Array.from(symbols).slice(0, 20),
    content_types: Array.from(contentTypes).slice(0, 20),
    stale,
    evidence_timestamps: Array.from(new Set(sourceTimestamps)),
    saved_at: now,
  };
  const updated = notebooks.map(notebook => notebook.id === notebookId
    ? { ...notebook, updated_at: now, items: [item, ...notebook.items.filter(existing => existing.message_id !== message.id)].slice(0, 200) }
    : notebook);
  write(updated);
  return updated;
}
