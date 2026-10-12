export interface Summary {
  runs: number;
  last_run: string | null;
  jobs_evaluated: number;
  jobs_evaluated_per_run: number;
  avg_run_minutes: number;
  median_run_minutes: number;
  valid_jobs: number;
  sponsored_share: number;
  remote_jobs: number;
  companies: number;
  sources: number;
}
export interface WeekPoint { week: string; postings: number; valid: number; sponsored: number; sources: number }
export interface SourceRow { source: string; accepted: number; rejected: number; total: number; yield_rate: number }
export interface FunnelRow { stage: string; reason: string; count: number }
export interface CompanyRow {
  company: string; total: number; valid: number; sponsored: number;
  sponsorship_rate: number; valid_rate: number; sources: number;
}
export interface Job {
  id: number; company: string; title: string; location: string; source: string; url: string;
  job_type: string; remote: boolean; sponsored: boolean; track: string;
  entry_date?: string | null;
}
export interface JobPage { total: number; items: Job[] }
export interface Stage { stage: string; key: string; count: number }
export interface Run { ts: string; minutes: number; valid: number; discarded: number; failed_http: number }
export interface QuarantineRow { family: string; reason: string; rows: number; share: number }
export interface Meta { generated_at: string }
export interface Count { label: string; count: number }
export interface Insights {
  valid_postings: number;
  remote_share: number;
  roles: Count[];
  tracks: Count[];
  states: Count[];
  top_hiring: CompanyRow[];
  companies_total: number;
  companies_sponsoring: number;
  top_sponsors: CompanyRow[];
}
export interface JobQuery { q: string; source: string; jobType: string; sponsored: boolean; remote: boolean; page: number }

export const PAGE_SIZE = 25;
const BASE: string = import.meta.env?.VITE_API_BASE ?? '/api';
export const STATIC_MODE: boolean = import.meta.env?.VITE_DATA_MODE === 'static';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal, fetchImpl: typeof fetch = fetch): Promise<T> {
  if (STATIC_MODE) {
    const { resolveStatic } = await import('./staticData');
    return resolveStatic<T>(path, fetchImpl);
  }
  let res: Response;
  try {
    res = await fetchImpl(`${BASE}${path}`, { signal });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(0, 'Cannot reach the dashboard API. Start it with: uvicorn dashboard_api.app:app --port 8001');
  }
  if (res.status >= 502 && res.status <= 504) {
    throw new ApiError(res.status, 'Cannot reach the dashboard API. Start it with: uvicorn dashboard_api.app:app --port 8001');
  }
  if (!res.ok) throw new ApiError(res.status, `Request failed (${res.status})`);
  return (await res.json()) as T;
}

export function jobsPath(q: JobQuery): string {
  const p = new URLSearchParams();
  if (q.q.trim()) p.set('q', q.q.trim());
  if (q.source) p.set('source', q.source);
  if (q.jobType) p.set('job_type', q.jobType);
  if (q.sponsored) p.set('sponsored', 'true');
  if (q.remote) p.set('remote', 'true');
  p.set('limit', String(PAGE_SIZE));
  p.set('offset', String(q.page * PAGE_SIZE));
  return `/jobs?${p.toString()}`;
}
