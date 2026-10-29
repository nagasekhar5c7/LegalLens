import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

/**
 * The backend the dev server proxies to. Same-origin in development keeps the
 * session cookie and CORS entirely out of the picture — see `src/api/env.ts`.
 */
const BACKEND = process.env.LL_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000'

const proxy = {
  target: BACKEND,
  // The backend's CORS origin list and cookie both key off the browser's host,
  // so the Host header must survive the hop.
  changeOrigin: false,
  // SSE: no buffering, no timeout on an idle stream between keep-alives.
  timeout: 0,
  proxyTimeout: 0,
} as const

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    open: false,
    proxy: {
      '/api': { ...proxy },
      '/healthz': { ...proxy },
      '/readyz': { ...proxy },
    },
  },
})
