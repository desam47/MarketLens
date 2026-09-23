import { collectChatBrowserData, isChatBrowserDataRelevant, loadChatSharing, saveChatSharing, DEFAULT_CHAT_SHARING, isSharingAnything } from './chatBrowserData';

const POSITION = { id: 'p1', symbol: 'aapl', side: 'long', quantity: 100, entryPrice: 200, stopPrice: 190, sector: 'Technology' };
const JOURNAL_ENTRY = {
  id: 'j1', symbol: 'msft', side: 'long', status: 'closed', quantity: 10,
  entryPrice: 400, exitPrice: 420, stopPrice: 390, targetPrice: 430,
  entryDate: '2026-09-01', exitDate: '2026-09-10', setup: 'breakout',
  thesis: 'SECRET THESIS', reviewNotes: 'SECRET REVIEW', screenshotDataUrl: 'data:image/png;base64,AAAA',
};
const PRESET = { name: 'Breakout', filters: [{ type: 'rsi', params: { op: '<', value: 30 } }], match: 'AND' };

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem('marketlens.risk.positions', JSON.stringify([POSITION]));
  window.localStorage.setItem('marketlens.trade.journal', JSON.stringify([JOURNAL_ENTRY]));
  window.localStorage.setItem('marketlens.scanner.presets', JSON.stringify([PRESET]));
});

test('sharing is off by default and sends nothing', () => {
  expect(loadChatSharing()).toEqual(DEFAULT_CHAT_SHARING);
  expect(isSharingAnything(DEFAULT_CHAT_SHARING)).toBe(false);
  expect(collectChatBrowserData(DEFAULT_CHAT_SHARING, "What's the market doing today?")).toBeNull();
});

test('only switched-on categories are collected', () => {
  const payload = collectChatBrowserData({ ...DEFAULT_CHAT_SHARING, positions: true }, 'What is my portfolio risk?');
  expect(Object.keys(payload!)).toEqual(['positions']);
  expect(payload!.positions).toEqual([
    { symbol: 'AAPL', side: 'long', quantity: 100, entry_price: 200, stop_price: 190, sector: 'Technology' },
  ]);
});

test('persistent sharing does not send browser data for unrelated questions', () => {
  const settings = { positions: true, journal: true, scan_presets: true };
  expect(isChatBrowserDataRelevant("What's the market doing today?", settings)).toBe(false);
  expect(isChatBrowserDataRelevant('What is my portfolio risk?', settings)).toBe(true);
  expect(isChatBrowserDataRelevant('Review my trade journal', settings)).toBe(true);
  expect(isChatBrowserDataRelevant('Run my Breakout preset', settings)).toBe(true);
});

test('only the relevant enabled category is collected', () => {
  const settings = { positions: true, journal: true, scan_presets: true };
  const payload = collectChatBrowserData(settings, 'Review my trade journal');
  expect(Object.keys(payload!)).toEqual(['journal_entries']);
  expect(payload!.journal_entries?.[0].symbol).toBe('MSFT');
});

test('journal free text and screenshots never leave the browser', () => {
  const payload = collectChatBrowserData({ ...DEFAULT_CHAT_SHARING, journal: true }, 'Review my trade journal');
  const entry = payload!.journal_entries![0];
  expect(entry).toEqual({
    symbol: 'MSFT', side: 'long', status: 'closed', quantity: 10, entry_price: 400, exit_price: 420,
    stop_price: 390, target_price: 430, entry_date: '2026-09-01', exit_date: '2026-09-10', setup: 'breakout',
  });
  const serialized = JSON.stringify(payload);
  expect(serialized).not.toContain('SECRET THESIS');
  expect(serialized).not.toContain('SECRET REVIEW');
  expect(serialized).not.toContain('data:image');
});

test('scan presets are collected with their filters', () => {
  const payload = collectChatBrowserData({ ...DEFAULT_CHAT_SHARING, scan_presets: true }, 'Run my Breakout preset');
  expect(payload!.scan_presets).toEqual([{ name: 'Breakout', filters: [{ type: 'rsi', params: { op: '<', value: 30 } }], match: 'AND' }]);
});

test('scanner preset sharing drops non-primitive filter payloads', () => {
  window.localStorage.setItem('marketlens.scanner.presets', JSON.stringify([{
    name: 'Safe', filters: [{ type: 'rsi_oversold', params: { threshold: 30, secret: { prompt: 'do not send' } } }], match: 'AND',
  }]));
  expect(collectChatBrowserData({ ...DEFAULT_CHAT_SHARING, scan_presets: true }, 'Run my Safe preset')!.scan_presets).toEqual([
    { name: 'Safe', filters: [{ type: 'rsi_oversold', params: { threshold: 30 } }], match: 'AND' },
  ]);
});

test('incomplete positions are dropped rather than sent half-filled', () => {
  window.localStorage.setItem('marketlens.risk.positions', JSON.stringify([
    POSITION, { id: 'p2', symbol: 'TSLA', side: 'long', quantity: 0, entryPrice: null },
  ]));
  const payload = collectChatBrowserData({ ...DEFAULT_CHAT_SHARING, positions: true }, 'What is my portfolio risk?');
  expect(payload!.positions!.map(row => row.symbol)).toEqual(['AAPL']);
});

test('an empty category is omitted so the backend keeps its honest unavailable answer', () => {
  window.localStorage.setItem('marketlens.risk.positions', '[]');
  expect(collectChatBrowserData({ ...DEFAULT_CHAT_SHARING, positions: true }, 'What is my portfolio risk?')).toBeNull();
});

test('settings round-trip and corrupt storage falls back to off', () => {
  saveChatSharing({ positions: true, journal: false, scan_presets: true });
  expect(loadChatSharing()).toEqual({ positions: true, journal: false, scan_presets: true });
  window.localStorage.setItem('marketlens.chat.sharing', 'not json');
  expect(loadChatSharing()).toEqual(DEFAULT_CHAT_SHARING);
});
