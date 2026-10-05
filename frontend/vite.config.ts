import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // The collector drives this from a second machine on the LAN, so listen on all interfaces.
    host: true,
    port: 5173,
    /*
     * The dev server reaches the capture service on 8000 through this proxy, so
     * `npm run dev` needs the service running. The dev build and the served build
     * therefore exercise the same client.
     */
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // The FastAPI app serves this directory in production, so keep paths relative to the root.
    assetsDir: 'assets',
  },
})
