// Builds the browser demo (demo.html) into dist-demo/, for demo.logshed.dev. See src/demo/.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'fs';
import path from 'path';

const outDir = path.resolve(import.meta.dirname, 'dist-demo');

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'logshed-demo-index',
      apply: 'build',
      // Serve the demo page as index.html
      closeBundle() {
        fs.renameSync(path.join(outDir, 'demo.html'), path.join(outDir, 'index.html'));
      },
    },
  ],
  build: {
    outDir,
    emptyOutDir: true,
    rolldownOptions: { input: path.resolve(import.meta.dirname, 'demo.html') },
  },
});
