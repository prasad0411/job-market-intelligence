import { fetchJson, type FetchJsonOptions } from '../http.js';
import type { JobRecord } from '../types.js';

export const THEMUSE_SOURCE = 'themuse_ts';
const BASE = 'https://www.themuse.com/api/public/jobs';
const CATEGORIES = ['Software Engineering', 'Data and Analytics'];
const LEVELS = ['Internship', 'Entry Level'];

interface MuseJob {
  id?: number | string;
  name?: string;
  publication_date?: string;
  locations?: { name?: string }[];
  company?: { name?: string };
  refs?: { landing_page?: string };
}
interface MusePage { page?: number; page_count?: number; results?: MuseJob[] }

export function pageUrl(page: number): string {
  const p = new URLSearchParams({ page: String(page), descending: 'true' });
  for (const c of CATEGORIES) p.append('category', c);
  for (const l of LEVELS) p.append('level', l);
  return `${BASE}?${p.toString()}`;
}

/** Pure mapping from one API page to records; rows missing a title, company or link are skipped. */
export function parseTheMuse(page: MusePage): JobRecord[] {
  const out: JobRecord[] = [];
  for (const j of page.results ?? []) {
    const title = j.name?.trim();
    const company = j.company?.name?.trim();
    const url = j.refs?.landing_page?.trim();
    if (!title || !company || !url || !url.startsWith('http')) continue;
    out.push({
      company,
      title,
      location: j.locations?.find((l) => l.name?.trim())?.name?.trim() ?? 'Unknown',
      url,
      job_id: j.id !== undefined ? String(j.id) : 'N/A',
      source: THEMUSE_SOURCE,
      published_at: j.publication_date ?? null,
    });
  }
  return out;
}

export async function fetchTheMuse(maxPages = 5, http: FetchJsonOptions = {}): Promise<JobRecord[]> {
  const records: JobRecord[] = [];
  for (let page = 1; page <= maxPages; page++) {
    const data = await fetchJson<MusePage>(pageUrl(page), http);
    records.push(...parseTheMuse(data));
    if (!data.page_count || page >= data.page_count) break;
  }
  return records;
}
