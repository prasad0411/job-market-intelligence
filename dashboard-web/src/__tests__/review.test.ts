import { describe, expect, it } from 'vitest';
import { averageSecondsPerJob, initialReview, reduction, reviewReducer } from '../state/review';

describe('review session reducer', () => {
  it('records a measured session with seconds per posting', () => {
    let s = reviewReducer(initialReview, { type: 'start', now: 1_000 });
    s = reviewReducer(s, { type: 'decide', id: 1, decision: 'shortlist' });
    s = reviewReducer(s, { type: 'decide', id: 2, decision: 'skip' });
    s = reviewReducer(s, { type: 'decide', id: 2, decision: 'shortlist' });
    s = reviewReducer(s, { type: 'stop', now: 21_000 });
    expect(s.active).toBe(false);
    expect(s.history).toHaveLength(1);
    expect(s.history[0]).toMatchObject({ reviewed: 2, elapsedMs: 20_000, secondsPerJob: 10 });
  });

  it('ignores decisions outside a session and discards empty sessions', () => {
    let s = reviewReducer(initialReview, { type: 'decide', id: 1, decision: 'skip' });
    expect(s.decisions).toEqual({});
    s = reviewReducer(reviewReducer(s, { type: 'start', now: 0 }), { type: 'stop', now: 5_000 });
    expect(s.history).toEqual([]);
  });

  it('weights the average by postings and computes the reduction', () => {
    const history = [
      { endedAt: 'a', reviewed: 10, elapsedMs: 100_000, secondsPerJob: 10 },
      { endedAt: 'b', reviewed: 30, elapsedMs: 180_000, secondsPerJob: 6 },
    ];
    expect(averageSecondsPerJob(history)).toBe(7);
    expect(reduction(7, 35)).toBe(0.8);
    expect(reduction(null, 35)).toBeNull();
    expect(reduction(0, 60)).toBe(1);
    expect(averageSecondsPerJob([])).toBeNull();
  });

  it('rejects a zero or negative baseline', () => {
    expect(reviewReducer(initialReview, { type: 'baseline', seconds: 0 }).baselineSeconds).toBeNull();
    expect(reviewReducer(initialReview, { type: 'baseline', seconds: 45 }).baselineSeconds).toBe(45);
  });
});
