import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/mcp':       'http://localhost:8000',
      '/terraform': 'http://localhost:8000',
      '/rag':       'http://localhost:8000',
      '/tools':     'http://localhost:8000',
      '/health':    'http://localhost:8000',
      '/docs':      'http://localhost:8000',
    },
  },
})
