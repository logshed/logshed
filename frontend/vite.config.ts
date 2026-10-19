import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'fs';
import path from 'path';

const outDir = path.resolve(__dirname, '../backend/app/static');

// Swagger UI and ReDoc for the backend's /docs and /redoc pages, served from /docs-assets/
// instead of a CDN. Copied from node_modules after each build (the backend picks the URLs).
const DOCS_ASSETS: Array<[string, string]> = [
  ['swagger-ui-dist/swagger-ui-bundle.js', 'swagger-ui-bundle.js'],
  ['swagger-ui-dist/swagger-ui-bundle.js.LICENSE.txt', 'swagger-ui-bundle.js.LICENSE.txt'],
  ['swagger-ui-dist/swagger-ui.css', 'swagger-ui.css'],
  ['swagger-ui-dist/LICENSE', 'swagger-ui-dist.LICENSE'],
  ['swagger-ui-dist/NOTICE', 'swagger-ui-dist.NOTICE'],
  ['redoc/bundles/redoc.standalone.js', 'redoc.standalone.js'],
  ['redoc/bundles/redoc.standalone.js.LICENSE.txt', 'redoc.standalone.js.LICENSE.txt'],
  ['redoc/LICENSE', 'redoc.LICENSE'],
];

function copyDocsAssets(): Plugin {
  return {
    name: 'logshed-copy-docs-assets',
    apply: 'build',
    closeBundle() {
      const target = path.join(outDir, 'docs-assets');
      fs.mkdirSync(target, { recursive: true });
      for (const [from, to] of DOCS_ASSETS) {
        fs.copyFileSync(path.resolve(__dirname, 'node_modules', from), path.join(target, to));
      }
    },
  };
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), copyDocsAssets()],
  build: {
    outDir,
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
