import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// base './'：构建产物相对路径，适配 fts ui 以 /workflow 前缀托管
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: 'dist', assetsDir: 'assets' },
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:9100', changeOrigin: true } }
  }
});
