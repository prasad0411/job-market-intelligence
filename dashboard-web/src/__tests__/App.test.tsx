import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';
import { Provider } from 'react-redux';
import { makeStore } from '../store';

const SUMMARY = { runs: 675, last_run: '2026-10-06', jobs_evaluated: 448200, jobs_evaluated_per_run: 664, avg_run_minutes: 37.2,
  valid_jobs: 1614, sponsored_share: 0.3055, remote_jobs: 240, companies: 3331, sources: 37 };
const JOBS = { total: 2, items: [
  { id: 2, company: 'Stripe', title: 'Software Engineer Intern', location: 'Remote', source: 'SWE List', url: 'https://x/2', job_type: 'Internship', remote: true, sponsored: true, track: 'SDE' },
  { id: 1, company: 'Ramp', title: 'Data Engineer Co-op', location: 'NYC', source: 'SimplifyJobs', url: 'https://x/1', job_type: 'Co-op', remote: false, sponsored: false, track: 'SDE' },
] };
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });

function routeFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const u = String(input);
    if (u.includes('/summary')) return json(SUMMARY);
    if (u.includes('/weekly')) return json([{ week: '2026-W38', postings: 120, valid: 80, sponsored: 20, sources: 7 }]);
    if (u.includes('/funnel')) return json([{ stage: 'season', reason: 'Summer 2027', count: 295 }]);
    if (u.includes('/job-sources')) return json([{ source: 'SWE List', count: 1 }]);
    if (u.includes('/jobs')) return json(JOBS);
    return json([]);
  });
}

const renderApp = () => render(<Provider store={makeStore()}><App /></Provider>);

describe('dashboard', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('shows pipeline KPIs from the API', async () => {
    routeFetch();
    renderApp();
    expect(await screen.findByText('448,200')).toBeInTheDocument();
    expect(screen.getByText('664 per run across 675 runs')).toBeInTheDocument();
    expect(screen.getByText('30.6%')).toBeInTheDocument();
    expect(await screen.findByText('Weekly ingest')).toBeInTheDocument();
    expect(screen.queryByText('Why postings were filtered')).not.toBeInTheDocument();
  });

  it('filters postings through the API', async () => {
    const fetchMock = routeFetch();
    const user = userEvent.setup();
    renderApp();
    await user.click(screen.getByRole('link', { name: 'Postings' }));
    expect(await screen.findByText('2 matching postings')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Software Engineer Intern' })).toHaveAttribute('href', 'https://x/2');
    await user.click(screen.getByLabelText('Sponsors H-1B'));
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('sponsored=true'))).toBe(true);
  });

  it('measures a keyboard review session', async () => {
    routeFetch();
    const user = userEvent.setup();
    renderApp();
    await user.click(screen.getByRole('link', { name: 'Postings' }));
    await screen.findByText('2 matching postings');
    await user.click(screen.getByRole('button', { name: 'Start review session' }));
    await user.keyboard('s');
    await user.keyboard('x');
    const bar = screen.getByRole('region', { name: 'Review session' });
    expect(within(bar).getByText('2 decided')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'End session' }));
    expect(within(bar).getByText('1 sessions, 2 postings')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Spreadsheet baseline'), '60');
    expect(screen.getByTestId('reduction').textContent).toMatch(/^\d+%$/);
  });

  it('explains how to start the API when it is down', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('x', { status: 502 }));
    renderApp();
    expect((await screen.findAllByRole('alert'))[0]).toHaveTextContent('uvicorn dashboard_api.app:app --port 8001');
  });
});
