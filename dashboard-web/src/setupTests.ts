import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

/** Node 25 exposes an experimental global localStorage that shadows jsdom's and is unusable without a file. */
class MemoryStorage {
  private store = new Map<string, string>();
  get length() { return this.store.size; }
  clear() { this.store.clear(); }
  getItem(key: string) { return this.store.has(key) ? this.store.get(key)! : null; }
  key(i: number) { return [...this.store.keys()][i] ?? null; }
  removeItem(key: string) { this.store.delete(key); }
  setItem(key: string, value: string) { this.store.set(key, String(value)); }
}

function usable(): boolean {
  try {
    const s = globalThis.localStorage;
    s.setItem('__probe__', '1');
    s.removeItem('__probe__');
    return typeof s.clear === 'function';
  } catch {
    return false;
  }
}

if (!usable()) {
  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true, writable: true });
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: true, writable: true });
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  location.hash = '';
});
