import { describe, expect, it } from 'vitest';
import { makeStore, selectFilters, selectReviewStats } from '../store';
import { resetFilters, setFilters, setPage } from '../store/filtersSlice';
import { decide, setBaseline, startSession, stopSession } from '../store/reviewSlice';

describe('redux store', () => {
  it('resets paging when a filter changes', () => {
    const store = makeStore();
    store.dispatch(setPage(3));
    expect(selectFilters(store.getState()).page).toBe(3);
    store.dispatch(setFilters({ sponsored: true }));
    expect(selectFilters(store.getState())).toMatchObject({ sponsored: true, page: 0 });
    store.dispatch(resetFilters());
    expect(selectFilters(store.getState()).sponsored).toBe(false);
  });

  it('records a review session with memoised stats that survive a reload', () => {
    const store = makeStore();
    store.dispatch(startSession(0));
    store.dispatch(decide({ id: 1, decision: 'skip' }));
    store.dispatch(decide({ id: 2, decision: 'shortlist' }));
    store.dispatch(stopSession(20_000));
    store.dispatch(setBaseline(40));
    const first = selectReviewStats(store.getState());
    expect(selectReviewStats(store.getState())).toBe(first);
    expect(first).toMatchObject({ avgSeconds: 10, reduction: 0.75, sessions: 1, postings: 2 });
    expect(makeStore().getState().review.history).toHaveLength(1);
  });
});
