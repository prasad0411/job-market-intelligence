import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { ErrorBoundary } from '../components/ErrorBoundary';

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error('boom');
  return <p>Market view ready</p>;
}

function Harness() {
  const [explode, setExplode] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setExplode(false)}>Fix</button>
      <ErrorBoundary label="the market view">
        <Bomb explode={explode} />
      </ErrorBoundary>
      <p>Navigation still here</p>
    </>
  );
}

describe('ErrorBoundary', () => {
  it('contains a crash, keeps the rest of the page, and recovers on retry', async () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong while showing the market view');
    expect(screen.getByText('Navigation still here')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Fix' }));
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(screen.getByText('Market view ready')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    quiet.mockRestore();
  });
});
