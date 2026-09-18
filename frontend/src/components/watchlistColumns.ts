/**
 * Watchlist table column definitions - single source of truth for column
 * widths, keys, and metadata used by both regular and virtualized renderers.
 */

export interface WatchlistColumn {
  key: string;
  header: string;
  sortable: boolean;
  width: string;        // CSS width for regular table th
  virtWidth: string;    // CSS grid width for virtualized header/row
  minWidth?: number;    // Minimum pixel width for responsive behavior
  visible: boolean;     // Default visibility (can be toggled by user)
}

export const WATCHLIST_COLUMNS: WatchlistColumn[] = [
  {
    key: 'symbol',
    header: 'Symbol',
    sortable: true,
    width: '80px',
    virtWidth: '80px',
    minWidth: 70,
    visible: true,
  },
  {
    key: 'type',
    header: 'Type',
    sortable: false,
    width: '64px',
    virtWidth: '64px',
    minWidth: 56,
    visible: true,
  },
  {
    key: 'price',
    header: 'Price',
    sortable: true,
    width: '110px',
    virtWidth: 'minmax(110px, 1fr)',
    minWidth: 90,
    visible: true,
  },
  {
    key: 'change',
    header: 'Change %',
    sortable: true,
    width: '80px',
    virtWidth: '80px',
    minWidth: 70,
    visible: true,
  },
  {
    key: 'trend',
    header: 'Trend',
    sortable: false,
    width: '320px',
    virtWidth: 'minmax(200px, 1fr)',
    minWidth: 180,
    visible: true,
  },
  {
    key: 'rs',
    header: 'Rel. Strength',
    sortable: true,
    width: '1fr',
    virtWidth: '1fr',
    minWidth: 120,
    visible: true,
  },
  {
    key: 'actions',
    header: 'Actions',
    sortable: false,
    width: '70px',
    virtWidth: '70px',
    minWidth: 64,
    visible: true,
  },
];

export function getVisibleColumns(): WatchlistColumn[] {
  return WATCHLIST_COLUMNS.filter(c => c.visible);
}

export function getColumnByKey(key: string): WatchlistColumn | undefined {
  return WATCHLIST_COLUMNS.find(c => c.key === key);
}

export function buildVirtGridTemplateColumns(visibleKeys?: Set<string>): string {
  const cols = visibleKeys
    ? WATCHLIST_COLUMNS.filter(c => visibleKeys.has(c.key))
    : WATCHLIST_COLUMNS;
  return cols.map(c => c.virtWidth).join(' ');
}

export function buildRegularTableWidths(): string {
  return getVisibleColumns().map(c => c.width).join(' ');
}

export function getSortableKeys(): string[] {
  return WATCHLIST_COLUMNS.filter(c => c.sortable).map(c => c.key);
}