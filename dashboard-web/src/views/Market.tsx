import type { Insights } from '../api';
import { BarList } from '../components/BarList';
import { DataTable } from '../components/DataTable';
import { Notice } from '../components/Notice';
import { Panel } from '../components/Panel';
import { StatCard } from '../components/StatCard';
import { num, pct } from '../format';
import { useApi } from '../state/useApi';

export function Market() {
  const r = useApi<Insights>('/insights');
  const d = r.data;
  return (
    <div className="stack">
      <Notice error={r.error} loading={r.loading && !d} />
      {d && (
        <>
          <div className="stats">
            <StatCard label="Early career postings" value={num(d.valid_postings)} note="internship, new grad and co-op roles" />
            <StatCard label="Companies hiring" value={num(d.companies_total)} />
            <StatCard label="Companies that sponsor H-1B" value={num(d.companies_sponsoring)} note={`${pct(d.companies_sponsoring / Math.max(1, d.companies_total))} of companies tracked`} />
            <StatCard label="Remote postings" value={pct(d.remote_share)} />
          </div>
          <div className="grid-3">
            <Panel title="Role type" subtitle="Valid postings by employment type">
              <BarList items={d.roles.map((x) => ({ label: x.label, value: x.count }))} />
            </Panel>
            <Panel title="Career track" subtitle="Which resume each posting is matched to">
              <BarList items={d.tracks.map((x) => ({ label: x.label, value: x.count }))} />
            </Panel>
            <Panel title="Where the jobs are" subtitle="Top states, plus remote">
              <BarList items={d.states.map((x) => ({ label: x.label, value: x.count }))} />
            </Panel>
          </div>
          <div className="grid-2">
            <Panel title="Who is hiring most" subtitle="Valid postings per company, from the dbt dim_company mart">
              <DataTable
                rows={d.top_hiring}
                rowKey={(c) => c.company}
                caption="Top hiring companies"
                columns={[
                  { key: 'company', header: 'Company', render: (c) => <strong>{c.company}</strong> },
                  { key: 'valid', header: 'Valid postings', align: 'right', render: (c) => num(c.valid), sortValue: (c) => c.valid },
                  { key: 'sources', header: 'Sources', align: 'right', render: (c) => num(c.sources), sortValue: (c) => c.sources },
                  { key: 'sp', header: 'Sponsored', align: 'right', render: (c) => pct(c.sponsorship_rate, 0), sortValue: (c) => c.sponsorship_rate },
                ]}
              />
            </Panel>
            <Panel title="Top H-1B sponsors" subtitle="Companies with the most postings confirmed against USCIS approvals">
              <BarList items={d.top_sponsors.map((c) => ({ label: c.company, value: c.sponsored, detail: `of ${num(c.valid)}` }))} />
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
