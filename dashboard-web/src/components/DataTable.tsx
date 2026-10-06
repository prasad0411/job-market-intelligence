import { useMemo, useState, type ReactNode } from 'react';

export interface ColumnDef<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  sortValue?: (row: T) => number | string;
  align?: 'left' | 'right';
}

interface Props<T> {
  rows: T[];
  columns: ColumnDef<T>[];
  rowKey: (row: T) => string | number;
  caption: string;
  activeKey?: string | number | null;
  empty?: string;
}

/** Generic accessible table with client side sorting on any column that defines sortValue. */
export function DataTable<T>({ rows, columns, rowKey, caption, activeKey, empty = 'No rows' }: Props<T>) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort?.key);
    if (!col?.sortValue || !sort) return rows;
    const get = col.sortValue;
    return [...rows].sort((a, b) => (get(a) > get(b) ? 1 : get(a) < get(b) ? -1 : 0) * sort.dir);
  }, [rows, columns, sort]);

  return (
    <div className="table-scroll">
      <table className="datatable">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => {
              const dir = sort?.key === c.key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none';
              return (
                <th key={c.key} scope="col" className={c.align === 'right' ? 'num' : undefined} aria-sort={c.sortValue ? dir : undefined}>
                  {c.sortValue ? (
                    <button type="button" className="sort" onClick={() => setSort((s) => ({ key: c.key, dir: s?.key === c.key && s.dir === -1 ? 1 : -1 }))}>
                      {c.header}
                      <span aria-hidden="true">{dir === 'ascending' ? ' \u25b2' : dir === 'descending' ? ' \u25bc' : ''}</span>
                    </button>
                  ) : c.header}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr><td colSpan={columns.length} className="empty">{empty}</td></tr>
          ) : (
            sorted.map((r) => (
              <tr key={rowKey(r)} className={activeKey === rowKey(r) ? 'active' : undefined}>
                {columns.map((c) => (
                  <td key={c.key} className={c.align === 'right' ? 'num' : undefined}>{c.render(r)}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
