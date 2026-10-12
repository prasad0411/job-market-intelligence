import type { Stage, Summary, WeekPoint } from '../api';
import { WeeklyChart } from '../components/WeeklyChart';
import { FunnelBar } from '../components/FunnelBar';
import { Notice } from '../components/Notice';
import { Panel } from '../components/Panel';
import { StatCard } from '../components/StatCard';
import { num, pct } from '../format';
import { useApi } from '../state/useApi';

export function Overview() {
  const summary = useApi<Summary>('/summary');
  const weekly = useApi<WeekPoint[]>('/weekly');
  const pipeline = useApi<Stage[]>('/pipeline');
  const written = pipeline.data?.find((p) => p.key === 'valid')?.count ?? null;
  const s = summary.data;
  return (
    <div className="stack">
      <Notice error={summary.error} loading={summary.loading && !s} />
      {s && (
        <div className="stats">
          <StatCard label="Postings evaluated" value={num(s.jobs_evaluated)} note={`${num(s.jobs_evaluated_per_run)} per run across ${num(s.runs)} runs`} />
          <StatCard label="Noise filtered automatically" value={written !== null ? pct(1 - written / Math.max(1, s.jobs_evaluated)) : 'n/a'} note="duplicates, wrong season, non tech, senior, non US" />
          <StatCard label="Valid postings" value={num(s.valid_jobs)} note={`${num(s.remote_jobs)} remote`} />
          <StatCard label="Sponsorship confirmed" value={pct(s.sponsored_share)} note="of valid postings, from USCIS H-1B approvals" />
          <StatCard label="Companies tracked" value={num(s.companies)} note={`${num(s.sources)} sources`} />
          <StatCard label="Median run" value={`${s.median_run_minutes} min`} note="fully automated, 3 runs a day" />
        </div>
      )}
      <Panel title="Where every posting went" subtitle="All postings the pipeline has evaluated, by the first gate that stopped them. Cheapest checks run first">
        <Notice error={pipeline.error} loading={pipeline.loading && !pipeline.data} />
        {pipeline.data && <FunnelBar stages={pipeline.data} />}
      </Panel>
      <Panel title="Weekly ingest">
        <Notice error={weekly.error} loading={weekly.loading && !weekly.data} />
        {weekly.data && <WeeklyChart data={weekly.data} />}
      </Panel>
    </div>
  );
}
