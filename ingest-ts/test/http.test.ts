import { describe, expect, it, vi } from 'vitest';
import { fetchJson, HttpError } from '../src/http.js';

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

describe('fetchJson', () => {
  it('retries 429 and 5xx, then succeeds', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(new Response('', { status: 429 }))
      .mockResolvedValueOnce(new Response('', { status: 503 }))
      .mockResolvedValueOnce(ok({ fine: true }));
    await expect(fetchJson('https://x', { fetchImpl, backoffMs: 1 })).resolves.toEqual({ fine: true });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it('does not retry a 404', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response('', { status: 404 }));
    await expect(fetchJson('https://x', { fetchImpl, backoffMs: 1 })).rejects.toBeInstanceOf(HttpError);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('gives up after the retry budget on network errors', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('fetch failed'));
    await expect(fetchJson('https://x', { fetchImpl, retries: 2, backoffMs: 1 })).rejects.toThrow('fetch failed');
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });
});
