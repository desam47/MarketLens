import React from 'react';
import { render, screen } from '@testing-library/react';
import { StartupModeNotice } from './StartupModeNotice';

describe('StartupModeNotice', () => {
  it('explains when live market data is paused in API mode', () => {
    render(<StartupModeNotice startupMode="api" />);

    expect(screen.getByRole('status')).toHaveTextContent('API mode');
    expect(screen.getByText(/Live market data is paused/)).toBeInTheDocument();
  });

  it('stays hidden during normal full startup', () => {
    const { container } = render(<StartupModeNotice startupMode="full" />);

    expect(container).toBeEmptyDOMElement();
  });
});
