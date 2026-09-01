import { render, screen, fireEvent } from '@testing-library/react';
import { PageErrorBoundary } from './PageErrorBoundary';

// Suppress React's "component threw" console.error for these tests —
// boundary errors are expected here and the React-internal warning
// floods the test output.
const originalError = console.error;
beforeAll(() => {
  console.error = jest.fn();
});
afterAll(() => {
  console.error = originalError;
});

function ThrowingChild({ message }: { message: string }): JSX.Element {
  throw new Error(message);
}

function SafeChild() {
  return <div data-testid="safe-child">All good</div>;
}

describe('PageErrorBoundary', () => {
  it('renders children when no error is thrown', () => {
    render(
      <PageErrorBoundary>
        <SafeChild />
      </PageErrorBoundary>
    );
    expect(screen.getByTestId('safe-child')).toBeInTheDocument();
    expect(screen.queryByText(/something went wrong/i)).toBeNull();
  });

  it('renders a fallback when a child throws', () => {
    render(
      <PageErrorBoundary pageName="Dashboard">
        <ThrowingChild message="kaboom" />
      </PageErrorBoundary>
    );
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    expect(screen.getByText(/Dashboard/)).toBeInTheDocument();
    expect(screen.getByText('kaboom')).toBeInTheDocument();
  });

  it('falls back to a generic page name when none is provided', () => {
    render(
      <PageErrorBoundary>
        <ThrowingChild message="explode" />
      </PageErrorBoundary>
    );
    // The boundary substitutes "This page" for an unspecified name.
    expect(screen.getByText(/This page encountered an error/)).toBeInTheDocument();
  });

  it('hides children after an error', () => {
    render(
      <PageErrorBoundary pageName="X">
        <ThrowingChild message="crash" />
      </PageErrorBoundary>
    );
    // The thrown child rendered nothing but its error message. The safe
    // child is not present because it was never mounted.
    expect(screen.queryByTestId('safe-child')).toBeNull();
  });

  it('recovers when the user clicks Try again', () => {
    // Use a component whose throw state is controlled so we can observe
    // the recovery path.
    let shouldThrow = true;
    function Conditional(): JSX.Element {
      if (shouldThrow) throw new Error('first');
      return <div data-testid="recovered">Recovered</div>;
    }
    const { rerender } = render(
      <PageErrorBoundary>
        <Conditional />
      </PageErrorBoundary>
    );
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();

    // Flip the flag and click Try again — boundary re-renders children.
    shouldThrow = false;
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    rerender(
      <PageErrorBoundary>
        <Conditional />
      </PageErrorBoundary>
    );
    expect(screen.getByTestId('recovered')).toBeInTheDocument();
  });
});
