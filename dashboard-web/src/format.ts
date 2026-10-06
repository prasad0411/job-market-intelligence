export const num = (n: number) => n.toLocaleString('en-US');
export const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;
export function seconds(ms: number): string {
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}
