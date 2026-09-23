import { createChatNotebook, loadChatNotebooks, saveMessageToChatNotebook } from './chatNotebooks';

beforeEach(() => window.localStorage.clear());

test('creates a local notebook and preserves answer evidence timestamps', () => {
  const notebook = createChatNotebook('AAPL research');
  const updated = saveMessageToChatNotebook(notebook.id, {
    id: 7, session_id: 1, role: 'assistant', content: 'Verified answer', created_at: '', grounded: true,
    blocks: [{ id: 'evidence-1', type: 'evidence', data: {}, quality: { state: 'verified', grounded: true, source_timestamp: '2026-09-23T12:00:00Z', confidence: 1 } }],
  }, 'How is AAPL?');
  expect(updated[0].items[0].question).toBe('How is AAPL?');
  expect(updated[0].items[0].evidence_timestamps).toEqual(['2026-09-23T12:00:00Z']);
  expect(loadChatNotebooks()[0].name).toBe('AAPL research');
});
