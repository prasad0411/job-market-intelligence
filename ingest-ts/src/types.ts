/** One posting, in the shape aggregator/ts_sources.py hands to the Python pipeline. */
export interface JobRecord {
  company: string;
  title: string;
  location: string;
  url: string;
  job_id: string;
  source: string;
  /** ISO 8601 publication time, or null when the source gives none. */
  published_at: string | null;
}

export interface SourceReport {
  source: string;
  records: number;
  error: string | null;
  ms: number;
}

export type FetchLike = (url: string, init?: { signal?: AbortSignal; headers?: Record<string, string> }) => Promise<Response>;
