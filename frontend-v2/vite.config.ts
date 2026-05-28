import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// V-Studio dev server.
//
// To avoid CORS in local dev, when VITE_DEV_PROXY_TARGET is set we proxy
// /api/* through Vite to the backend. The browser sees same-origin
// (localhost:5175) so no preflight rejection from Railway's CORS allowlist.
//
// In production builds the proxy is irrelevant — Vite inlines VITE_API_BASE
// and the deployed UI hits the backend directly (CORS allowlist on Railway
// must include the deployed UI origin).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxyTarget =
    env.VITE_DEV_PROXY_TARGET || env.VITE_API_BASE ||
    'https://cmds-book-production.up.railway.app';

  return {
    plugins: [react()],
    server: {
      port: 5175,
      strictPort: true,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
          secure: true,
        },
      },
    },
    preview: {
      port: 5175,
    },
  };
});
