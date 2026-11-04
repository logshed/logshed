import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'fs';
import path from 'path';
import zlib from 'zlib';

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

// Brotli and gzip copies of every text file in the build, served by the backend
// (backend/app/core/static_files.py) to browsers that accept them, so nothing is compressed per
// request. Kept only when at least 10% smaller.
const COMPRESSIBLE = /\.(js|mjs|css|html|svg|json)$/;
const MIN_COMPRESS_BYTES = 512;

function* filesUnder(dir: string): Generator<string> {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* filesUnder(full);
    else if (entry.isFile()) yield full;
  }
}

function precompress(): Plugin {
  return {
    name: 'logshed-precompress',
    apply: 'build',
    // After the other closeBundle hooks, so the copied docs assets are compressed too
    closeBundle: {
      order: 'post',
      sequential: true,
      handler() {
        for (const file of filesUnder(outDir)) {
          if (!COMPRESSIBLE.test(file)) continue;
          const source = fs.readFileSync(file);
          if (source.length < MIN_COMPRESS_BYTES) continue;
          const copies: Array<[string, Buffer]> = [
            ['.br', zlib.brotliCompressSync(source, {
              params: {
                [zlib.constants.BROTLI_PARAM_QUALITY]: zlib.constants.BROTLI_MAX_QUALITY,
                [zlib.constants.BROTLI_PARAM_SIZE_HINT]: source.length,
              },
            })],
            ['.gz', zlib.gzipSync(source, { level: zlib.constants.Z_BEST_COMPRESSION })],
          ];
          for (const [suffix, compressed] of copies) {
            if (compressed.length <= source.length * 0.9) fs.writeFileSync(file + suffix, compressed);
          }
        }
      },
    },
  };
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), copyDocsAssets(), precompress()],
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
        client: {
          enabled: true,
          include: ['lucide-react', 'recharts'],
        },
      },
    },
  },
});
