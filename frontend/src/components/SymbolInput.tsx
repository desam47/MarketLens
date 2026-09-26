import React, { useState, useImperativeHandle, forwardRef, useEffect, useRef } from 'react';
import { SymbolAutocompleteInput, rememberSymbol, resolveWatchlistSymbol, type SymbolAutocompleteInputHandle } from './SymbolAutocompleteInput';

interface SymbolInputProps {
  symbol: string;
  onChange: (symbol: string) => void;
  onSubmit: () => void;
}

export interface SymbolInputHandle {
  focus: () => void;
  setValue: (v: string) => void;
}

export const SymbolInput = forwardRef<SymbolInputHandle, SymbolInputProps>(
  function SymbolInput({ symbol, onChange, onSubmit }, ref) {
  const [inputValue, setInputValue] = useState(symbol);
  const inputRef = useRef<SymbolAutocompleteInputHandle | null>(null);

  useEffect(() => setInputValue(symbol), [symbol]);

  useImperativeHandle(ref, () => ({
    focus: () => inputRef.current?.focus(),
    setValue: (v: string) => setInputValue(v.toUpperCase()),
  }), []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const normalized = inputValue.toUpperCase().trim();
    if (!normalized) return;
    if (!await resolveWatchlistSymbol(normalized)) return;
    rememberSymbol(normalized);
    if (normalized !== symbol) {
      // Parent's effect on `symbol` will fetch the new symbol once its
      // state updates — calling onSubmit here too would fetch the old
      // symbol first, then the new one, as a duplicate request.
      onChange(normalized);
    } else {
      onSubmit();
    }
  };

  return (
    <form onSubmit={handleSubmit} className="symbol-input">
      <SymbolAutocompleteInput
        ref={inputRef}
        value={inputValue}
        onChange={setInputValue}
        placeholder="SYMBOL"
        className="symbol-field"
      />
      <button type="submit" className="btn btn-primary">
        Analyze
      </button>
    </form>
  );
});
