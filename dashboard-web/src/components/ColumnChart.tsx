import { num } from '../format';

export interface Column { label: string; value: number; secondary?: number }

interface Props { data: Column[]; valueLabel: string; secondaryLabel?: string; height?: number }

/** Vertical bars with an optional overlaid secondary series, labelled for screen readers. */
export function ColumnChart({ data, valueLabel, secondaryLabel, height = 180 }: Props) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <figure className="colchart" aria-label={`${valueLabel} by period`}>
      <div className="colchart-plot" style={{ height }}>
        {data.map((d) => (
          <div key={d.label} className="col" title={`${d.label}: ${num(d.value)} ${valueLabel}${d.secondary !== undefined && secondaryLabel ? `, ${num(d.secondary)} ${secondaryLabel}` : ''}`}>
            <span className="col-bar" style={{ height: `${(d.value / max) * 100}%` }} />
            {d.secondary !== undefined && <span className="col-bar col-secondary" style={{ height: `${(d.secondary / max) * 100}%` }} />}
          </div>
        ))}
      </div>
      <div className="colchart-axis" aria-hidden="true">
        <span>{data[0]?.label}</span>
        <span>{data[data.length - 1]?.label}</span>
      </div>
      <figcaption className="legend">
        <span><i className="swatch primary" /> {valueLabel}</span>
        {secondaryLabel && <span><i className="swatch secondary" /> {secondaryLabel}</span>}
      </figcaption>
    </figure>
  );
}
