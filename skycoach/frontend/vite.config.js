import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// SKYCOACH_BASE controls the URL prefix the bundle is served from.
// - Standalone (Vercel, dev server): leave unset → "/"
// - Mounted under PTGO at /skycoach: SKYCOACH_BASE=/skycoach/
const BASE = process.env.SKYCOACH_BASE || '/'

export default defineConfig({
  base: BASE,
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/health': 'http://127.0.0.1:8001',
    },
  },
})
