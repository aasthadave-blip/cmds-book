import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// V-Studio dev server runs on 5174 so it does not collide with the
// existing `frontend/` Vite app (which uses 5173).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
  },
  preview: {
    port: 5174,
  },
});
