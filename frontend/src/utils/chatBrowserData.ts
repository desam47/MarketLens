/**
 * Opt-in sharing of browser-local data with Chat (Version 5).
 *
 * Risk Dashboard positions, Trade Journal entries, and Scanner presets are
 * stored only in this browser, so Chat's server-side tools cannot see them
 * and answer "unavailable" instead. These switches let the trader share a
 * bounded, structured snapshot per turn.
 *
 * Every switch is off by default and stored separately from
 * `chatPreferences` (which only tailors wording): sharing sends real data
 * to the backend and, through the prompt's tool evidence, to the configured
 * AI provider, so it is never implied by an unrelated preference.
 *
 * Free text never leaves the browser. Journal thesis, review notes, and
 * screenshots are dropped here, and the backend's BrowserJournalEntry drops
 * them again if any client sends them.
 */
import { ChatBrowserData } from '../services/api';

const STORAGE_KEY = 'marketlens.chat.sharing';
const RISK_POSITIONS_KEY = 'marketlens.risk.positions';
const JOURNAL_KEY = 'marketlens.trade.journal';
const SCANNER_PRESETS_KEY = 'marketlens.scanner.presets';

const MAX_POSITIONS = 500;
const MAX_JOURNAL_ENTRIES = 200;
const MAX_PRESETS = 50;

export interface ChatSharingSettings {
  positions: boolean;
  journal: boolean;
  scan_presets: boolean;
}

export const DEFAULT_CHAT_SHARING: ChatSharingSettings = {
  positions: false,
  journal: false,
  scan_presets: false,
};

export const SHARING_FIELDS: Array<{
  key: keyof ChatSharingSettings;
  label: string;
  description: string;
}> = [
  {
    key: 'positions',
    label: 'Risk Dashboard positions',
    description: 'Symbol, side, quantity, entry, stop, sector — for portfolio risk and what-if questions.',
  },
  {
    key: 'journal',
    label: 'Trade Journal entries',
    description: 'Dates, prices, quantity, setup tag. Your thesis, review notes, and screenshots are never sent.',
  },
  {
    key: 'scan_presets',
    label: 'Saved Scanner presets',
    description: 'Preset names and filters, so Chat can describe or reuse a saved scan.',
  },
];

const SHARING_INTENTS: Record<keyof ChatSharingSettings, RegExp> = {
  positions: /\b(?:portfolio|position(?:s)?|book|exposure|drawdown|concentration|risk dashboard|portfolio health|what[- ]if|scenario)\b/i,
  journal: /\b(?:trade journal|trading journal|journal entries?|journal|trade history|trading mistakes?|journal coach)\b/i,
  scan_presets: /\b(?:saved scans?|saved scanner presets?|saved presets?|scanner presets?|my presets?|use .*preset|run .*preset)\b/i,
};

export function loadChatSharing(): ChatSharingSettings {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || 'null');
    if (!parsed || typeof parsed !== 'object') return { ...DEFAULT_CHAT_SHARING };
    return {
      positions: parsed.positions === true,
      journal: parsed.journal === true,
      scan_presets: parsed.scan_presets === true,
    };
  } catch {
    return { ...DEFAULT_CHAT_SHARING };
  }
}

export function saveChatSharing(settings: ChatSharingSettings): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    /* best effort — private browsing / storage full / disabled */
  }
}

export function isSharingAnything(settings: ChatSharingSettings): boolean {
  return settings.positions || settings.journal || settings.scan_presets;
}

function relevantSharingKeys(content: string, settings: ChatSharingSettings): Array<keyof ChatSharingSettings> {
  return (Object.keys(SHARING_INTENTS) as Array<keyof ChatSharingSettings>)
    .filter(key => settings[key] && SHARING_INTENTS[key].test(content));
}

/**
 * Keep persistent sharing switches from sending unrelated browser data.
 * This is deliberately conservative: a false negative asks the trader to
 * make the request more explicit, while a false positive would disclose a
 * local snapshot on an unrelated turn.
 */
export function isChatBrowserDataRelevant(content: string, settings: ChatSharingSettings): boolean {
  return relevantSharingKeys(content, settings).length > 0;
}

function readArray(key: string): unknown[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function positiveNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined;
}

function primitive(value: unknown): string | number | boolean | null | undefined {
  if (value == null || typeof value === 'string' || typeof value === 'boolean') return value ?? null;
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

/** Drops keys whose value is undefined so the payload carries only real values. */
function compact<T extends object>(value: T): T {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined)) as T;
}

function mapPositions(): ChatBrowserData['positions'] {
  return readArray(RISK_POSITIONS_KEY)
    .filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object')
    .map(row => compact({
      symbol: String(row.symbol ?? '').toUpperCase(),
      side: row.side === 'short' ? ('short' as const) : ('long' as const),
      quantity: positiveNumber(row.quantity),
      entry_price: positiveNumber(row.entryPrice),
      current_price: positiveNumber(row.currentPrice),
      stop_price: positiveNumber(row.stopPrice),
      sector: typeof row.sector === 'string' && row.sector ? row.sector : undefined,
    }))
    // The backend requires a symbol, quantity, and entry price; an
    // incomplete row is dropped here rather than rejected server-side.
    .filter(row => row.symbol && row.quantity !== undefined && row.entry_price !== undefined)
    .slice(0, MAX_POSITIONS) as ChatBrowserData['positions'];
}

function mapJournalEntries(): ChatBrowserData['journal_entries'] {
  return readArray(JOURNAL_KEY)
    .filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object')
    .map(row => compact({
      symbol: String(row.symbol ?? '').toUpperCase(),
      side: row.side === 'short' ? ('short' as const) : ('long' as const),
      status: row.status === 'open' || row.status === 'closed' ? row.status : ('planned' as const),
      quantity: positiveNumber(row.quantity),
      entry_price: positiveNumber(row.entryPrice),
      exit_price: positiveNumber(row.exitPrice),
      stop_price: positiveNumber(row.stopPrice),
      target_price: positiveNumber(row.targetPrice),
      entry_date: typeof row.entryDate === 'string' ? row.entryDate.slice(0, 40) : undefined,
      exit_date: typeof row.exitDate === 'string' ? row.exitDate.slice(0, 40) : undefined,
      setup: typeof row.setup === 'string' && row.setup ? row.setup.slice(0, 100) : undefined,
      // thesis / reviewNotes / screenshotDataUrl are deliberately omitted.
    }))
    .filter(row => row.symbol)
    // Newest entries first, so a long journal is truncated from the old end.
    .reverse()
    .slice(0, MAX_JOURNAL_ENTRIES) as ChatBrowserData['journal_entries'];
}

function mapScanPresets(): ChatBrowserData['scan_presets'] {
  return readArray(SCANNER_PRESETS_KEY)
    .filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object')
    .map(row => {
      const filters = Array.isArray(row.filters) ? row.filters : [];
      return {
        name: String(row.name ?? '').slice(0, 80),
        filters: filters
          .filter((filter): filter is Record<string, unknown> => Boolean(filter) && typeof filter === 'object')
          .slice(0, 40)
          .map(filter => {
            const params = filter.params && typeof filter.params === 'object' && !Array.isArray(filter.params)
              ? Object.fromEntries(Object.entries(filter.params).slice(0, 20).flatMap(([key, value]) => {
                const item = primitive(value);
                return item === undefined ? [] : [[key.slice(0, 80), item]];
              }))
              : {};
            return { type: String(filter.type ?? '').slice(0, 80), params };
          })
          .filter(filter => filter.type),
        match: row.match === 'OR' ? ('OR' as const) : ('AND' as const),
      };
    })
    .filter(row => row.name)
    .slice(0, MAX_PRESETS) as ChatBrowserData['scan_presets'];
}

/**
 * The snapshot to send with one Chat turn, or `null` when nothing is shared.
 *
 * Only switched-on, question-relevant categories are read, and a category
 * with no stored rows is left out entirely so the backend keeps its honest
 * "unavailable" answer rather than receiving an empty list. The content is
 * required here so enabling one category cannot disclose another category's
 * snapshot on the same turn.
 */
export function collectChatBrowserData(settings: ChatSharingSettings, content: string): ChatBrowserData | null {
  const payload: ChatBrowserData = {};
  const relevant = new Set(relevantSharingKeys(content, settings));
  if (relevant.has('positions')) {
    const positions = mapPositions();
    if (positions?.length) payload.positions = positions;
  }
  if (relevant.has('journal')) {
    const entries = mapJournalEntries();
    if (entries?.length) payload.journal_entries = entries;
  }
  if (relevant.has('scan_presets')) {
    const presets = mapScanPresets();
    if (presets?.length) payload.scan_presets = presets;
  }
  return Object.keys(payload).length ? payload : null;
}
