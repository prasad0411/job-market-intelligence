interface Props { label: string; value: string; note?: string }

export function StatCard({ label, value, note }: Props) {
  return (
    <div className="stat">
      <p className="stat-label">{label}</p>
      <p className="stat-value">{value}</p>
      {note && <p className="stat-note">{note}</p>}
    </div>
  );
}
