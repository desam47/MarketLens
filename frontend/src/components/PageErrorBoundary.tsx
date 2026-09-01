import React, { Component, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Short label for the error message, e.g. "Dashboard" */
  pageName?: string;
}

interface State {
  hasError: boolean;
  errorMessage: string;
}

/**
 * PageErrorBoundary — catches uncaught errors thrown by any child component
 * (page, card, hook) and renders a styled fallback instead of blanking the
 * entire page.
 *
 * Placed around each page rendered by App.tsx so a crash in one page
 * (e.g. a malformed API response, a third-party library bug) does not
 * affect the rest of the app.
 */
export class PageErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, errorMessage: '' };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, errorMessage: error.message };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Log to console for debugging; the user sees the fallback UI.
    console.error(`[PageErrorBoundary] ${this.props.pageName ?? 'page'} crashed:`, error, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, errorMessage: '' });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="page-error-boundary">
          <div className="page-error-card">
            <div className="page-error-icon">⚠️</div>
            <h2>Something went wrong</h2>
            <p className="page-error-name">
              {this.props.pageName ?? 'This page'} encountered an error.
            </p>
            {this.state.errorMessage && (
              <pre className="page-error-message">{this.state.errorMessage}</pre>
            )}
            <div className="page-error-actions">
              <button className="btn" onClick={this.handleRetry}>
                Try again
              </button>
              <button
                className="btn"
                onClick={() => window.location.reload()}
              >
                Reload page
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
