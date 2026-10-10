import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { JobQuery } from '../api';
import { filtersReducer, initialFilters } from '../state/filters';

/** Postings filters. The rules live in the pure, separately tested filtersReducer. */
const filtersSlice = createSlice({
  name: 'filters',
  initialState: initialFilters,
  reducers: {
    setFilters: (state, action: PayloadAction<Partial<Omit<JobQuery, 'page'>>>) =>
      filtersReducer(state, { type: 'set', patch: action.payload }),
    setPage: (state, action: PayloadAction<number>) =>
      filtersReducer(state, { type: 'page', page: action.payload }),
    resetFilters: (state) => filtersReducer(state, { type: 'reset' }),
  },
});

export const { setFilters, setPage, resetFilters } = filtersSlice.actions;
export default filtersSlice.reducer;
