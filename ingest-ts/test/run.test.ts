import { describe, expect, it } from 'vitest';
import { runAll } from '../src/run.js';
import type { JobRecord } from '../src/types.js';

const rec = (url: string, source: string): JobRecord => ({ company: 'A', title: 'Intern', location: 'NYC', url, job_id: '1', source, published_at: null });

describe('runAll', () => {
  it('keeps going when one source fails and dedupes by URL', async () => {
    const { records, reports } = await runAll({
      good: async () => [rec('https://a/1', 'good'), rec('https://a/2', 'good')],
      dup: async () => [rec('https://a/2', 'dup'), rec('https://a/3', 'dup')],
      broken: async () => { throw new Error('API changed'); },
    });
    expect(records.map((r) => r.url)).toEqual(['https://a/1', 'https://a/2', 'https://a/3']);
    expect(reports.map(({ source, records: n, error }) => ({ source, n, error }))).toEqual([
      { source: 'good', n: 2, error: null },
      { source: 'dup', n: 1, error: null },
      { source: 'broken', n: 0, error: 'API changed' },
    ]);
  });
});
