import { combineReducers, configureStore, createSelector } from '@reduxjs/toolkit';
import { useDispatch, useSelector } from 'react-redux';
import { averageSecondsPerJob, initialReview, reduction, type ReviewState } from '../state/review';
import filters from './filtersSlice';
import review from './reviewSlice';

const REVIEW_KEY = 'jmi.review.v1';
const rootReducer = combineReducers({ filters, review });
export type RootState = ReturnType<typeof rootReducer>;

function loadReview(): ReviewState {
  try {
    const saved = JSON.parse(localStorage.getItem(REVIEW_KEY) ?? 'null');
    if (saved && Array.isArray(saved.history)) {
      return { ...initialReview, history: saved.history, baselineSeconds: saved.baselineSeconds ?? null };
    }
  } catch {
    /* storage unavailable or corrupt: start fresh */
  }
  return initialReview;
}

/** A fresh store per app (and per test), with saved review sessions restored. */
export function makeStore(preloadedState?: Partial<RootState>) {
  const store = configureStore({
    reducer: rootReducer,
    preloadedState: { review: loadReview(), ...preloadedState },
  });
  let last = store.getState().review;
  store.subscribe(() => {
    const { review: next } = store.getState();
    if (next.history === last.history && next.baselineSeconds === last.baselineSeconds) return;
    last = next;
    try {
      localStorage.setItem(REVIEW_KEY, JSON.stringify({ history: next.history, baselineSeconds: next.baselineSeconds }));
    } catch {
      /* private mode: measurements stay in memory */
    }
  });
  return store;
}

export type AppStore = ReturnType<typeof makeStore>;
export type AppDispatch = AppStore['dispatch'];

export const useAppDispatch = useDispatch.withTypes<AppDispatch>();
export const useAppSelector = useSelector.withTypes<RootState>();

export const selectFilters = (s: RootState) => s.filters;
export const selectReview = (s: RootState) => s.review;

/** Memoised: recomputed only when the session history or baseline changes. */
export const selectReviewStats = createSelector(
  [(s: RootState) => s.review.history, (s: RootState) => s.review.baselineSeconds],
  (history, baseline) => {
    const avg = averageSecondsPerJob(history);
    return {
      avgSeconds: avg,
      reduction: reduction(avg, baseline),
      sessions: history.length,
      postings: history.reduce((n, h) => n + h.reviewed, 0),
    };
  },
);
