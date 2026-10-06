import { describe, expect, it } from 'vitest';
import { isPartialWeek, isoWeekStart, niceTicks, summarize, weekLabel } from '../weekly';

const wk = (week: string, postings: number, valid: number) => ({ week, postings, valid, sponsored: 0, sources: 1 });

describe('weekly chart helpers', () => {
  it('maps ISO weeks to their Monday', () => {
    expect(isoWeekStart('2026-W01')?.toISOString().slice(0, 10)).toBe('2025-12-29');
    expect(isoWeekStart('2026-W18')?.toISOString().slice(0, 10)).toBe('2026-04-27');
    expect(weekLabel('2026-W38')).toBe('Sep 14');
    expect(isoWeekStart('bad')).toBeNull();
  });

  it('chooses round axis ticks that cover the maximum', () => {
    expect(niceTicks(4830)).toEqual([0, 2000, 4000, 6000]);
    expect(niceTicks(95)).toEqual([0, 50, 100]);
    expect(niceTicks(0)).toEqual([0]);
  });

  it('summarizes totals, busiest week and recent change', () => {
    const data = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => wk(`2026-W${String(n + 10).padStart(2, '0')}`, n * 100, n * 10));
    const s = summarize(data, new Date('2026-12-01T00:00:00Z'));
    expect(s.postings).toBe(3600);
    expect(s.valid).toBe(360);
    expect(s.busiest?.week).toBe('2026-W18');
    expect(s.recentChange).toBeCloseTo((260 - 100) / 100, 5);
    expect(summarize(data.slice(0, 3)).recentChange).toBeNull();
  });

  it('ignores an in progress week when comparing recent weeks', () => {
    const data = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => wk(`2026-W${String(n + 32).padStart(2, '0')}`, n * 100, n * 10));
    const now = new Date('2026-10-06T12:00:00Z'); // inside 2026-W41
    expect(isPartialWeek('2026-W41', now)).toBe(true);
    expect(isPartialWeek('2026-W40', now)).toBe(false);
    expect(summarize(data, now).recentChange).toBeCloseTo((260 - 100) / 100, 5);
  });
});
