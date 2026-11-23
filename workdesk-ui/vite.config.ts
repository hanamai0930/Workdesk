import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss(), {
    name: 'workdesk-shared-styles',
    transformIndexHtml: { order: 'post', handler: () => ['style.css','workbench.css','settings.css'].map(href => ({tag:'link',attrs:{rel:'stylesheet',href},injectTo:'head' as const})) },
  }],
  resolve: { alias: { '@': fileURLToPath(new URL('./frontend', import.meta.url)) } },
  build: { outDir: 'dist', emptyOutDir: true, manifest: true,
    rollupOptions: { output: { manualChunks: { assistant: ['@assistant-ui/react'] } } },
  },
});
