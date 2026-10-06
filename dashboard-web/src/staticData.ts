/**
 * Static mode for the public site: the same paths the API serves, answered from a JSON
 * snapshot (public/data/*.json) with the same filtering, ordering and paging rules as
 * dashboard_api/queries.py, so local and public dashboards behave identically.
 */
import type { CompanyRow, Job, JobPage } from './api';

const JOB_TYPES = new Set(['Internship', 'Full Time', 'Co-op']);
const cache = new Map<string, Promise<unknown>>();

export function dataUrl(name: string): string {
  const base = import.meta.env?.BASE_URL ?? '/';
  return `${base.endsWith('/') ? base : `${base}/`}data/${name}.json`;
}

function load<T>(name: string, fetchImpl: typeof fetch): Promise<T> {
  if (!cache.has(name)) {
    cache.set(name, fetchImpl(dataUrl(name)).then((r) => {
      if (!r.ok) throw new Error(`Snapshot file ${name}.json is missing (${r.status})`);
      return r.json();
    }).catch((e: unknown) => { cache.delete(name); throw e; }));
  }
  return cache.get(name) as Promise<T>;
}

export function clearStaticCache(): void { cache.clear(); }

export function filterJobs(all: Job[], p: URLSearchParams): JobPage {
  const q = (p.get('q') ?? '').trim().toLowerCase();
  const source = p.get('source') ?? '';
  const type = p.get('job_type') ?? '';
  const sponsored = p.get('sponsored') === 'true';
  const remote = p.get('remote') === 'true';
  const limit = Number(p.get('limit') ?? 50);
  const offset = Number(p.get('offset') ?? 0);
  const rows = all.filter((j) =>
    (!q || j.company.toLowerCase().includes(q) || j.title.toLowerCase().includes(q)) &&
    (!source || j.source === source) &&
    (!JOB_TYPES.has(type) || j.job_type === type) &&
    (!sponsored || j.sponsored) &&
    (!remote || j.remote));
  return { total: rows.length, items: rows.slice(offset, offset + limit) };
}

export function filterCompanies(all: CompanyRow[], p: URLSearchParams): CompanyRow[] {
  const q = (p.get('q') ?? '').trim().toLowerCase();
  const sponsored = p.get('sponsored') === 'true';
  const limit = Number(p.get('limit') ?? 50);
  return all.filter((c) => (!q || c.company.toLowerCase().includes(q)) && (!sponsored || c.sponsored > 0)).slice(0, limit);
}

const FILES: Record<string, string> = {
  '/summary': 'summary', '/weekly': 'weekly', '/sources': 'sources', '/funnel': 'funnel',
  '/job-sources': 'job_sources', '/pipeline': 'pipeline', '/runs': 'runs', '/quarantine': 'quarantine', '/meta': 'meta',
};

export async function resolveStatic<T>(path: string, fetchImpl: typeof fetch = fetch): Promise<T> {
  const [route, query = ''] = path.split('?');
  const params = new URLSearchParams(query);
  if (route === '/jobs') return filterJobs(await load<Job[]>('jobs', fetchImpl), params) as T;
  if (route === '/companies') return filterCompanies(await load<CompanyRow[]>('companies', fetchImpl), params) as T;
  const file = FILES[route];
  if (!file) throw new Error(`No snapshot for ${route}`);
  return load<T>(file, fetchImpl);
}
