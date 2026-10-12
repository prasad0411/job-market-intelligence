import type { JobQuery } from '../api';

export const initialFilters: JobQuery = { q: '', source: '', jobType: '', sponsored: false, remote: false, page: 0 };

export type FilterAction =
  | { type: 'set'; patch: Partial<Omit<JobQuery, 'page'>> }
  | { type: 'page'; page: number }
  | { type: 'reset' };

export function filtersReducer(state: JobQuery, action: FilterAction): JobQuery {
  switch (action.type) {
    case 'set':
      return { ...state, ...action.patch, page: 0 };
    case 'page':
      return { ...state, page: Math.max(0, action.page) };
    case 'reset':
      return initialFilters;
  }
}
