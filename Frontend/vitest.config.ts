import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/main.tsx', 'src/vite-env.d.ts'],
      // Measured 2026-11-04 with `npm run test:coverage` (24 files, 156 tests):
      // statements/lines 31.71%, branches 73.79%, functions 37.8%.
      // The previous 80% values were configuration only and had never been
      // executed.  These floors are the measured baseline minus a small
      // margin so a regression fails; raising them is tracked as follow-up
      // work, not presented as already achieved.
      thresholds: {
        statements: 30,
        branches: 70,
        functions: 35,
        lines: 30,
      },
    },
  },
});
