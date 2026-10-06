import { useState } from 'react';
import type { WeekPoint } from '../api';
import { num, pct } from '../format';
import { isPartialWeek, niceTicks, summarize, weekLabel } from '../weekly';

const W = 960;
const H = 280;
const PAD = { top: 16, right: 12, bottom: 34, left: 52 };

/** Stacked weekly bars: postings that passed every filter, and the rest that were filtered out. */
const RANGES = [
  { id: '12', label: '12 weeks', weeks: 12 },
  { id: '26', label: '26 weeks', weeks: 26 },
  { id: 'all', label: 'All', weeks: Infinity },
] as const;

export function WeeklyChart({ data: all }: { data: WeekPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [range, setRange] = useState<(typeof RANGES)[number]['id']>('12');
  const weeks = RANGES.find((r) => r.id === range)!.weeks;
  const data = Number.isFinite(weeks) ? all.slice(-weeks) : all;
  const s = summarize(data);
  const ticks = niceTicks(Math.max(1, ...data.map((d) => d.postings)));
  const top = ticks[ticks.length - 1] || 1;
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const band = plotW / Math.max(1, data.length);
  const bar = Math.max(4, band * 0.68);
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH;
  const labelEvery = Math.max(1, Math.ceil(data.length / 8));
  const h = hover !== null ? data[hover] : null;

  return (
    <div className="weekly">
      <div className="weekly-head">
      <dl className="weekly-summary">
        <div><dt>Collected</dt><dd>{num(s.postings)}</dd></div>
        <div><dt>Passed every filter</dt><dd>{num(s.valid)} <small>{pct(s.validRate)}</small></dd></div>
        {s.busiest && <div><dt>Busiest week</dt><dd>{weekLabel(s.busiest.week)} <small>{num(s.busiest.postings)} postings</small></dd></div>}
        {s.recentChange !== null && (
          <div>
            <dt>Valid, last 4 full weeks vs prior 4</dt>
            <dd className={s.recentChange >= 0 ? 'up' : 'down'}>{s.recentChange >= 0 ? '+' : '\u2212'}{pct(Math.abs(s.recentChange), 0)}</dd>
          </div>
        )}
      </dl>
        <div className="segmented" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button key={r.id} type="button" aria-pressed={range === r.id} onClick={() => { setRange(r.id); setHover(null); }}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <div className="weekly-plot" onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Weekly postings from ${weekLabel(data[0]?.week ?? '')} to ${weekLabel(data[data.length - 1]?.week ?? '')}`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
              <text x={PAD.left - 8} y={y(t)} className="axis" textAnchor="end" dominantBaseline="middle">{num(t)}</text>
            </g>
          ))}
          {data.map((d, i) => {
            const x = PAD.left + i * band + (band - bar) / 2;
            return (
              <g key={`${i}-${d.week}`} className={[hover === i ? 'active' : '', isPartialWeek(d.week) ? 'partial' : ''].join(' ').trim() || undefined}>
                <rect x={x} y={y(d.postings)} width={bar} height={Math.max(0, y(d.valid) - y(d.postings))} rx={2} className="seg-filtered" />
                <rect x={x} y={y(d.valid)} width={bar} height={Math.max(0, PAD.top + plotH - y(d.valid))} rx={2} className="seg-valid" />
                {i % labelEvery === 0 && (
                  <text x={x + bar / 2} y={H - 10} className="axis" textAnchor="middle">{weekLabel(d.week)}</text>
                )}
                <rect
                  x={PAD.left + i * band} y={PAD.top} width={band} height={plotH} className="hit"
                  tabIndex={0}
                  aria-label={`Week of ${weekLabel(d.week)}: ${num(d.postings)} postings, ${num(d.valid)} passed every filter`}
                  onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}
                />
              </g>
            );
          })}
        </svg>
        {h && hover !== null && (
          <div className="weekly-tip" role="status" style={{ left: `${((PAD.left + (hover + 0.5) * band) / W) * 100}%` }}>
            <strong>Week of {weekLabel(h.week)}{isPartialWeek(h.week) ? ' (in progress)' : ''}</strong>
            <span>{num(h.postings)} postings collected</span>
            <span>{num(h.valid)} passed every filter ({pct(h.postings ? h.valid / h.postings : 0)})</span>
            <span>{num(h.sponsored)} with confirmed sponsorship</span>
            <span>{num(h.sources)} sources active</span>
          </div>
        )}
      </div>
      <p className="legend">
        <span><i className="swatch valid" /> passed every filter</span>
        <span><i className="swatch filtered" /> filtered out</span>
      </p>
    </div>
  );
}
