import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/ask': 'http://localhost:8000',
      '/datasets': 'http://localhost:8000',
      '/switch-dataset': 'http://localhost:8000',
      '/eval-summary': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/audit': 'http://localhost:8000',
    }
  }
})
