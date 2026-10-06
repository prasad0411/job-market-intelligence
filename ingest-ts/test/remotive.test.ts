import { describe, expect, it } from 'vitest';
import { parseRemotive, toIsoUtc } from '../src/sources/remotive.js';

describe('Remotive source', () => {
  it('maps jobs, defaults location to Remote, and treats naive dates as UTC', () => {
    const recs = parseRemotive({
      jobs: [
        { id: 1, url: 'https://remotive.com/remote-jobs/software-dev/a-1', title: 'Junior Backend Engineer', company_name: 'Acme', candidate_required_location: 'USA', publication_date: '2026-10-05T10:00:00' },
        { id: 2, url: 'https://remotive.com/remote-jobs/software-dev/b-2', title: 'New Grad SWE', company_name: 'Beta', candidate_required_location: '', publication_date: 'not a date' },
        { id: 3, title: 'No link', company_name: 'Gamma' },
      ],
    });
    expect(recs).toHaveLength(2);
    expect(recs[0]).toMatchObject({ location: 'USA', source: 'remotive_ts', job_id: '1', published_at: '2026-10-05T10:00:00.000Z' });
    expect(recs[1]).toMatchObject({ location: 'Remote', published_at: null });
  });

  it('keeps explicit offsets', () => {
    expect(toIsoUtc('2026-10-05T10:00:00+02:00')).toBe('2026-10-05T08:00:00.000Z');
    expect(toIsoUtc(undefined)).toBeNull();
  });
});
