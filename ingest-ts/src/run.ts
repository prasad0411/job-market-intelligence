import type { FetchJsonOptions } from './http.js';
import { fetchRemotive, REMOTIVE_SOURCE } from './sources/remotive.js';
import { fetchTheMuse, THEMUSE_SOURCE } from './sources/themuse.js';
import type { JobRecord, SourceReport } from './types.js';

export type SourceFn = (http: FetchJsonOptions) => Promise<JobRecord[]>;

export const SOURCES: Record<string, SourceFn> = {
  [THEMUSE_SOURCE]: (http) => fetchTheMuse(5, http),
  [REMOTIVE_SOURCE]: (http) => fetchRemotive(http),
};

/** Runs every source concurrently. One failing source never stops the others (fail open). */
export async function runAll(
  sources: Record<string, SourceFn> = SOURCES,
  http: FetchJsonOptions = {},
): Promise<{ records: JobRecord[]; reports: SourceReport[] }> {
  const names = Object.keys(sources);
  const started = names.map(() => Date.now());
  const settled = await Promise.allSettled(names.map((n) => sources[n](http)));
  const seen = new Set<string>();
  const records: JobRecord[] = [];
  const reports: SourceReport[] = settled.map((s, i) => {
    const ms = Date.now() - started[i];
    if (s.status === 'rejected') {
      return { source: names[i], records: 0, error: s.reason instanceof Error ? s.reason.message : String(s.reason), ms };
    }
    let kept = 0;
    for (const r of s.value) {
      if (seen.has(r.url)) continue;
      seen.add(r.url);
      records.push(r);
      kept++;
    }
    return { source: names[i], records: kept, error: null, ms };
  });
  return { records, reports };
}
