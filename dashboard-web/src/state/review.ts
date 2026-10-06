export type Decision = 'shortlist' | 'skip';

export interface SessionResult { endedAt: string; reviewed: number; elapsedMs: number; secondsPerJob: number }

export interface ReviewState {
  active: boolean;
  startedAt: number | null;
  decisions: Record<number, Decision>;
  history: SessionResult[];
  baselineSeconds: number | null;
}

export const initialReview: ReviewState = { active: false, startedAt: null, decisions: {}, history: [], baselineSeconds: null };

export type ReviewAction =
  | { type: 'start'; now: number }
  | { type: 'decide'; id: number; decision: Decision }
  | { type: 'stop'; now: number }
  | { type: 'baseline'; seconds: number | null }
  | { type: 'load'; history: SessionResult[]; baselineSeconds: number | null };

export function reviewReducer(state: ReviewState, action: ReviewAction): ReviewState {
  switch (action.type) {
    case 'start':
      return { ...state, active: true, startedAt: action.now, decisions: {} };
    case 'decide':
      return state.active ? { ...state, decisions: { ...state.decisions, [action.id]: action.decision } } : state;
    case 'stop': {
      if (!state.active || state.startedAt === null) return state;
      const reviewed = Object.keys(state.decisions).length;
      const elapsedMs = Math.max(0, action.now - state.startedAt);
      const next = { ...state, active: false, startedAt: null };
      if (reviewed === 0) return next;
      const result: SessionResult = {
        endedAt: new Date(action.now).toISOString(),
        reviewed,
        elapsedMs,
        secondsPerJob: Math.round((elapsedMs / 1000 / reviewed) * 10) / 10,
      };
      return { ...next, history: [result, ...state.history].slice(0, 50) };
    }
    case 'baseline':
      return { ...state, baselineSeconds: action.seconds && action.seconds > 0 ? action.seconds : null };
    case 'load':
      return { ...state, history: action.history, baselineSeconds: action.baselineSeconds };
  }
}

/** Mean seconds per job across saved sessions, weighted by jobs reviewed. */
export function averageSecondsPerJob(history: SessionResult[]): number | null {
  const jobs = history.reduce((n, h) => n + h.reviewed, 0);
  if (jobs === 0) return null;
  return Math.round((history.reduce((t, h) => t + h.elapsedMs, 0) / 1000 / jobs) * 10) / 10;
}

export function reduction(dashboardSeconds: number | null, baselineSeconds: number | null): number | null {
  if (dashboardSeconds === null || !baselineSeconds) return null;
  return Math.round((1 - dashboardSeconds / baselineSeconds) * 1000) / 1000;
}
