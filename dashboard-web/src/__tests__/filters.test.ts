import { describe, expect, it } from 'vitest';
import { jobsPath } from '../api';
import { filtersReducer, initialFilters } from '../state/filters';

describe('filters', () => {
  it('resets to page 0 when a filter changes and clamps paging', () => {
    let s = filtersReducer(initialFilters, { type: 'page', page: 3 });
    expect(s.page).toBe(3);
    s = filtersReducer(s, { type: 'set', patch: { sponsored: true } });
    expect(s).toMatchObject({ sponsored: true, page: 0 });
    expect(filtersReducer(s, { type: 'page', page: -2 }).page).toBe(0);
    expect(filtersReducer(s, { type: 'reset' })).toEqual(initialFilters);
  });

  it('builds the jobs query string', () => {
    expect(jobsPath(initialFilters)).toBe('/jobs?limit=25&offset=0');
    expect(jobsPath({ q: ' stripe ', source: 'SWE List', jobType: 'Co-op', sponsored: true, remote: true, page: 2 }))
      .toBe('/jobs?q=stripe&source=SWE+List&job_type=Co-op&sponsored=true&remote=true&limit=25&offset=50');
  });
});
