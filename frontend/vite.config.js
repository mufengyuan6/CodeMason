import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端固定端口 5847（避开常用端口），后端 8765
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5847,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
