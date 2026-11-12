import process from 'node:process'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev.sh can select an alternate API port without changing application config.
const backend = `http://127.0.0.1:${process.env.ACA_DEV_API_PORT || '8000'}`

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/jobs':      backend,
      '/mcp':       backend,
      '/auth':      backend,
      '/terraform': backend,
      '/rag':       backend,
      '/tools':     backend,
      '/health':    backend,
      '/ready':     backend,
      '/api/ollama': backend,
      '/docs':      backend,
    },
  },
})
