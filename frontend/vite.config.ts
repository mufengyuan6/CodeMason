import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import { fileURLToPath, URL } from 'node:url'

// 前端固定端口 5847（避开常用端口），后端 29864（local-port-manager 固定分配）
// proxy 覆盖：/api（REST 通用）+ v1.13 面板端点 + /ws（WebSocket，需 ws:true）
// 修复记录（S19 契约）：/costs /context /health-signals /ws 无代理 → dev 形态面板 404/WS 连不上
export default defineConfig({
  plugins: [
    vue(),
    vueJsx(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    host: '127.0.0.1',
    port: 5847,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:29864',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/costs': {
        target: 'http://127.0.0.1:29864',
        changeOrigin: true,
      },
      '/context': {
        target: 'http://127.0.0.1:29864',
        changeOrigin: true,
      },
      '/health-signals': {
        target: 'http://127.0.0.1:29864',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:29864',
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia'],
        },
      },
    },
  },
})