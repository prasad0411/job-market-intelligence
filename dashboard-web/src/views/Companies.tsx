import { useState } from 'react';
import type { CompanyRow } from '../api';
import { DataTable } from '../components/DataTable';
import { Notice } from '../components/Notice';
import { Panel } from '../components/Panel';
import { num, pct } from '../format';
import { useApi } from '../state/useApi';

export function Companies() {
  const [q, setQ] = useState('');
  const [sponsored, setSponsored] = useState(false);
  const c = useApi<CompanyRow[]>(`/companies?limit=100&q=${encodeURIComponent(q.trim())}${sponsored ? '&sponsored=true' : ''}`);
  return (
    <Panel
      title="Companies"
      subtitle="Hiring activity and H-1B sponsorship by company"
      actions={
        <div className="inline-filters">
          <input type="search" aria-label="Search companies" placeholder="Search companies" value={q} onChange={(e) => setQ(e.target.value)} />
          <label className="check">
            <input type="checkbox" checked={sponsored} onChange={(e) => setSponsored(e.target.checked)} /> Sponsors only
          </label>
        </div>
      }
    >
      <Notice error={c.error} loading={c.loading && !c.data} />
      {c.data && (
        <DataTable
          rows={c.data}
          rowKey={(r) => r.company}
          caption="Companies"
          empty="No companies match"
          columns={[
            { key: 'company', header: 'Company', render: (r) => <strong>{r.company}</strong>, sortValue: (r) => r.company.toLowerCase() },
            { key: 'valid', header: 'Valid postings', align: 'right', render: (r) => num(r.valid), sortValue: (r) => r.valid },
            { key: 'total', header: 'All postings', align: 'right', render: (r) => num(r.total), sortValue: (r) => r.total },
            { key: 'sources', header: 'Sources', align: 'right', render: (r) => num(r.sources), sortValue: (r) => r.sources },
            { key: 'sponsor', header: 'Sponsored share', align: 'right', render: (r) => pct(r.sponsorship_rate, 0), sortValue: (r) => r.sponsorship_rate },
          ]}
        />
      )}
    </Panel>
  );
}
