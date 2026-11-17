import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(['/api', '/health'].map(path => [path, {
      target: process.env.BACKEND_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true,
      configure(proxy) {
        proxy.on('error', (error, _request, response) => {
          console.error('[backend proxy] Cannot get a response from Django:', (error as NodeJS.ErrnoException).code || error.name);
          if ('writeHead' in response && !response.headersSent) {
            response.writeHead(502, { 'Content-Type': 'application/json' });
            response.end(JSON.stringify({ detail: 'The frontend proxy cannot reach Django or its connection was interrupted. Check the Django terminal and restart the backend on port 8000, then retry.' }));
          }
        });
      },
    }])),
  },
  preview: { port: 4173 },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], include: ['src/**/*.test.{ts,tsx}'] },
});
