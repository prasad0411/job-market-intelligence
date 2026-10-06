import { fetchJson, type FetchJsonOptions } from '../http.js';
import type { JobRecord } from '../types.js';

export const REMOTIVE_SOURCE = 'remotive_ts';
const URLS = [
  'https://remotive.com/api/remote-jobs?category=software-dev&limit=200',
  'https://remotive.com/api/remote-jobs?category=data&limit=100',
];

interface RemotiveJob {
  id?: number | string;
  url?: string;
  title?: string;
  company_name?: string;
  candidate_required_location?: string;
  publication_date?: string;
}

/** Remotive dates carry no timezone; they are published in UTC. */
export function toIsoUtc(d: string | undefined): string | null {
  if (!d) return null;
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(d) ? d : `${d}Z`;
  return Number.isNaN(Date.parse(s)) ? null : new Date(s).toISOString();
}

export function parseRemotive(data: { jobs?: RemotiveJob[] }): JobRecord[] {
  const out: JobRecord[] = [];
  for (const j of data.jobs ?? []) {
    const title = j.title?.trim();
    const company = j.company_name?.trim();
    const url = j.url?.trim();
    if (!title || !company || !url || !url.startsWith('http')) continue;
    out.push({
      company,
      title,
      location: j.candidate_required_location?.trim() || 'Remote',
      url,
      job_id: j.id !== undefined ? String(j.id) : 'N/A',
      source: REMOTIVE_SOURCE,
      published_at: toIsoUtc(j.publication_date),
    });
  }
  return out;
}

export async function fetchRemotive(http: FetchJsonOptions = {}): Promise<JobRecord[]> {
  const pages = await Promise.all(URLS.map((u) => fetchJson<{ jobs?: RemotiveJob[] }>(u, http)));
  return pages.flatMap(parseRemotive);
}
