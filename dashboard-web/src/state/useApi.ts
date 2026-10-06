import { useEffect, useState } from 'react';
import { getJson } from '../api';

export interface Loadable<T> { data: T | null; error: string | null; loading: boolean }

interface Result<T> { path: string | null; data: T | null; error: string | null }

/** Fetches path whenever it changes, aborts stale requests, keeps the last data while reloading. */
export function useApi<T>(path: string | null): Loadable<T> {
  const [result, setResult] = useState<Result<T>>({ path: null, data: null, error: null });
  useEffect(() => {
    if (path === null) return;
    const ctrl = new AbortController();
    getJson<T>(path, ctrl.signal)
      .then((data) => setResult({ path, data, error: null }))
      .catch((err: unknown) => {
        if (!ctrl.signal.aborted) setResult({ path, data: null, error: err instanceof Error ? err.message : 'Request failed' });
      });
    return () => ctrl.abort();
  }, [path]);
  return { data: result.data, error: result.path === path ? result.error : null, loading: path !== null && result.path !== path };
}
