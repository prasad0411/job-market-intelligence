import { describe, expect, it, vi } from 'vitest';
import type { CompanyRow, Job } from '../api';
import { clearStaticCache, filterCompanies, filterJobs, resolveStatic } from '../staticData';

const job = (id: number, over: Partial<Job> = {}): Job => ({
  id, company: 'Acme', title: 'Software Engineer Intern', location: 'NYC', source: 'SWE List', url: `https://x/${id}`,
  job_type: 'Internship', remote: false, sponsored: false, track: 'SDE', ...over,
});

describe('static snapshot queries mirror the API', () => {
  const jobs = [job(3, { company: 'Stripe', sponsored: true }), job(2, { remote: true, job_type: 'Co-op' }), job(1, { source: 'LinkedIn' })];

  it('filters, normalises unknown types and pages like queries.jobs', () => {
    const q = (s: string) => filterJobs(jobs, new URLSearchParams(s));
    expect(q('limit=25&offset=0').total).toBe(3);
    expect(q('q=stripe').items.map((j) => j.id)).toEqual([3]);
    expect(q('sponsored=true').items.map((j) => j.id)).toEqual([3]);
    expect(q('remote=true&job_type=Co-op').items.map((j) => j.id)).toEqual([2]);
    expect(q('source=LinkedIn').items.map((j) => j.id)).toEqual([1]);
    expect(q('job_type=Plano, TX').total).toBe(3);
    expect(q('limit=1&offset=1')).toEqual({ total: 3, items: [jobs[1]] });
  });

  it('filters companies', () => {
    const cs = [{ company: 'Ramp', sponsored: 2 }, { company: 'Plaid', sponsored: 0 }] as CompanyRow[];
    expect(filterCompanies(cs, new URLSearchParams('q=ra')).map((c) => c.company)).toEqual(['Ramp']);
    expect(filterCompanies(cs, new URLSearchParams('sponsored=true')).map((c) => c.company)).toEqual(['Ramp']);
  });

  it('loads each snapshot file once and reports a missing file', async () => {
    clearStaticCache();
    const fetchImpl = vi.fn(async (u: RequestInfo | URL) =>
      String(u).endsWith('/data/summary.json') ? new Response('{"runs":1}') : new Response('', { status: 404 }));
    await resolveStatic('/summary', fetchImpl as typeof fetch);
    await resolveStatic('/summary', fetchImpl as typeof fetch);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    await expect(resolveStatic('/weekly', fetchImpl as typeof fetch)).rejects.toThrow('weekly.json is missing');
  });
});
