import { render, screen, fireEvent } from '@testing-library/react';
import { ErrorBanner } from './ErrorBanner';

describe('ErrorBanner', () => {
  it('renders the provided message', () => {
    render(<ErrorBanner message="Something failed" />);
    expect(screen.getByText('Something failed')).toBeInTheDocument();
  });

  it('renders the warning icon', () => {
    render(<ErrorBanner message="Boom" />);
    expect(screen.getByText('⚠️')).toBeInTheDocument();
  });

  it('does not render a dismiss button when onDismiss is omitted', () => {
    render(<ErrorBanner message="No callback" />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders a dismiss button when onDismiss is provided', () => {
    render(<ErrorBanner message="With callback" onDismiss={() => undefined} />);
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('calls onDismiss when the button is clicked', () => {
    const onDismiss = jest.fn();
    render(<ErrorBanner message="Clickable" onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
