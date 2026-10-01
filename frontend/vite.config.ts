import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = 'http://127.0.0.1:7860'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        // The backend's origin guard refuses a state-changing request whose
        // Origin names a different netloc than the Host header. The proxy
        // rewrites Host to the target but forwards the browser's Origin
        // (:5173), so every proxied write would 403. Present the target's
        // origin instead: the request did come from the app's own origin,
        // merely through the dev proxy, and the CSRF double-submit token
        // still carries the real protection. Production is single-origin
        // and never proxies.
        headers: { origin: apiTarget },
      },
    },
  },
})
