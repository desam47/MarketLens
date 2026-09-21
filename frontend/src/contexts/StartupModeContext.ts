import { createContext, useContext } from 'react';

export type StartupMode = 'full' | 'api' | null;

export const StartupModeContext = createContext<StartupMode>(null);

export function useStartupMode(): StartupMode {
  return useContext(StartupModeContext);
}
