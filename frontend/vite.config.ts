import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: path.resolve(__dirname, '../backend/app/static'),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
  // Vitest config inline
  // @ts-ignore
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    // Pre-bundle large many-module libraries once instead of importing ~1,500 icon modules per test file
    // (cuts collect time by ~70%). Adding react-dom and the testing libraries gained little and broke a test.
    deps: {
      optimizer: {
        web: {
          enabled: true,
          include: ['lucide-react', 'recharts'],
        },
      },
    },
  },
});
