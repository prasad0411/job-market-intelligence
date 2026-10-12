import { useEffect, useState } from 'react';
import { pct, seconds } from '../format';
import { selectReview, selectReviewStats, useAppDispatch, useAppSelector } from '../store';
import { setBaseline, startSession, stopSession } from '../store/reviewSlice';

/** Times real review sessions so the review speed claim is measured, not estimated. */
export function ReviewBar() {
  const review = useAppSelector(selectReview);
  const stats = useAppSelector(selectReviewStats);
  const dispatch = useAppDispatch();
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!review.active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [review.active]);

  const reviewed = Object.keys(review.decisions).length;
  const avg = stats.avgSeconds;
  const cut = stats.reduction;
  const sessionJobs = stats.postings;

  return (
    <section className="reviewbar" aria-label="Review session">
      <div className="reviewbar-main">
        {review.active ? (
          <>
            <span className="live-dot" aria-hidden="true" />
            <strong>Reviewing</strong>
            <span>{reviewed} decided</span>
            <span>{seconds(Math.max(0, now - (review.startedAt ?? now)))}</span>
            <span className="keys">J and K move, S shortlists, X skips</span>
            <button type="button" className="primary" onClick={() => dispatch(stopSession(Date.now()))}>End session</button>
          </>
        ) : (
          <>
            <button type="button" className="primary" onClick={() => { setNow(Date.now()); dispatch(startSession(Date.now())); }}>
              Start review session
            </button>
            <span className="muted">Times each decision so review speed is measured on real postings.</span>
          </>
        )}
      </div>
      <dl className="reviewbar-stats">
        <div><dt>Dashboard</dt><dd>{avg !== null ? `${avg}s per posting` : 'No sessions yet'}</dd></div>
        <div>
          <dt><label htmlFor="baseline">Spreadsheet baseline</label></dt>
          <dd>
            <input
              id="baseline"
              type="number"
              min={1}
              step={1}
              placeholder="seconds"
              value={review.baselineSeconds ?? ''}
              onChange={(e) => dispatch(setBaseline(e.target.value === '' ? null : Number(e.target.value)))}
            />
            <span className="muted"> s per posting</span>
          </dd>
        </div>
        <div><dt>Review time reduction</dt><dd data-testid="reduction">{cut !== null ? pct(cut, 0) : 'Needs both numbers'}</dd></div>
        <div><dt>Measured on</dt><dd>{review.history.length} sessions, {sessionJobs} postings</dd></div>
      </dl>
    </section>
  );
}
