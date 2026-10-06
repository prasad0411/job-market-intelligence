import type { FunnelRow, Stage, Summary, WeekPoint } from '../api';
import { BarList } from '../components/BarList';
import { ColumnChart } from '../components/ColumnChart';
import { FunnelBar } from '../components/FunnelBar';
import { Notice } from '../components/Notice';
import { Panel } from '../components/Panel';
import { StatCard } from '../components/StatCard';
import { num, pct } from '../format';
import { useApi } from '../state/useApi';

export function Overview() {
  const summary = useApi<Summary>('/summary');
  const weekly = useApi<WeekPoint[]>('/weekly');
  const funnel = useApi<FunnelRow[]>('/funnel?days=30');
  const pipeline = useApi<Stage[]>('/pipeline');
  const s = summary.data;
  return (
    <div className="stack">
      <Notice error={summary.error} loading={summary.loading && !s} />
      {s && (
        <div className="stats">
          <StatCard label="Postings evaluated" value={num(s.jobs_evaluated)} note={`${num(s.jobs_evaluated_per_run)} per run across ${num(s.runs)} runs`} />
          <StatCard label="Valid postings" value={num(s.valid_jobs)} note={`${num(s.remote_jobs)} remote`} />
          <StatCard label="Sponsorship confirmed" value={pct(s.sponsored_share)} note="USCIS H-1B approvals" />
          <StatCard label="Companies tracked" value={num(s.companies)} note={`${num(s.sources)} sources`} />
          <StatCard label="Average run" value={`${s.avg_run_minutes} min`} note="fully automated, 3 runs a day" />
        </div>
      )}
      <Panel title="Where every posting went" subtitle="All postings the pipeline has evaluated, by the first gate that stopped them. Cheapest checks run first">
        <Notice error={pipeline.error} loading={pipeline.loading && !pipeline.data} />
        {pipeline.data && <FunnelBar stages={pipeline.data} />}
      </Panel>
      <div className="grid-2">
        <Panel title="Weekly ingest" subtitle="Postings landed per ISO week, from the dbt fct_weekly_ingest mart">
          <Notice error={weekly.error} loading={weekly.loading && !weekly.data} />
          {weekly.data && (
            <ColumnChart
              data={weekly.data.map((w) => ({ label: w.week, value: w.postings, secondary: w.valid }))}
              valueLabel="postings"
              secondaryLabel="valid"
            />
          )}
        </Panel>
        <Panel title="Why postings were filtered" subtitle="Last 30 days, by rule">
          <Notice error={funnel.error} loading={funnel.loading && !funnel.data} />
          {funnel.data && <BarList items={funnel.data.map((f) => ({ label: f.reason, value: f.count, detail: f.stage }))} />}
        </Panel>
      </div>
    </div>
  );
}
