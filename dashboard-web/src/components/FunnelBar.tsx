import type { Stage } from '../api';
import { num, pct } from '../format';

const COLORS = ['#94a3b8', '#a8b4c6', '#f59e0b', '#fbbf24', '#fb923c', '#f97316', '#ef4444', '#dc2626', '#a855f7', '#10b981'];

/** One bar showing where every evaluated posting went, with an accessible legend. */
export function FunnelBar({ stages }: { stages: Stage[] }) {
  const total = stages.reduce((n, s) => n + s.count, 0) || 1;
  return (
    <figure className="funnel">
      <div className="funnel-bar" role="img" aria-label={stages.map((s) => `${s.stage} ${pct(s.count / total)}`).join(', ')}>
        {stages.map((s, i) => (
          <span key={s.key} className="funnel-seg" style={{ width: `${(s.count / total) * 100}%`, background: COLORS[i % COLORS.length] }} title={`${s.stage}: ${num(s.count)}`} />
        ))}
      </div>
      <ul className="funnel-legend">
        {stages.map((s, i) => (
          <li key={s.key} className={s.key === 'valid' ? 'funnel-valid' : undefined}>
            <i style={{ background: COLORS[i % COLORS.length] }} />
            <span>{s.stage}</span>
            <strong>{num(s.count)}</strong>
            <small>{pct(s.count / total)}</small>
          </li>
        ))}
      </ul>
    </figure>
  );
}
