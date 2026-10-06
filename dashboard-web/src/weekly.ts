import type { WeekPoint } from './api';

/** Monday of an ISO week such as "2026-W18", in UTC. */
export function isoWeekStart(week: string): Date | null {
  const m = /^(\d{4})-W(\d{2})$/.exec(week);
  if (!m) return null;
  const year = Number(m[1]);
  const w = Number(m[2]);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const day = jan4.getUTCDay() || 7;
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - day + 1 + (w - 1) * 7);
  return monday;
}

export function weekLabel(week: string): string {
  const d = isoWeekStart(week);
  return d ? d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) : week;
}

/** Round axis maximum and evenly spaced ticks (1, 2 or 5 times a power of ten). */
export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? 10 * pow;
  const ticks: number[] = [];
  for (let v = 0; v <= max + step * 0.999; v += step) ticks.push(Math.round(v));
  return ticks;
}

export interface WeeklySummary {
  postings: number;
  valid: number;
  validRate: number;
  busiest: WeekPoint | null;
  recentChange: number | null;
}

/** Totals, busiest week, and last four weeks against the four before them. */
export function summarize(data: WeekPoint[]): WeeklySummary {
  const postings = data.reduce((n, w) => n + w.postings, 0);
  const valid = data.reduce((n, w) => n + w.valid, 0);
  const busiest = data.reduce<WeekPoint | null>((b, w) => (!b || w.postings > b.postings ? w : b), null);
  let recentChange: number | null = null;
  if (data.length >= 8) {
    const sum = (xs: WeekPoint[]) => xs.reduce((n, w) => n + w.valid, 0);
    const last = sum(data.slice(-4));
    const prev = sum(data.slice(-8, -4));
    recentChange = prev > 0 ? (last - prev) / prev : null;
  }
  return { postings, valid, validRate: postings ? valid / postings : 0, busiest, recentChange };
}
