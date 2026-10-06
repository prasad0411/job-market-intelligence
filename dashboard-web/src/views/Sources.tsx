import type { QuarantineRow, SourceRow } from '../api';
import { BarList } from '../components/BarList';
import { DataTable } from '../components/DataTable';
import { Notice } from '../components/Notice';
import { Panel } from '../components/Panel';
import { num, pct } from '../format';
import { useApi } from '../state/useApi';

export function Sources() {
  const s = useApi<SourceRow[]>('/sources');
  const q = useApi<QuarantineRow[]>('/quarantine');
  return (
    <div className="stack">
    <Panel title="Source quality" subtitle="Rows accepted at the Silver gate versus quarantined, from the dbt fct_source_quality mart">
      <Notice error={s.error} loading={s.loading && !s.data} />
      {s.data && (
        <DataTable
          rows={s.data}
          rowKey={(r) => r.source}
          caption="Source quality"
          columns={[
            { key: 'source', header: 'Source', render: (r) => r.source, sortValue: (r) => r.source },
            { key: 'total', header: 'Rows', align: 'right', render: (r) => num(r.total), sortValue: (r) => r.total },
            { key: 'accepted', header: 'Accepted', align: 'right', render: (r) => num(r.accepted), sortValue: (r) => r.accepted },
            { key: 'rejected', header: 'Quarantined', align: 'right', render: (r) => num(r.rejected), sortValue: (r) => r.rejected },
            {
              key: 'yield', header: 'Yield', align: 'right', sortValue: (r) => r.yield_rate,
              render: (r) => <span className={r.yield_rate < 0.8 ? 'warn' : undefined}>{pct(r.yield_rate)}</span>,
            },
          ]}
        />
      )}
    </Panel>
    <Panel title="Data quality gate" subtitle="Rows held back at the Silver layer of the lakehouse, by rule. Nothing is deleted; every rejection stays queryable">
      <Notice error={q.error} loading={q.loading && !q.data} />
      {q.data && (
        <BarList items={q.data.map((r) => ({ label: r.reason.replace(/_/g, ' '), value: r.rows, detail: pct(r.share) }))} />
      )}
    </Panel>
    </div>
  );
}
