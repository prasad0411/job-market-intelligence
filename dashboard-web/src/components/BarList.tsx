import { num } from '../format';

export interface BarItem { label: string; value: number; detail?: string }

export function BarList({ items, unit }: { items: BarItem[]; unit?: string }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="barlist">
      {items.map((i) => (
        <li key={i.label}>
          <span className="barlist-label">{i.label}</span>
          <span className="barlist-track"><span className="barlist-fill" style={{ width: `${(i.value / max) * 100}%` }} /></span>
          <span className="barlist-value">{num(i.value)}{unit ? ` ${unit}` : ''}{i.detail ? <small> {i.detail}</small> : null}</span>
        </li>
      ))}
    </ul>
  );
}
