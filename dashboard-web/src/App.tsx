import { useEffect, useState } from 'react';
import { Companies } from './views/Companies';
import { Jobs } from './views/Jobs';
import { Overview } from './views/Overview';
import { Reliability } from './views/Reliability';
import { STATIC_MODE, type Meta } from './api';
import { useApi } from './state/useApi';
import { Sources } from './views/Sources';

const TABS = [
  { id: 'overview', label: 'Overview', view: Overview },
  { id: 'jobs', label: 'Postings', view: Jobs },
  { id: 'reliability', label: 'Reliability', view: Reliability },
  { id: 'sources', label: 'Sources', view: Sources },
  { id: 'companies', label: 'Companies', view: Companies },
] as const;
type TabId = (typeof TABS)[number]['id'];

const fromHash = (): TabId => {
  const h = typeof location !== 'undefined' ? location.hash.slice(1) : '';
  return (TABS.find((t) => t.id === h)?.id ?? 'overview') as TabId;
};

export default function App() {
  const [tab, setTab] = useState<TabId>(fromHash);
  useEffect(() => {
    const onHash = () => setTab(fromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const View = TABS.find((t) => t.id === tab)!.view;
  const meta = useApi<Meta>(STATIC_MODE ? '/meta' : null);
  return (
    <div className="app">
      <header className="appbar">
        <div className="appbar-inner">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">JM</span>
            <div>
              <p className="brand-name">Job Market Intelligence</p>
              <p className="brand-sub">Automated early career job pipeline</p>
            </div>
          </div>
          <nav className="tabs" aria-label="Sections">
            {TABS.map((t) => (
              <a key={t.id} href={`#${t.id}`} aria-current={tab === t.id ? 'page' : undefined} onClick={() => setTab(t.id)}>
                {t.label}
              </a>
            ))}
          </nav>
        </div>
      </header>
      <main className="page">
        {STATIC_MODE && meta.data && (
          <p className="snapshot">
            Live snapshot of the pipeline, updated {new Date(meta.data.generated_at).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })}.{' '}
            <a href="https://github.com/prasad0411/job-market-intelligence">Source and architecture on GitHub</a>
          </p>
        )}
        <View />
      </main>
    </div>
  );
}
