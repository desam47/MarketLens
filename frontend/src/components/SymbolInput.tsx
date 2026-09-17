import React, { useState, useImperativeHandle, forwardRef } from 'react';

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

  useImperativeHandle(ref, () => ({
    focus: () => { /* input is uncontrolled-ish; focus handled by the form */ },
    setValue: (v: string) => setInputValue(v.toUpperCase()),
  }), []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const normalized = inputValue.toUpperCase().trim();
    if (normalized && normalized !== symbol) {
      onChange(normalized);
    }
    onSubmit();
  };

  return (
    <form onSubmit={handleSubmit} className="symbol-input">
      <input
        type="text"
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value.toUpperCase())}
        placeholder="SYMBOL"
        maxLength={5}
        className="symbol-field"
      />
      <button type="submit" className="btn btn-primary">
        Analyze
      </button>
    </form>
  );
});
