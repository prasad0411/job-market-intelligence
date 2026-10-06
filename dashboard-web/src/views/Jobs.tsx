import { useCallback, useEffect, useState } from 'react';
import { PAGE_SIZE, STATIC_MODE, jobsPath, type Job, type JobPage } from '../api';
import { DataTable, type ColumnDef } from '../components/DataTable';
import { Notice } from '../components/Notice';
import { ReviewBar } from '../components/ReviewBar';
import { num } from '../format';
import { useApi } from '../state/useApi';
import { useStore } from '../state/useStore';

const TYPES = ['Internship', 'Full Time', 'Co-op'];

export function Jobs() {
  const { filters, dispatchFilters, review, dispatchReview } = useStore();
  const page = useApi<JobPage>(jobsPath(filters));
  const sources = useApi<{ source: string; count: number }[]>('/job-sources');
  const [cursor, setCursor] = useState(0);
  const items = page.data?.items ?? [];
  const activeId = review.active ? items[Math.min(cursor, items.length - 1)]?.id ?? null : null;

  const decide = useCallback((id: number, decision: 'shortlist' | 'skip') => {
    dispatchReview({ type: 'decide', id, decision });
    setCursor((c) => Math.min(c + 1, Math.max(0, items.length - 1)));
  }, [dispatchReview, items.length]);

  useEffect(() => {
    if (!review.active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === 'j') setCursor((c) => Math.min(c + 1, items.length - 1));
      else if (e.key === 'k') setCursor((c) => Math.max(c - 1, 0));
      else if ((e.key === 's' || e.key === 'x') && activeId !== null) decide(activeId, e.key === 's' ? 'shortlist' : 'skip');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [review.active, items.length, activeId, decide]);

  const columns: ColumnDef<Job>[] = [
    { key: 'company', header: 'Company', render: (j) => <strong>{j.company}</strong>, sortValue: (j) => j.company.toLowerCase() },
    { key: 'title', header: 'Role', render: (j) => <a href={j.url} target="_blank" rel="noreferrer">{j.title}</a>, sortValue: (j) => j.title.toLowerCase() },
    { key: 'location', header: 'Location', render: (j) => j.location },
    { key: 'type', header: 'Type', render: (j) => j.job_type, sortValue: (j) => j.job_type },
    {
      key: 'tags', header: 'Signals', render: (j) => (
        <span className="tags">
          {j.sponsored && <span className="tag tag-good">Sponsors H-1B</span>}
          {j.remote && <span className="tag">Remote</span>}
          <span className="tag tag-muted">{j.source}</span>
        </span>
      ),
    },
  ];
  if (review.active) {
    columns.push({
      key: 'decide', header: 'Decision', render: (j) => {
        const d = review.decisions[j.id];
        return (
          <span className="decide">
            <button type="button" aria-pressed={d === 'shortlist'} onClick={() => decide(j.id, 'shortlist')}>Shortlist</button>
            <button type="button" aria-pressed={d === 'skip'} onClick={() => decide(j.id, 'skip')}>Skip</button>
          </span>
        );
      },
    });
  }

  const total = page.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <div className="stack">
      {!STATIC_MODE && <ReviewBar />}
      <form className="filters" role="search" onSubmit={(e) => e.preventDefault()}>
        <label className="field grow">
          <span>Search</span>
          <input type="search" placeholder="Company or role" value={filters.q} onChange={(e) => dispatchFilters({ type: 'set', patch: { q: e.target.value } })} />
        </label>
        <label className="field">
          <span>Source</span>
          <select value={filters.source} onChange={(e) => dispatchFilters({ type: 'set', patch: { source: e.target.value } })}>
            <option value="">All sources</option>
            {sources.data?.map((s) => <option key={s.source} value={s.source}>{s.source} ({num(s.count)})</option>)}
          </select>
        </label>
        <label className="field">
          <span>Type</span>
          <select value={filters.jobType} onChange={(e) => dispatchFilters({ type: 'set', patch: { jobType: e.target.value } })}>
            <option value="">All types</option>
            {TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={filters.sponsored} onChange={(e) => dispatchFilters({ type: 'set', patch: { sponsored: e.target.checked } })} />
          Sponsors H-1B
        </label>
        <label className="check">
          <input type="checkbox" checked={filters.remote} onChange={(e) => dispatchFilters({ type: 'set', patch: { remote: e.target.checked } })} />
          Remote
        </label>
        <button type="button" onClick={() => dispatchFilters({ type: 'reset' })}>Clear</button>
      </form>
      <Notice error={page.error} loading={page.loading && !page.data} />
      <p className="result-count" aria-live="polite">{page.data ? `${num(total)} matching postings` : '\u00a0'}</p>
      {page.data && (
        <DataTable rows={items} columns={columns} rowKey={(j) => j.id} caption="Valid postings" activeKey={activeId} empty="No postings match these filters" />
      )}
      <nav className="pager" aria-label="Pages">
        <button type="button" disabled={filters.page === 0} onClick={() => dispatchFilters({ type: 'page', page: filters.page - 1 })}>Previous</button>
        <span>Page {filters.page + 1} of {num(pages)}</span>
        <button type="button" disabled={filters.page + 1 >= pages} onClick={() => dispatchFilters({ type: 'page', page: filters.page + 1 })}>Next</button>
      </nav>
    </div>
  );
}
