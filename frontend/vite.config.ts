import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Прокси к FastAPI в dev: приложение и API живут на одном origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})