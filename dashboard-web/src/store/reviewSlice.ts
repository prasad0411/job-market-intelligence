import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import { initialReview, reviewReducer, type Decision, type ReviewState } from '../state/review';

/** Timed review sessions. The rules live in the pure, separately tested reviewReducer. */
const reviewSlice = createSlice({
  name: 'review',
  initialState: initialReview as ReviewState,
  reducers: {
    startSession: (state, action: PayloadAction<number>) =>
      reviewReducer(state, { type: 'start', now: action.payload }),
    decide: (state, action: PayloadAction<{ id: number; decision: Decision }>) =>
      reviewReducer(state, { type: 'decide', ...action.payload }),
    stopSession: (state, action: PayloadAction<number>) =>
      reviewReducer(state, { type: 'stop', now: action.payload }),
    setBaseline: (state, action: PayloadAction<number | null>) =>
      reviewReducer(state, { type: 'baseline', seconds: action.payload }),
  },
});

export const { startSession, decide, stopSession, setBaseline } = reviewSlice.actions;
export default reviewSlice.reducer;
