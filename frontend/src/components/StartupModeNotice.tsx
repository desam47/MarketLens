import React from 'react';

export function StartupModeNotice({ startupMode }: { startupMode: 'full' | 'api' | null }) {
  if (startupMode !== 'api') return null;

  return (
    <div className="startup-mode-notice" role="status">
      <strong>API mode</strong>
      <span>Live market data is paused. Set STARTUP_MODE=full and restart to re-enable it.</span>
    </div>
  );
}
