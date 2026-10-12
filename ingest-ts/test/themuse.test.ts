import { describe, expect, it, vi } from 'vitest';
import { fetchTheMuse, pageUrl, parseTheMuse } from '../src/sources/themuse.js';

const page = (n: number, count: number, ids: number[]) => ({
  page: n,
  page_count: count,
  results: ids.map((id) => ({
    id,
    name: `Software Engineer ${id}`,
    publication_date: '2026-10-05T14:00:00Z',
    locations: [{ name: 'Boston, MA' }, { name: 'Remote' }],
    company: { name: 'Acme' },
    refs: { landing_page: `https://www.themuse.com/jobs/acme/se-${id}` },
  })),
});

describe('The Muse source', () => {
  it('maps API rows to records and skips incomplete ones', () => {
    const recs = parseTheMuse({
      results: [
        ...page(1, 1, [7]).results,
        { id: 8, name: 'No company', refs: { landing_page: 'https://x' } },
        { id: 9, name: 'Bad link', company: { name: 'B' }, refs: { landing_page: 'javascript:alert(1)' } },
        { id: 10, name: '  Data Intern ', company: { name: 'C' }, refs: { landing_page: 'https://y' }, locations: [] },
      ],
    });
    expect(recs).toEqual([
      { company: 'Acme', title: 'Software Engineer 7', location: 'Boston, MA', url: 'https://www.themuse.com/jobs/acme/se-7', job_id: '7', source: 'themuse_ts', published_at: '2026-10-05T14:00:00Z' },
      { company: 'C', title: 'Data Intern', location: 'Unknown', url: 'https://y', job_id: '10', source: 'themuse_ts', published_at: null },
    ]);
  });

  it('builds the filtered page URL', () => {
    const u = new URL(pageUrl(2));
    expect(u.searchParams.get('page')).toBe('2');
    expect(u.searchParams.getAll('level')).toEqual(['Internship', 'Entry Level']);
    expect(u.searchParams.getAll('category')).toEqual(['Software Engineering', 'Data and Analytics']);
  });

  it('stops paging at page_count', async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      const n = Number(new URL(url).searchParams.get('page'));
      return new Response(JSON.stringify(page(n, 2, [n * 10])), { status: 200 });
    });
    const recs = await fetchTheMuse(5, { fetchImpl, retries: 0 });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(recs.map((r) => r.job_id)).toEqual(['10', '20']);
  });
});
