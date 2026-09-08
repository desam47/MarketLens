import { useState, useEffect, useCallback } from 'react';
import api from '../services/api';

/**
 * Hook to manage individual card data fetching.
 * Handles the common safeCall logic, loading, and error state.
 */
export function useDashboardCardData<T>(
  fetchFn: (symbol: string, ...args: any[]) => Promise<T>,
  symbol: string,
  args: any[] = []
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchFn(symbol, ...args);
      setData(result);
    } catch (err: any) {
      setError(err?.message || 'Request failed');
    } finally {
      setLoading(false);
    }
  }, [fetchFn, symbol, ...args]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return { data, loading, error, refetch: fetchData };
}
