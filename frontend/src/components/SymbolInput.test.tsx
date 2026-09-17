import { render, screen, fireEvent } from '@testing-library/react';
import { SymbolInput } from './SymbolInput';

describe('SymbolInput', () => {
  it('renders the input with the current symbol value', () => {
    render(<SymbolInput symbol="SPY" onChange={() => {}} onSubmit={() => {}} />);
    const input = screen.getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('SPY');
  });

  it('calls only onChange (not onSubmit) when the form is submitted with a changed symbol', () => {
    const onChange = jest.fn();
    const onSubmit = jest.fn();
    render(<SymbolInput symbol="AAPL" onChange={onChange} onSubmit={onSubmit} />);
    // Type a different symbol — the component normalizes on input change.
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'msft' } });
    fireEvent.click(screen.getByRole('button', { name: /analyze/i }));
    expect(onChange).toHaveBeenCalledWith('MSFT');
    // onSubmit must NOT fire here — the parent's effect on the changed
    // `symbol` prop is responsible for fetching. Calling onSubmit too
    // would fetch the old symbol, then the new one, as a duplicate request.
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('calls onSubmit even when the symbol has not changed (onChange is not called)', () => {
    const onChange = jest.fn();
    const onSubmit = jest.fn();
    render(<SymbolInput symbol="NVDA" onChange={onChange} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole('button', { name: /analyze/i }));
    // onChange should not fire because the input value matches the prop.
    expect(onChange).not.toHaveBeenCalled();
    // onSubmit always fires regardless.
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('calls onChange with the normalized (uppercase) symbol when the form is submitted', () => {
    const onChange = jest.fn();
    render(<SymbolInput symbol="spy" onChange={onChange} onSubmit={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /analyze/i }));
    expect(onChange).toHaveBeenCalledWith('SPY');
  });

  it('does not call onChange when the input is unchanged from the prop', () => {
    const onChange = jest.fn();
    render(<SymbolInput symbol="MSFT" onChange={onChange} onSubmit={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /analyze/i }));
    // The check in handleSubmit is: normalized !== symbol
    // "MSFT" !== "MSFT" → false, so onChange is not called.
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox')).toHaveValue('MSFT');
  });
});
