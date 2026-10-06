import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') },
    },
  },
  test: { environment: 'jsdom', setupFiles: ['./src/setupTests.ts'] },
});
