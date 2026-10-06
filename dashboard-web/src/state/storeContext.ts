import { createContext } from 'react';
import type { JobQuery } from '../api';
import type { FilterAction } from './filters';
import type { ReviewAction, ReviewState } from './review';

export interface Store {
  filters: JobQuery;
  dispatchFilters: (a: FilterAction) => void;
  review: ReviewState;
  dispatchReview: (a: ReviewAction) => void;
}

export const StoreContext = createContext<Store | null>(null);
export const REVIEW_KEY = 'jmi.review.v1';
