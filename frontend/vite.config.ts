import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';

export default defineConfig(({ command }) => ({
  plugins: [vue()],
  base: command === 'serve' ? '/' : '/assets/reception/',
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000' },
    },
  },
  build: {
    outDir: '../src/nexusguard/web/dist',
    // Only generated files live here; the editable sources are under frontend/src.
    emptyOutDir: true,
    sourcemap: false,
  },
}));
