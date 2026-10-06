import { useEffect, useMemo, useReducer, type ReactNode } from 'react';
import { filtersReducer, initialFilters } from './filters';
import { initialReview, reviewReducer, type ReviewState } from './review';
import { REVIEW_KEY, StoreContext } from './storeContext';

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

export function StoreProvider({ children }: { children: ReactNode }) {
  const [filters, dispatchFilters] = useReducer(filtersReducer, initialFilters);
  const [review, dispatchReview] = useReducer(reviewReducer, undefined, loadReview);

  useEffect(() => {
    try {
      localStorage.setItem(REVIEW_KEY, JSON.stringify({ history: review.history, baselineSeconds: review.baselineSeconds }));
    } catch {
      /* private mode: measurements stay in memory */
    }
  }, [review.history, review.baselineSeconds]);

  const value = useMemo(() => ({ filters, dispatchFilters, review, dispatchReview }), [filters, review]);
  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}
