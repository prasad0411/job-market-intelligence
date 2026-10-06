export function Notice({ error, loading }: { error: string | null; loading: boolean }) {
  if (error) return <p className="notice notice-error" role="alert">{error}</p>;
  if (loading) return <p className="notice" aria-busy="true">Loading</p>;
  return null;
}
