import React, { useState } from 'react';

interface SymbolInputProps {
  symbol: string;
  onChange: (symbol: string) => void;
  onSubmit: () => void;
}

export function SymbolInput({ symbol, onChange, onSubmit }: SymbolInputProps) {
  const [inputValue, setInputValue] = useState(symbol);

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
}
