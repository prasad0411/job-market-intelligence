import type { ReactNode } from 'react';

interface Props { title: string; subtitle?: string; actions?: ReactNode; children: ReactNode }

export function Panel({ title, subtitle, actions, children }: Props) {
  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2 className="panel-title">{title}</h2>
          {subtitle && <p className="panel-sub">{subtitle}</p>}
        </div>
        {actions}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}
