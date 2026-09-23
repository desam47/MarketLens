import { useEffect, useState } from 'react';
import api, { MarketSession } from '../services/api';

const POLL_MS = 60_000;

/** Exchange-wide session state (premarket/regular/after_hours/closed), shared across pages. */
export function useMarketSession() {
  const [session, setSession] = useState<MarketSession | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      // Some isolated component tests provide a reduced API mock without this method.
      api.getMarketSession?.()
        ?.then(result => { if (!cancelled) setSession(result); })
        ?.catch(() => { /* stale/no session data is fine; badge falls back to age-only logic */ });
    };
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return session;
}

export default useMarketSession;
