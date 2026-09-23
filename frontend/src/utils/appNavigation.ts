export type AppPage =
  | 'dashboard'
  | 'watchlist'
  | 'health'
  | 'alerts'
  | 'backtest'
  | 'symbol'
  | 'signals'
  | 'scanner'
  | 'hub'
  | 'risk'
  | 'journal'
  | 'calendar';

export interface NavigationState {
  symbol?: string;
  timeframe?: string;
  session?: string;
  filters?: Record<string, unknown>;
  selectedRecords?: string[];
}

const HASH_BY_PAGE: Record<AppPage, string> = {
  dashboard: '#dashboard',
  watchlist: '#watchlist',
  health: '#system-health',
  alerts: '#alerts',
  backtest: '#backtest',
  symbol: '#symbol',
  signals: '#signals',
  scanner: '#scanner',
  hub: '#ai-hub',
  risk: '#risk',
  journal: '#journal',
  calendar: '#calendar',
};

const PAGE_BY_HASH: Record<string, AppPage> = {
  '#dashboard': 'dashboard',
  '#watchlist': 'watchlist',
  '#system-health': 'health',
  '#alerts': 'alerts',
  '#backtest': 'backtest',
  '#symbol': 'symbol',
  '#signals': 'signals',
  '#historical-replay': 'signals',
  '#scanner': 'scanner',
  '#ai-hub': 'hub',
  '#risk': 'risk',
  '#journal': 'journal',
  '#calendar': 'calendar',
};

export function pageForHash(hash: string): AppPage {
  return PAGE_BY_HASH[hash] ?? 'dashboard';
}

export function hashForPage(page: AppPage): string {
  return HASH_BY_PAGE[page];
}
