import type { FetchLike } from './types.js';

export interface FetchJsonOptions {
  fetchImpl?: FetchLike;
  timeoutMs?: number;
  retries?: number;
  backoffMs?: number;
}

export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, url: string) {
    super(`HTTP ${status} for ${url}`);
    this.name = 'HttpError';
    this.status = status;
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** GET JSON with a per attempt timeout, retrying only on 429, 5xx and network errors. */
export async function fetchJson<T = unknown>(url: string, opts: FetchJsonOptions = {}): Promise<T> {
  const { fetchImpl = fetch as FetchLike, timeoutMs = 15_000, retries = 2, backoffMs = 1_000 } = opts;
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetchImpl(url, {
        signal: AbortSignal.timeout(timeoutMs),
        headers: { Accept: 'application/json', 'User-Agent': 'job-market-intelligence/1.0 (+https://github.com/prasad0411/job-market-intelligence)' },
      });
      if (res.ok) return (await res.json()) as T;
      if (res.status !== 429 && res.status < 500) throw new HttpError(res.status, url);
      lastError = new HttpError(res.status, url);
    } catch (err) {
      if (err instanceof HttpError && err.status !== 429 && err.status < 500) throw err;
      lastError = err;
    }
    if (attempt < retries) await sleep(backoffMs * 2 ** attempt);
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}
