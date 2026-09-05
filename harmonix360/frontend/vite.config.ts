import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
        // Phase 10's realtime channels connect to /api/v1/ws/{channel}. Without
        // this the proxy answers the upgrade request with a 200 and the socket
        // never opens — which the UI reports honestly as "live feed
        // unavailable", so the feature would look implemented and simply never
        // work in development.
        ws: true,
      },
      // Offline reachability check (src/lib/reachability.ts) pings /health
      // directly, outside the /api/v1 prefix — needs its own proxy entry.
      '/health': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
