import React from 'react';
import { type StartupMode } from '../contexts/StartupModeContext';

export function StartupModeNotice({ startupMode }: { startupMode: StartupMode }) {
  if (startupMode !== 'api') return null;

  return (
    <div className="startup-mode-notice" role="status">
      <strong>API mode</strong>
      <span>Live market data is paused. Set STARTUP_MODE=full and restart to re-enable it.</span>
    </div>
  );
}
