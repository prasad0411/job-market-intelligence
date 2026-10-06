import type { QuarantineRow, Run } from '../api';
import { ColumnChart } from '../components/ColumnChart';
import { DataTable } from '../components/DataTable';
import { Notice } from '../components/Notice';
import { Panel } from '../components/Panel';
import { StatCard } from '../components/StatCard';
import { num, pct } from '../format';
import { useApi } from '../state/useApi';

function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return 0;
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
}

export function Reliability() {
  const runs = useApi<Run[]>('/runs');
  const quarantine = useApi<QuarantineRow[]>('/quarantine');
  const r = runs.data ?? [];
  const minutes = r.map((x) => x.minutes).sort((a, b) => a - b);
  const longest = r.reduce<Run | null>((m, x) => (!m || x.minutes > m.minutes ? x : m), null);
  const httpFails = r.reduce((n, x) => n + x.failed_http, 0);
  const recent = r.slice(-60);
  return (
    <div className="stack">
      <Notice error={runs.error} loading={runs.loading && !runs.data} />
      {runs.data && (
        <div className="stats">
          <StatCard label="Scheduled runs" value={num(r.length)} note="launchd, three times a day" />
          <StatCard label="Median run" value={`${quantile(minutes, 0.5)} min`} note={`p95 ${quantile(minutes, 0.95)} min`} />
          <StatCard label="Longest run" value={`${longest?.minutes ?? 0} min`} note={longest ? `on ${longest.ts.slice(0, 10)}` : ''} />
          <StatCard label="Job pages with HTTP errors" value={num(httpFails)} note="logged per run; the run continues" />
        </div>
      )}
      {runs.data && (
        <Panel title="Run duration" subtitle="Minutes per run, last 60 runs. Long runs mark incidents; the largest came from a Sheets call that hung until a 60 second timeout was added">
          <ColumnChart data={recent.map((x) => ({ label: x.ts.slice(5, 10), value: x.minutes }))} valueLabel="minutes" height={160} />
        </Panel>
      )}
      <Panel title="Silver gate quarantine" subtitle="Rows held back by data quality rules in the medallion lakehouse, from the dbt fct_rejection_funnel mart. Nothing is deleted; every rejection stays queryable">
        <Notice error={quarantine.error} loading={quarantine.loading && !quarantine.data} />
        {quarantine.data && (
          <DataTable
            rows={quarantine.data}
            rowKey={(q) => q.reason}
            caption="Quarantine reasons"
            columns={[
              { key: 'reason', header: 'Rule', render: (q) => q.reason.replace(/_/g, ' '), sortValue: (q) => q.reason },
              { key: 'family', header: 'Family', render: (q) => q.family, sortValue: (q) => q.family },
              { key: 'rows', header: 'Rows', align: 'right', render: (q) => num(q.rows), sortValue: (q) => q.rows },
              { key: 'share', header: 'Share', align: 'right', render: (q) => pct(q.share), sortValue: (q) => q.share },
            ]}
          />
        )}
      </Panel>
    </div>
  );
}
