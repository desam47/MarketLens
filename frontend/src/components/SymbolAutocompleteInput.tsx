import React, { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import api from '../services/api';

const RECENT_SYMBOLS_KEY = 'marketlens.recent.symbols';
const MAX_RECENT_SYMBOLS = 12;
export const SYMBOL_MAX_LENGTH = 10;

let catalogPromise: Promise<string[]> | null = null;

export function clearSymbolCatalogCache(): void {
  catalogPromise = null;
}

function normalize(value: string): string {
  return value.trim().toUpperCase();
}

function readRecentSymbols(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENT_SYMBOLS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === 'string').map(normalize).filter(Boolean)
      : [];
  } catch {
    return [];
  }
}

export function rememberSymbol(value: string): void {
  const symbol = normalize(value);
  if (!symbol) return;
  const next = [symbol, ...readRecentSymbols().filter(item => item !== symbol)].slice(0, MAX_RECENT_SYMBOLS);
  try {
    window.localStorage.setItem(RECENT_SYMBOLS_KEY, JSON.stringify(next));
  } catch {
    // Private browsing or blocked site data should not disable symbol entry.
  }
}

function loadSymbolCatalog(): Promise<string[]> {
  if (!catalogPromise) {
    catalogPromise = api.getSymbolCatalog().then((symbols) => (
      symbols.map(normalize).filter(Boolean)
    )).catch(() => {
      catalogPromise = null;
      return [];
    });
  }
  return catalogPromise;
}

/** Resolve a typed value only when it is currently on a Watchlist. */
export async function resolveWatchlistSymbol(value: string): Promise<string | null> {
  const symbol = normalize(value);
  if (!symbol) return null;
  const catalog = await loadSymbolCatalog();
  return catalog.includes(symbol) ? symbol : null;
}

export interface SymbolAutocompleteInputHandle {
  focus: () => void;
  blur: () => void;
}

export interface SymbolAutocompleteInputProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'onSelect'> {
  value: string;
  onChange: (value: string) => void;
  onSelect?: (symbol: string) => void;
}

/** Shared symbol field used anywhere the app asks for a ticker. */
export const SymbolAutocompleteInput = forwardRef<
  SymbolAutocompleteInputHandle,
  SymbolAutocompleteInputProps
>(function SymbolAutocompleteInput({
  value,
  onChange,
  onSelect,
  onKeyDown,
  onFocus,
  onBlur,
  className,
  disabled,
  maxLength = SYMBOL_MAX_LENGTH,
  ...inputProps
}, ref) {
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<string[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogLoaded, setCatalogLoaded] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const [filtering, setFiltering] = useState(false);
  const [hasExplicitSelection, setHasExplicitSelection] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const query = normalize(value);
  const filterQuery = filtering ? query : '';

  const suggestions = useMemo(() => {
    return Array.from(new Set(catalog))
      .filter(symbol => !filterQuery || symbol.includes(filterQuery))
      .sort((left, right) => {
        if (!filterQuery) return left.localeCompare(right);
        const leftRank = left === filterQuery ? 0 : left.startsWith(filterQuery) ? 1 : 2;
        const rightRank = right === filterQuery ? 0 : right.startsWith(filterQuery) ? 1 : 2;
        return leftRank - rightRank || left.localeCompare(right);
      });
  }, [catalog, filterQuery]);

  useImperativeHandle(ref, () => ({
    focus: () => inputRef.current?.focus(),
    blur: () => inputRef.current?.blur(),
  }), []);

  useEffect(() => {
    if (!open || disabled) {
      return undefined;
    }
    let active = true;
    setCatalogLoading(true);
    loadSymbolCatalog().then((symbols) => {
      if (active) {
        setCatalog(symbols);
        setCatalogLoaded(true);
      }
    }).finally(() => {
      if (active) setCatalogLoading(false);
    });
    return () => {
      active = false;
    };
  }, [disabled, open]);

  useEffect(() => {
    setHighlighted(0);
    setHasExplicitSelection(false);
  }, [query]);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    const isKnown = !query || catalog.includes(query);
    input.setCustomValidity(catalogLoaded && !isKnown ? 'Choose a symbol from a Watchlist.' : '');
  }, [catalog, catalogLoaded, query]);

  useEffect(() => {
    const handleOutsidePointer = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handleOutsidePointer);
    return () => document.removeEventListener('mousedown', handleOutsidePointer);
  }, []);

  const choose = (symbol: string) => {
    const selected = normalize(symbol);
    rememberSymbol(selected);
    onChange(selected);
    onSelect?.(selected);
    setFiltering(false);
    setHasExplicitSelection(false);
    setOpen(false);
  };

  return (
    <div className="symbol-autocomplete" ref={rootRef}>
      <input
        {...inputProps}
        ref={inputRef}
        value={value}
        disabled={disabled}
        className={className}
        maxLength={maxLength}
        autoComplete="off"
        aria-autocomplete="list"
        onChange={(event) => {
          onChange(event.target.value.toUpperCase());
          setFiltering(true);
          setHasExplicitSelection(false);
          setOpen(true);
        }}
        onFocus={(event) => {
          // Load the catalog for validation, but do not cover surrounding
          // form controls with every ticker merely because this field gained
          // focus. Suggestions appear once the user starts searching.
          setFiltering(false);
          setHasExplicitSelection(false);
          setOpen(true);
          onFocus?.(event);
        }}
        onBlur={(event) => {
          // Keep a clicked option usable before closing the popup.
          window.setTimeout(() => setOpen(false), 120);
          onBlur?.(event);
        }}
        onKeyDown={(event) => {
          if (open && filtering && suggestions.length > 0 && event.key === 'ArrowDown') {
            event.preventDefault();
            setHasExplicitSelection(true);
            setHighlighted(index => Math.min(index + 1, suggestions.length - 1));
            return;
          }
          if (open && filtering && suggestions.length > 0 && event.key === 'ArrowUp') {
            event.preventDefault();
            setHasExplicitSelection(true);
            setHighlighted(index => Math.max(index - 1, 0));
            return;
          }
          if (
            open
            && filtering
            && suggestions.length > 0
            && event.key === 'Enter'
            && suggestions[highlighted]
            && query !== suggestions[highlighted]
            && (filtering || hasExplicitSelection)
          ) {
            event.preventDefault();
            choose(suggestions[highlighted]);
            return;
          }
          if (
            open
            && filtering
            && suggestions.length > 0
            && event.key === 'Tab'
            && suggestions[highlighted]
            && query !== suggestions[highlighted]
            && (filtering || hasExplicitSelection)
          ) {
            choose(suggestions[highlighted]);
            return;
          }
          if (event.key === 'Escape') setOpen(false);
          onKeyDown?.(event);
        }}
      />
      {open && filtering && (suggestions.length > 0 || catalogLoading || (query && !catalogLoading)) && (
        <ul className="symbol-autocomplete-list" role="listbox">
          {catalogLoading && suggestions.length === 0 && <li className="symbol-autocomplete-status">Loading symbols…</li>}
          {!catalogLoading && suggestions.length === 0 && query && (
            <li className="symbol-autocomplete-status">No Watchlist matches</li>
          )}
          {suggestions.map((suggestion, index) => (
            <li key={suggestion} role="option" aria-selected={index === highlighted}>
              <button
                type="button"
                className={index === highlighted ? 'symbol-autocomplete-option is-highlighted' : 'symbol-autocomplete-option'}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(suggestion)}
              >
                {suggestion}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
