import {
  createChatNotebook,
  loadChatNotebooks,
  mergeServerChatNotebooks,
  removeChatNotebook,
  removeChatNotebookItem,
  renameChatNotebook,
  saveMessageToChatNotebook,
} from './chatNotebooks';

beforeEach(() => window.localStorage.clear());

test('creates a local notebook and preserves answer evidence timestamps', () => {
  const notebook = createChatNotebook('AAPL research');
  const updated = saveMessageToChatNotebook(notebook.id, {
    id: 7, session_id: 1, role: 'assistant', content: 'Verified answer', created_at: '', grounded: true, focus: ['AAPL'], partial: [], unavailable: [],
    blocks: [{ id: 'evidence-1', type: 'evidence', data: {}, quality: { state: 'verified', grounded: true, source_timestamp: '2099-09-23T12:00:00Z', confidence: 1 } }],
  }, 'How is AAPL?');
  expect(updated[0].items[0].question).toBe('How is AAPL?');
  expect(updated[0].items[0].symbols).toEqual(['AAPL']);
  expect(updated[0].items[0].content_types).toEqual(['evidence']);
  expect(updated[0].items[0].stale).toBe(false);
  expect(updated[0].items[0].evidence_timestamps).toEqual(['2099-09-23T12:00:00Z']);
  expect(loadChatNotebooks()[0].name).toBe('AAPL research');
});

test('server merge keeps notebooks and items that exist only in this browser', () => {
  const offline = createChatNotebook('Offline idea');
  const message = { id: 9, session_id: 1, role: 'assistant' as const, content: 'Local answer', created_at: '', grounded: true, focus: [], partial: [], unavailable: [], blocks: [] };
  saveMessageToChatNotebook(offline.id, message, 'Q');
  window.localStorage.setItem('marketlens.chat.notebooks', JSON.stringify([
    ...loadChatNotebooks(),
    { id: 'server-1', name: 'Synced', created_at: '', updated_at: '', items: [{ ...loadChatNotebooks()[0].items[0], id: 'local-unsynced', message_id: 11 }] },
  ]));

  const merged = mergeServerChatNotebooks([
    { id: 1, name: 'Synced', created_at: '', updated_at: '', items: [] } as any,
  ]);

  expect(merged.map(notebook => notebook.id)).toEqual(['server-1', offline.id]);
  expect(merged[0].items.map(item => item.message_id)).toEqual([11]);
  expect(merged[1].items[0].answer).toBe('Local answer');
  expect(loadChatNotebooks()).toHaveLength(2);
});

test('renames notebooks and removes only the requested saved answer or notebook', () => {
  const notebook = createChatNotebook('Trade ideas');
  const message = { id: 10, session_id: 1, role: 'assistant' as const, content: 'Local answer', created_at: '', grounded: true, focus: [], partial: [], unavailable: [], blocks: [] };
  const updated = saveMessageToChatNotebook(notebook.id, message, 'Q');
  const itemId = updated[0].items[0].id;

  expect(renameChatNotebook(notebook.id, 'Renamed ideas')[0].name).toBe('Renamed ideas');
  expect(removeChatNotebookItem(notebook.id, itemId)[0].items).toEqual([]);
  expect(removeChatNotebook(notebook.id)).toEqual([]);
});
